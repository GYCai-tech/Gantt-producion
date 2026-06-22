import os
import heapq
import httpx
from collections import defaultdict
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text
from datetime import datetime, timedelta
from typing import Optional
from app.db import get_engine

router = APIRouter(prefix="/api")

JORNADA_INICIO = 7
JORNADA_FIN    = 16
_SEGMENTOS = [(7 * 60, 11 * 60), (11 * 60 + 15, 16 * 60)]


def _next_workday_start(dt: datetime) -> datetime:
    nxt = (dt + timedelta(days=1)).replace(hour=JORNADA_INICIO, minute=0, second=0, microsecond=0)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def _base_programadas(ahora: datetime) -> datetime:
    """Punto de inicio para la cola de programadas.
    Si ahora está fuera de jornada o en fin de semana, devuelve el inicio
    del siguiente día laborable para que las barras sean visibles en el Gantt."""
    if ahora.weekday() >= 5:
        return _next_workday_start(ahora)
    t = ahora.hour + ahora.minute / 60
    if t < JORNADA_INICIO:
        return ahora.replace(hour=JORNADA_INICIO, minute=0, second=0, microsecond=0)
    if t >= JORNADA_FIN:
        return _next_workday_start(ahora)
    return ahora


def add_work_minutes(start: datetime, minutes: float) -> datetime:
    if minutes <= 0:
        return start
    remaining = float(minutes)
    day = start
    for _ in range(400):
        if day.weekday() >= 5:
            day = _next_workday_start(day); continue
        midnight = day.replace(hour=0, minute=0, second=0, microsecond=0)
        cm = (day - midnight).total_seconds() / 60
        for a, b in _SEGMENTOS:
            if cm >= b:
                continue
            seg_start = max(cm, a)
            avail = b - seg_start
            if remaining <= avail:
                return midnight + timedelta(minutes=seg_start + remaining)
            remaining -= avail
        day = _next_workday_start(day)
    return day


def _prev_fiable(fecha_prevista, fecha_orden) -> bool:
    """Devuelve False si fecha_prevista es igual a fecha_orden (relleno automático del ERP)."""
    if fecha_prevista is None or fecha_orden is None:
        return False
    fp = fecha_prevista.replace(tzinfo=None) if hasattr(fecha_prevista, 'tzinfo') and fecha_prevista.tzinfo else fecha_prevista
    fo = fecha_orden.replace(tzinfo=None) if hasattr(fecha_orden, 'tzinfo') and fecha_orden.tzinfo else fecha_orden
    return abs((fp - fo).total_seconds()) >= 86400


def _estado_programado(fecha_prevista_fin, fecha_orden=None) -> str:
    if fecha_prevista_fin is None or not _prev_fiable(fecha_prevista_fin, fecha_orden):
        return 'sin-estimar'
    return 'retrasada' if fecha_prevista_fin < datetime.now() else 'plazo'


def _min_est_neto(r, cap_min=None):
    """Duración neta restante de un bono programado (estimado - ya trabajado)."""
    min_est = float(r.get('min_estimados') or 0) - float(r.get('minutos_reales') or 0)
    if cap_min:
        min_est = min(min_est, cap_min)
    return min_est


def _prioridad_programado(r):
    fp = r.get('fecha_prevista_fin')
    return fp if _prev_fiable(fp, r.get('fecha_orden')) else datetime.max


def _planificar_programados(rows, deps, bono_fin, next_start, ahora):
    """Calcula inicio/fin de cada bono programado respetando a la vez la cola de
    su recurso y sus dependencias bono->bono (core.dependencias_bono).

    Es *list scheduling* con cola de prioridad: un nodo solo se considera "listo"
    cuando todos sus requeridos (los que también están en `rows`) ya se han
    planificado; entre los listos se elige primero el de fecha_prevista_fin más
    temprana. A diferencia de iterar un número fijo de pasadas, esto converge
    siempre en una sola pasada sea cual sea la profundidad de la cadena, y como
    `bono_fin`/`next_start` los pasa quien llama, funciona igual si el bono
    requerido vive en otro recurso o en otra vista (máquina/empleado) por
    completo — basta con que quien llama meta ahí TODOS los recursos a la vez.

    Un mismo bono puede estar asignado a VARIOS recursos a la vez (ej. un bono
    repartido entre 2-3 operarios); cada asignación es un nodo independiente
    `(idorden, idbono, recurso_key)` con su propia cola, pero un dependiente no
    se considera "listo" hasta que TODAS las asignaciones de cada requerido
    terminan — por eso `bono_fin` (a nivel de bono, sin recurso) guarda el fin
    más tardío entre las asignaciones de ese bono.

    `rows`: dicts con idorden, idbono, recurso_key (string namespaced para no
    chocar máquinas con empleados) y min_est (duración neta en minutos) ya
    calculados. Devuelve {(idorden, idbono, recurso_key): (start, end)}.
    """
    by_node = {}
    nodos_de_bono = defaultdict(list)   # (idorden, idbono) -> [nodo, ...] (puede haber varios recursos)
    for r in rows:
        if r['min_est'] <= 0:
            continue
        bono_key = (r['idorden'], r['idbono'])
        node = bono_key + (r['recurso_key'],)
        by_node[node] = r
        nodos_de_bono[bono_key].append(node)

    dependientes = defaultdict(list)   # nodo_requerido -> [nodo_dependiente, ...]
    pendientes = {}
    for node, r in by_node.items():
        bono_key = (r['idorden'], r['idbono'])
        req_nodos = [n for req in deps.get(bono_key, []) for n in nodos_de_bono.get(req, [])]
        pendientes[node] = len(req_nodos)
        for rn in req_nodos:
            dependientes[rn].append(node)

    heap = [(_prioridad_programado(r), node) for node, r in by_node.items() if pendientes[node] == 0]
    heapq.heapify(heap)

    def _planificar_nodo(node, r):
        bono_key = (r['idorden'], r['idbono'])
        rk = r['recurso_key']
        start = next_start[rk]
        for req in deps.get(bono_key, []):
            req_fin = bono_fin.get(req)
            if req_fin and req_fin > start:
                start = req_fin
        # Colchón de seguridad: un "programado" nunca puede arrancar en
        # "ahora" mismo o antes -- si el recurso está libre, start cae
        # exactamente en `ahora`/`base_prog` sin margen, así que en cuanto el
        # navegador tarda unos minutos en cargar/refrescar el Gantt, ese
        # inicio ya quedó "en el pasado" y la barra parece estar ya
        # trabajándose (o haberlo sido) cuando es pura cola sin empezar.
        # Mismo colchón que usa _estimar_fin para los bonos activos.
        start = max(start, ahora + timedelta(minutes=10))
        end = add_work_minutes(start, r['min_est'])
        next_start[rk] = end
        if bono_fin.get(bono_key) is None or end > bono_fin[bono_key]:
            bono_fin[bono_key] = end
        return start, end

    computed = {}
    while heap:
        _, node = heapq.heappop(heap)
        r = by_node[node]
        computed[node] = _planificar_nodo(node, r)
        for dep_node in dependientes.get(node, []):
            pendientes[dep_node] -= 1
            if pendientes[dep_node] == 0:
                heapq.heappush(heap, (_prioridad_programado(by_node[dep_node]), dep_node))

    # Ciclo en los datos (no debería pasar: core.dependencias_bono es un DAG real
    # sobre órdenes activas) — planifica lo que falte sin más restricciones para
    # no perder bonos del Gantt.
    for node, r in by_node.items():
        if node not in computed:
            computed[node] = _planificar_nodo(node, r)

    return computed


def _render_parcial(r, recurso_id, id_prefix, inicio, fin):
    """Sesión de trabajo ya realizada (fichaje cerrado) en un bono que SIGUE
    abierto (estado_bono=1, sin fichaje activo ahora) — ej. el operario fichó
    salida para el descanso/cambio de turno pero el bono no está terminado.
    Se pinta además de, no en vez de, la barra 'programado' que continúa la
    cola; sin esto esas horas ya trabajadas se volvían invisibles."""
    return {
        "id":           f"{id_prefix}_{recurso_id}_{r['idorden']}_{r['idbono']}",
        "idorden":      str(r['idorden']),
        "idbono":       r['idbono'],
        "recurso_id":   recurso_id,
        "tipo":         "parcial",
        "en_curso":     False,
        "estado":       "parcial",
        "estado_label": "Pausado",
        "situacion":    str(r.get('situacion', 'ACTIVADO')),
        "art":          r['articulo'],
        "operacion":    r['operacion'],
        "cantidad":     r.get('cantidad_pedida') or r.get('cantidad'),
        "prev":         None,
        "start":        inicio.isoformat(),
        "end":          fin.isoformat(),
        "estimado":     False,
        "progreso":     None,
        "operarios":    r.get('operarios'),
        "notas":        None,
        "min_real":     float(r['minutos_reales']) if r.get('minutos_reales') is not None else None,
    }


def _render_programado(r, recurso_id, id_prefix, start, end):
    """Bono en cola, sin fichaje real todavia. La etiqueta es siempre
    'Programado' -- no se distingue por fiabilidad de fecha_prevista_fin
    (ver _prev_fiable: casi todas vienen rellenadas/vacias del ERP, asi que
    matizar "sin fecha"/"retrasada" aqui solo confundia, dando la impresion
    de que el bono ya estaba en marcha cuando es pura proyeccion de cola)."""
    prev   = r['fecha_prevista_fin']
    fiable = _prev_fiable(prev, r.get('fecha_orden'))
    if r.get('estado_bono') == 3:
        estado, estado_label = "parada", "Bloqueado"
    else:
        estado, estado_label = "programado", "Programado"
    return {
        "id":           f"{id_prefix}_{recurso_id}_{r['idorden']}_{r['idbono']}",
        "idorden":      str(r['idorden']),
        "idbono":       r['idbono'],
        "recurso_id":   recurso_id,
        "tipo":         "programado",
        "en_curso":     False,
        "estado":       estado,
        "estado_label": estado_label,
        "situacion":    str(r.get('situacion', 'PENDIENTE')),
        "art":          r['articulo'],
        "operacion":    r['operacion'],
        "cantidad":     r.get('cantidad_pedida') or r.get('cantidad'),
        "prev":         prev.isoformat() if fiable else None,
        "start":        start.isoformat(),
        "end":          end.isoformat(),
        "estimado":     True,
        "progreso":     None,
        "operarios":    r.get('operarios'),
        "notas":        None,
    }


def _cargar_sesiones_fichaje(conn, recurso_col, desde, hasta, ahora):
    """Sesiones reales de fichaje (core.fact_fichajes), una fila por sesión
    (idempleado/máquina + idorden + idbono + idlinea + idnum).

    hinicial/hfinal son timestamptz con la hora local del ERP guardada
    deliberadamente "disfrazada" de UTC (ver memoria gotcha-fact-fichajes-tz);
    hay que recuperarla con AT TIME ZONE 'UTC' o todo sale desplazado +2h.
    """
    col = {'idempleado': 'f.idempleado', 'matricula_maquina': 'f.matricula_maquina'}[recurso_col]
    return conn.execute(text(f"""
        SELECT
            {col} AS recurso, f.idorden, f.idbono, f.idlinea, f.idnum,
            f.hinicial AT TIME ZONE 'UTC' AS inicio,
            f.hfinal   AT TIME ZONE 'UTC' AS fin,
            f.operacion, f.minutos_trabajados,
            fb.estado_bono, fb.cantidad_pedida, da.descrip AS articulo
        FROM core.fact_fichajes f
        LEFT JOIN core.fact_bonos fb  ON fb.idorden = f.idorden AND fb.idbono = f.idbono
        LEFT JOIN core.dim_articulo da ON da.idarticulo = fb.idarticulo
        WHERE {col} IS NOT NULL
          AND COALESCE(f.hfinal AT TIME ZONE 'UTC', :ahora) > :desde
          AND f.hinicial AT TIME ZONE 'UTC' < :hasta
        ORDER BY {col}, f.idorden, f.idbono, f.hinicial
    """), {"ahora": ahora, "desde": desde, "hasta": hasta}).mappings().all()


def _fusionar_sesiones(rows):
    """Funde sesiones de fichaje consecutivas del mismo bono (sin hueco real
    entre ellas: el fin de una coincide con el inicio de la siguiente) en un
    solo tramo visual, para no llenar el Gantt de astillas de 1 minuto.
    Devuelve un tramo por grupo contiguo, con la sesión todavía abierta (si la
    hay) excluida — esa la cubre ya la barra "real"."""
    grupos = defaultdict(list)
    for r in rows:
        grupos[(r['recurso'], r['idorden'], r['idbono'])].append(r)

    segmentos = []
    for ses in grupos.values():
        ses = sorted(ses, key=lambda r: r['inicio'])
        actual = None
        for r in ses:
            if actual is not None and not actual['abierto'] and r['inicio'] <= actual['fin']:
                actual['fin'] = r['fin']
                actual['abierto'] = r['fin'] is None
                actual['min_total'] += float(r['minutos_trabajados'] or 0)
            else:
                if actual is not None:
                    segmentos.append(actual)
                actual = {
                    'recurso': r['recurso'], 'idorden': r['idorden'], 'idbono': r['idbono'],
                    'idlinea': r['idlinea'], 'idnum': r['idnum'],
                    'inicio': r['inicio'], 'fin': r['fin'], 'abierto': r['fin'] is None,
                    'operacion': r['operacion'], 'articulo': r['articulo'],
                    'cantidad_pedida': r['cantidad_pedida'], 'estado_bono': r['estado_bono'],
                    'min_total': float(r['minutos_trabajados'] or 0),
                }
        if actual is not None:
            segmentos.append(actual)
    return segmentos


def _render_sesion(seg, id_prefix):
    tipo = "trabajado" if seg['estado_bono'] == 2 else "parcial"
    return {
        "id":           f"{id_prefix}_{seg['recurso']}_{seg['idorden']}_{seg['idbono']}_{seg['idlinea']}_{seg['idnum']}",
        "idorden":      str(seg['idorden']),
        "idbono":       seg['idbono'],
        "recurso_id":   str(seg['recurso']),
        "tipo":         tipo,
        "en_curso":     False,
        "estado":       "completado" if tipo == "trabajado" else "parcial",
        "estado_label": "Completado" if tipo == "trabajado" else "Pausado",
        "situacion":    "COMPLETADO" if tipo == "trabajado" else "ACTIVADO",
        "art":          seg['articulo'],
        "operacion":    seg['operacion'],
        "cantidad":     seg['cantidad_pedida'],
        "prev":         None,
        "start":        seg['inicio'].isoformat(),
        "end":          seg['fin'].isoformat(),
        "estimado":     False,
        "progreso":     None,
        "operarios":    None,
        "notas":        None,
        "min_real":     round(seg['min_total'], 1),
    }


def _cargar_dependencias(conn):
    """deps: (idorden, idbono_dependiente) -> [(idorden, idbono_requerido), ...]"""
    rows = conn.execute(text("""
        SELECT idorden, idbono_dependiente, idbono_requerido FROM core.dependencias_bono
    """)).mappings().all()
    deps = defaultdict(list)
    for r in rows:
        deps[(r['idorden'], r['idbono_dependiente'])].append((r['idorden'], r['idbono_requerido']))
    return deps


def _estimar_fin(inicio: datetime, min_est: float, min_real: float, ahora: datetime) -> datetime:
    """Proyecta el fin de un bono en curso. Sin estimación histórica (min_est<=0) no
    inventamos un final de jornada: la barra simplemente avanza con el reloj mientras
    el bono siga abierto (igual que el resto, con el colchón mínimo de 10 min)."""
    base = max(inicio, ahora)
    if min_est > 0:
        min_rest = max(min_est - min_real, 0)
        fin = add_work_minutes(base, min_rest if min_rest > 0 else 10)
    else:
        fin = ahora + timedelta(minutes=10)
    return max(fin, ahora + timedelta(minutes=10))


# ─────────────────────────────────────────────────────────────────────
#  GRUPOS  (filas del Gantt)
# ─────────────────────────────────────────────────────────────────────

@router.get("/grupos")
def get_grupos(vista: str = Query("empleado", pattern="^(maquina|empleado)$")):
    engine = get_engine()
    with engine.connect() as conn:
        if vista == 'maquina':
            rows = conn.execute(text("""
                SELECT
                    dm.matricula::text                    AS id,
                    dm.descrip                            AS nombre,
                    COALESCE(dm.tipo, 'Sin tipo')         AS sub,
                    COALESCE(dm.area, 'Sin área')         AS area
                FROM core.dim_maquinas dm
                ORDER BY dm.descrip
            """)).mappings().all()
        else:
            # Se agrupa por el área de la(s) máquina(s) en las que trabaja el
            # operario, no por su departamento del ERP. "En las que trabaja":
            # las de sus fichajes activos ahora mismo (puede tener varios a la
            # vez, ej. atendiendo 2-3 máquinas en paralelo); si no tiene ninguno
            # activo, la de su fichaje más reciente. El filtro de "activo" exige
            # además que el fichaje sea de las últimas 24h: fichajes abiertos
            # de hace meses/años son fichajes fantasma del ERP (ver memoria de
            # gotchas), no actividad real -- sin ese corte, ensucian el área.
            rows = conn.execute(text("""
                SELECT
                    de.idempleado::text                       AS id,
                    de.nombre_completo                        AS nombre,
                    COALESCE(maq.sub, 'Sin máquina')          AS sub,
                    COALESCE(maq.areas, ARRAY['Sin máquina']) AS areas
                FROM core.dim_empleados de
                LEFT JOIN LATERAL (
                    SELECT
                        array_to_string(array_agg(DISTINCT dm.area ORDER BY dm.area), ', ') AS sub,
                        array_agg(DISTINCT dm.area) AS areas
                    FROM core.dim_maquinas dm
                    WHERE dm.area IS NOT NULL
                      AND dm.matricula IN (
                          SELECT f.matricula_maquina
                          FROM core.fact_fichajes f
                          WHERE f.idempleado = de.idempleado
                            AND f.hfinal IS NULL
                            AND f.hinicial > NOW() - INTERVAL '1 day'

                          UNION

                          SELECT ultimo.matricula_maquina FROM (
                              SELECT f.matricula_maquina
                              FROM core.fact_fichajes f
                              WHERE f.idempleado = de.idempleado
                                AND NOT EXISTS (
                                    SELECT 1 FROM core.fact_fichajes fa
                                    WHERE fa.idempleado = de.idempleado
                                      AND fa.hfinal IS NULL
                                      AND fa.hinicial > NOW() - INTERVAL '1 day'
                                )
                              ORDER BY f.hinicial DESC
                              LIMIT 1
                          ) ultimo
                      )
                ) maq ON true
                WHERE (de.fechabaja IS NULL OR de.fechabaja > CURRENT_DATE)
                  AND (
                      LOWER(de.departamento) IN ('producción','produccion','logística','logistica')
                      OR de.idempleado IN (
                          SELECT DISTINCT idempleado FROM analytics.v_asignaciones_empleado
                          WHERE fase = 'EN_CURSO'
                      )
                  )
                ORDER BY de.nombre_completo
            """)).mappings().all()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────
#  ITEMS  (barras del Gantt)
# ─────────────────────────────────────────────────────────────────────

@router.get("/items")
def get_items(
    vista: str = Query("empleado", pattern="^(maquina|empleado)$"),
    desde: Optional[datetime] = None,
    hasta: Optional[datetime] = None,
):
    if desde is None:
        desde = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=7)
    if hasta is None:
        hasta = datetime.now() + timedelta(hours=1)
    desde, hasta = desde.replace(tzinfo=None), hasta.replace(tzinfo=None)
    ahora = datetime.now()
    base_prog = _base_programadas(ahora)

    # `bono_fin`/`next_start` se comparten entre máquina Y empleado, namespaced
    # por prefijo ("maq:"/"emp:"), para que una dependencia entre vistas (un
    # montaje de operario que espera una pieza de máquina, o al revés) se
    # resuelva aunque el Gantt sólo pinte una vista a la vez. Por eso ambos
    # bloques de queries se ejecutan siempre, no sólo el de `vista`.
    bono_fin = {}
    next_start = defaultdict(lambda: base_prog)
    MAX_MAQ_MIN = 10 * 9 * 60   # 10 días laborables → tope visual del Gantt

    engine = get_engine()
    with engine.connect() as conn:
        deps = _cargar_dependencias(conn)

        # ════════════════════════════ MÁQUINA ════════════════════════════
        # ── Máquinas en curso ──────────────────────────────────────
        activos = conn.execute(text("""
            WITH hist_art_op AS (
                SELECT idarticulo::text, LOWER(operacion) AS operacion,
                       AVG(min_reales / NULLIF(cantidad_objetivo, 0)) AS mpp
                FROM core.fact_bonos
                WHERE estado_orden = 2
                  AND cantidad_objetivo > 0 AND min_reales > 0
                GROUP BY idarticulo, LOWER(operacion)
            ),
            hist_op AS (
                SELECT LOWER(operacion) AS operacion,
                       AVG(min_reales / NULLIF(cantidad_objetivo, 0)) AS mpp
                FROM core.fact_bonos
                WHERE estado_orden = 2
                  AND cantidad_objetivo > 0 AND min_reales > 0
                GROUP BY LOWER(operacion)
            ),
            op_bono AS (   -- operario(s) fichados ahora mismo en cada bono
                SELECT idorden, idbono,
                       string_agg(DISTINCT nombre_empleado, ', ' ORDER BY nombre_empleado) AS operarios
                FROM analytics.v_asignaciones_empleado
                WHERE fase = 'EN_CURSO'
                GROUP BY idorden, idbono
            )
            SELECT
                m.matricula, m.maquina, m.idorden, m.idbono, m.operacion, m.articulo,
                m.situacion, m.cantidad_pedida, m.fecha_prevista_fin, m.fecha_orden,
                m.minutos_reales,
                COALESCE(m.fichaje_activo_desde, m.fecha_asignacion) AS inicio,
                ROUND(COALESCE(hao.mpp, ho.mpp) * NULLIF(m.cantidad_objetivo, 0)) AS min_estimados,
                op_bono.operarios
            FROM core.fact_asignaciones_maquina m
            LEFT JOIN hist_art_op hao ON hao.idarticulo = m.idarticulo AND hao.operacion = LOWER(m.operacion)
            LEFT JOIN hist_op     ho  ON ho.operacion = LOWER(m.operacion)
            LEFT JOIN op_bono         ON op_bono.idorden = m.idorden AND op_bono.idbono = m.idbono
            JOIN core.fact_bonos fb ON fb.idorden = m.idorden AND fb.idbono = m.idbono
            WHERE fb.estado_bono = 1
              AND m.situacion NOT IN ('COMPLETADO', 'ANULADO')
              AND m.fichaje_activo_desde IS NOT NULL
            ORDER BY m.matricula, m.fichaje_activo_desde DESC
        """)).mappings().all()

        maquina_items = []
        for r in activos:
            inicio   = r["inicio"] or ahora
            min_est  = float(r["min_estimados"] or 0)
            min_real = float(r["minutos_reales"] or 0)
            fin      = _estimar_fin(inicio, min(min_est, min_real + MAX_MAQ_MIN), min_real, ahora)
            bk = (r["idorden"], r["idbono"])
            if bono_fin.get(bk) is None or fin > bono_fin[bk]:
                bono_fin[bk] = fin
            rk = f"maq:{r['matricula']}"
            if fin > next_start[rk]:
                next_start[rk] = fin
            maquina_items.append({
                "id":           f"maq_real_{r['matricula']}_{r['idorden']}_{r['idbono']}",
                "idorden":      str(r["idorden"]),
                "idbono":       r["idbono"],
                "recurso_id":   str(r["matricula"]),
                "tipo":         "real",
                "en_curso":     True,
                "estado":       "plazo",
                "estado_label": "En curso",
                "situacion":    "EN_CURSO",
                "art":          r["articulo"],
                "operacion":    r["operacion"],
                "cantidad":     r["cantidad_pedida"],
                "prev":         r["fecha_prevista_fin"].isoformat() if _prev_fiable(r["fecha_prevista_fin"], r["fecha_orden"]) else None,
                "start":        inicio.isoformat(),
                "end":          fin.isoformat(),
                "estimado":     True,
                "progreso":     round(min(min_real / min_est * 100, 100)) if min_est > 0 else None,
                "operarios":    r["operarios"],
                "notas":        None,
            })

        # ── Máquinas completadas (dentro de la ventana) ────────────
        completados = conn.execute(text("""
            WITH op_bono AS (   -- operario(s) que trabajaron cada bono ya finalizado
                SELECT idorden, idbono,
                       string_agg(DISTINCT nombre_empleado, ', ' ORDER BY nombre_empleado) AS operarios
                FROM analytics.v_asignaciones_empleado
                WHERE fase = 'TRABAJADO'
                GROUP BY idorden, idbono
            )
            SELECT
                m.matricula, m.maquina, m.idorden, m.idbono, m.operacion, m.articulo,
                m.cantidad_pedida, m.fecha_prevista_fin, m.fecha_orden, m.minutos_reales,
                m.piezas_producidas, m.fecha_asignacion,
                op_bono.operarios
            FROM core.fact_asignaciones_maquina m
            LEFT JOIN op_bono ON op_bono.idorden = m.idorden AND op_bono.idbono = m.idbono
            WHERE m.situacion = 'COMPLETADO'
              AND m.fecha_asignacion IS NOT NULL
              AND m.fecha_asignacion < :hasta
              AND m.fecha_asignacion > :desde_aprox
        """), {"hasta": hasta, "desde_aprox": desde - timedelta(days=1)}).mappings().all()

        for r in completados:
            inicio   = r["fecha_asignacion"]
            min_real = float(r["minutos_reales"] or 0)
            fin      = add_work_minutes(inicio, min_real) if min_real > 0 else inicio + timedelta(minutes=15)
            if fin <= desde:
                continue
            maquina_items.append({
                "id":           f"maq_trab_{r['matricula']}_{r['idorden']}_{r['idbono']}",
                "idorden":      str(r["idorden"]),
                "idbono":       r["idbono"],
                "recurso_id":   str(r["matricula"]),
                "tipo":         "trabajado",
                "estado":       "completado",
                "estado_label": "Completado",
                "situacion":    "COMPLETADO",
                "art":          r["articulo"],
                "operacion":    r["operacion"],
                "cantidad":     r["cantidad_pedida"],
                "prev":         r["fecha_prevista_fin"].isoformat() if _prev_fiable(r["fecha_prevista_fin"], r["fecha_orden"]) else None,
                "start":        inicio.isoformat(),
                "end":          fin.isoformat(),
                "estimado":     False,
                "progreso":     None,
                "operarios":    r["operarios"],
                "notas":        None,
                "min_real":     float(r["minutos_reales"]) if r["minutos_reales"] is not None else None,
                "piezas":       float(r["piezas_producidas"]) if r["piezas_producidas"] is not None else None,
            })

        # ── Máquinas programadas: candidatas a la cola del scheduler ──
        prog_maq = conn.execute(text("""
            WITH hist_art_op AS (
                SELECT idarticulo::text, LOWER(operacion) AS operacion,
                       AVG(min_reales / NULLIF(cantidad_objetivo, 0)) AS mpp
                FROM core.fact_bonos
                WHERE estado_orden = 2
                  AND cantidad_objetivo > 0 AND min_reales > 0
                GROUP BY idarticulo, LOWER(operacion)
            ),
            hist_op AS (
                SELECT LOWER(operacion) AS operacion,
                       AVG(min_reales / NULLIF(cantidad_objetivo, 0)) AS mpp
                FROM core.fact_bonos
                WHERE estado_orden = 2
                  AND cantidad_objetivo > 0 AND min_reales > 0
                GROUP BY LOWER(operacion)
            ),
            op_bono AS (   -- operario(s) preasignado(s) o que dejaron pausado el bono en cola
                SELECT idorden, idbono,
                       string_agg(DISTINCT nombre_empleado, ', ' ORDER BY nombre_empleado) AS operarios
                FROM analytics.v_asignaciones_empleado
                WHERE fase IN ('PROGRAMADO', 'EN_CURSO')
                GROUP BY idorden, idbono
            )
            SELECT
                m.matricula AS recurso, m.idorden, m.idbono, m.operacion, m.articulo,
                m.cantidad_pedida, m.fecha_prevista_fin, m.fecha_orden, m.situacion, m.estado_bono,
                m.minutos_reales, m.fecha_asignacion,
                ROUND(COALESCE(hao.mpp, ho.mpp) * NULLIF(m.cantidad_objetivo, 0)) AS min_estimados,
                op_bono.operarios
            FROM core.fact_asignaciones_maquina m
            LEFT JOIN hist_art_op hao ON hao.idarticulo = m.idarticulo AND hao.operacion = LOWER(m.operacion)
            LEFT JOIN hist_op     ho  ON ho.operacion = LOWER(m.operacion)
            LEFT JOIN op_bono         ON op_bono.idorden = m.idorden AND op_bono.idbono = m.idbono
            WHERE (
                    m.estado_bono IN (0, 3)
                    OR (m.estado_bono = 1 AND m.fichaje_activo_desde IS NULL)  -- pausado: abierto pero sin nadie fichado
                  )
              AND m.estado_orden <> 2
            ORDER BY m.matricula, m.fecha_prevista_fin NULLS LAST
        """)).mappings().all()

        sched_rows = []
        for r in prog_maq:
            d = dict(r)
            d['recurso_key'] = f"maq:{r['recurso']}"
            d['min_est'] = _min_est_neto(d, cap_min=MAX_MAQ_MIN)
            sched_rows.append(d)

        # ════════════════════════════ EMPLEADO ════════════════════════════
        # ── Empleados: bonos en curso ──────────────────────────────────
        activos = conn.execute(text("""
            SELECT
                e.idempleado, e.idorden, e.idbono, e.operacion, e.articulo,
                e.situacion, e.cantidad_pedida, e.fecha_prevista_fin, e.fecha_orden,
                e.min_estimados, e.minutos_reales,
                COALESCE(e.fichaje_activo_desde, e.fecha_asignacion) AS inicio
            FROM analytics.v_asignaciones_empleado e
            JOIN core.fact_bonos fb ON fb.idorden = e.idorden AND fb.idbono = e.idbono
            WHERE fb.estado_bono = 1
              AND e.situacion NOT IN ('COMPLETADO', 'ANULADO')
              AND e.fichaje_activo_desde IS NOT NULL
        """)).mappings().all()

        empleado_items = []
        for r in activos:
            inicio   = r["inicio"] or ahora
            min_est  = float(r["min_estimados"] or 0)
            min_real = float(r["minutos_reales"] or 0)
            fin      = _estimar_fin(inicio, min_est, min_real, ahora)
            bk = (r["idorden"], r["idbono"])
            if bono_fin.get(bk) is None or fin > bono_fin[bk]:
                bono_fin[bk] = fin
            rk = f"emp:{r['idempleado']}"
            if fin > next_start[rk]:
                next_start[rk] = fin
            empleado_items.append({
                "id":           f"real_{r['idempleado']}_{r['idorden']}_{r['idbono']}",
                "idorden":      str(r["idorden"]),
                "idbono":       r["idbono"],
                "recurso_id":   str(r["idempleado"]),
                "tipo":         "real",
                "en_curso":     True,
                "estado":       "plazo",
                "estado_label": "En curso",
                "situacion":    str(r["situacion"]),
                "art":          r["articulo"],
                "operacion":    r["operacion"],
                "cantidad":     r["cantidad_pedida"],
                "prev":         r["fecha_prevista_fin"].isoformat() if _prev_fiable(r["fecha_prevista_fin"], r["fecha_orden"]) else None,
                "start":        inicio.isoformat(),
                "end":          fin.isoformat(),
                "estimado":     True,
                "progreso":     round(min(min_real / min_est * 100, 100)) if min_est > 0 else None,
                "operarios":    None,
                "notas":        None,
            })

        # ── Empleados: sesiones reales de fichaje (trabajado/parcial) ──────
        # Una barra por sesión real (hinicial→hfinal de core.fact_fichajes,
        # fusionando tramos contiguos), no un agregado: así se ve exactamente
        # cuándo abrió y cerró cada fichaje, incluso si el bono sigue abierto
        # (esas quedan como "parcial", igual que antes, pero ahora con la
        # granularidad real en vez de un único tramo inicio→fin agregado).
        sesiones_emp = _fusionar_sesiones(
            _cargar_sesiones_fichaje(conn, 'idempleado', desde, hasta, ahora)
        )
        for seg in sesiones_emp:
            if seg['abierto']:
                continue  # la sesión todavía abierta ya la cubre la barra "real"
            if seg['fin'] <= desde or seg['inicio'] >= hasta:
                continue
            empleado_items.append(_render_sesion(seg, 'emp_ses'))

        # ── Empleados programados: candidatas a la cola del scheduler ──
        prog_emp = conn.execute(text("""
            SELECT
                e.idempleado, e.idorden, e.idbono, e.operacion, e.articulo,
                e.cantidad_pedida, e.fecha_prevista_fin, e.fecha_orden, e.min_estimados, e.situacion, e.estado_bono,
                e.minutos_reales, e.fecha_inicio_real, e.fecha_fin_real
            FROM analytics.v_asignaciones_empleado e
            WHERE (
                    e.estado_bono IN (0, 3)
                    OR (e.estado_bono = 1 AND e.fichaje_activo_desde IS NULL)  -- pausado: abierto pero sin nadie fichado
                  )
              AND e.estado_orden <> 2
              AND e.min_estimados > 0
            ORDER BY e.idempleado, e.fecha_prevista_fin NULLS LAST
        """)).mappings().all()

        for r in prog_emp:
            d = dict(r)
            d['recurso_key'] = f"emp:{r['idempleado']}"
            d['min_est'] = _min_est_neto(d)
            sched_rows.append(d)

        # ── Una sola pasada del scheduler para TODOS los recursos a la vez ──
        computed = _planificar_programados(sched_rows, deps, bono_fin, next_start, ahora)

    if vista == 'maquina':
        result = list(maquina_items)
        for r in prog_maq:
            key = (r['idorden'], r['idbono'], f"maq:{r['recurso']}")
            if key in computed:
                start, end = computed[key]
                result.append(_render_programado(r, str(r['recurso']), 'maq_prog', start, end))
            if r.get('estado_bono') == 1 and r.get('fecha_asignacion') and float(r.get('minutos_reales') or 0) > 0:
                # No hay fecha_inicio_real/fin_real en máquina: se aproxima con
                # fecha_asignacion + minutos_reales, igual que el bloque "trabajado".
                inicio = r['fecha_asignacion']
                fin = add_work_minutes(inicio, float(r['minutos_reales']))
                result.append(_render_parcial(r, str(r['recurso']), 'maq_parcial', inicio, fin))
        return result

    result = list(empleado_items)
    for r in prog_emp:
        key = (r['idorden'], r['idbono'], f"emp:{r['idempleado']}")
        if key in computed:
            start, end = computed[key]
            result.append(_render_programado(r, str(r['idempleado']), 'emp_prog', start, end))
    return result


# ─────────────────────────────────────────────────────────────────────
#  HISTÓRICO DE PRODUCCIÓN  (página /historico-produccion)
# ─────────────────────────────────────────────────────────────────────

@router.get("/recursos")
def get_recursos(tipo: str = Query("empleado", pattern="^empleado$")):
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT
                de.idempleado::text                    AS id,
                de.nombre_completo                     AS nombre,
                COALESCE(de.departamento, 'Sin depto') AS grupo
            FROM core.dim_empleados de
            ORDER BY de.nombre_completo
        """)).mappings().all()
    return [dict(r) for r in rows]


@router.get("/historico/bonos")
def get_historico_bonos(idempleado: int, desde: str, hasta: str):
    hasta_excl = datetime.strptime(hasta, "%Y-%m-%d") + timedelta(days=1)
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT
                f.idorden, f.idbono, fb.idarticulo,
                da.descrip AS articulo,
                (array_agg(f.operacion ORDER BY f.hinicial DESC))[1]         AS operacion,
                (array_agg(f.matricula_maquina ORDER BY f.hinicial DESC))[1] AS matricula_maquina,
                SUM(f.minutos_trabajados)                  AS minutos_trabajados,
                MIN(f.hinicial AT TIME ZONE 'UTC')::date   AS primera_fecha,
                fb.cantidad_pedida, fo.fecha_prevista_fin
            FROM core.fact_fichajes f
            LEFT JOIN core.fact_bonos fb   ON fb.idorden = f.idorden AND fb.idbono = f.idbono
            LEFT JOIN core.dim_articulo da ON da.idarticulo = fb.idarticulo
            LEFT JOIN core.fact_ordenes fo ON fo.idorden = f.idorden
            WHERE f.idempleado = :idempleado
              AND f.hinicial AT TIME ZONE 'UTC' >= :desde
              AND f.hinicial AT TIME ZONE 'UTC' < :hasta_excl
            GROUP BY f.idorden, f.idbono, fb.idarticulo, da.descrip, fb.cantidad_pedida, fo.fecha_prevista_fin
            ORDER BY primera_fecha DESC
        """), {"idempleado": idempleado, "desde": desde, "hasta_excl": hasta_excl}).mappings().all()
    return [dict(r) for r in rows]


@router.get("/historico/actividad-diaria")
def get_historico_actividad_diaria(idempleado: int, desde: str, hasta: str):
    hasta_excl = datetime.strptime(hasta, "%Y-%m-%d") + timedelta(days=1)
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT
                (f.hinicial AT TIME ZONE 'UTC')::date AS fecha,
                SUM(f.minutos_trabajados)             AS minutos_trabajados,
                COUNT(DISTINCT f.idorden)              AS num_ordenes,
                COUNT(DISTINCT (f.idorden, f.idbono))  AS num_bonos
            FROM core.fact_fichajes f
            WHERE f.idempleado = :idempleado
              AND f.hinicial AT TIME ZONE 'UTC' >= :desde
              AND f.hinicial AT TIME ZONE 'UTC' < :hasta_excl
            GROUP BY 1
            ORDER BY 1
        """), {"idempleado": idempleado, "desde": desde, "hasta_excl": hasta_excl}).mappings().all()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────
#  REFRESCO ETL  (dispara el flujo Prefect bajo demanda)
# ─────────────────────────────────────────────────────────────────────

def _prefect_cfg():
    return (
        (os.getenv("PREFECT_API_URL") or "").rstrip("/"),
        os.getenv("PREFECT_DEPLOYMENT_ID") or "",
        os.getenv("PREFECT_API_KEY") or "",
    )

def _prefect_headers(api_key: str) -> dict:
    h = {"Content-Type": "application/json"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h

@router.post("/refrescar")
def refrescar():
    api, dep, key = _prefect_cfg()
    if not api or not dep:
        raise HTTPException(503, "Refresco no configurado: define PREFECT_API_URL y PREFECT_DEPLOYMENT_ID en .env")
    url = f"{api}/deployments/{dep}/create_flow_run"
    try:
        r = httpx.post(url, json={}, headers=_prefect_headers(key), timeout=20)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f"Prefect respondió {e.response.status_code}: {e.response.text[:200]}")
    except httpx.HTTPError as e:
        raise HTTPException(502, f"No se pudo contactar con Prefect: {e}")
    data = r.json()
    return {"flow_run_id": data.get("id"), "estado": (data.get("state") or {}).get("type")}

@router.get("/refrescar/{flow_run_id}")
def refrescar_estado(flow_run_id: str):
    api, _, key = _prefect_cfg()
    if not api:
        raise HTTPException(503, "Refresco no configurado")
    try:
        r = httpx.get(f"{api}/flow_runs/{flow_run_id}", headers=_prefect_headers(key), timeout=20)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"No se pudo consultar el estado: {e}")
    st = r.json().get("state") or {}
    return {"estado": st.get("type"), "nombre": st.get("name")}
