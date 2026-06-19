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

    Es *list scheduling* con cola de prioridad: un bono solo se considera "listo"
    cuando todos sus bonos requeridos (los que también están en `rows`) ya se han
    planificado; entre los listos se elige primero el de fecha_prevista_fin más
    temprana. A diferencia de iterar un número fijo de pasadas, esto converge
    siempre en una sola pasada sea cual sea la profundidad de la cadena, y como
    `bono_fin`/`next_start` los pasa quien llama, funciona igual si el bono
    requerido vive en otro recurso o en otra vista (máquina/empleado) por
    completo — basta con que quien llama meta ahí TODOS los recursos a la vez.

    `rows`: dicts con idorden, idbono, recurso_key (string namespaced para no
    chocar máquinas con empleados) y min_est (duración neta en minutos) ya
    calculados. Devuelve {(idorden, idbono): (start, end)}.
    """
    by_key = {(r['idorden'], r['idbono']): r for r in rows if r['min_est'] > 0}

    dependientes = defaultdict(list)   # requerido -> [dependiente, ...] (sólo si ambos están en `rows`)
    pendientes = {}
    for key in by_key:
        reqs = [req for req in deps.get(key, []) if req in by_key]
        pendientes[key] = len(reqs)
        for req in reqs:
            dependientes[req].append(key)

    heap = [(_prioridad_programado(r), key) for key, r in by_key.items() if pendientes[key] == 0]
    heapq.heapify(heap)

    computed = {}
    while heap:
        _, key = heapq.heappop(heap)
        r = by_key[key]
        rk = r['recurso_key']
        start = next_start[rk]
        for req in deps.get(key, []):
            req_fin = bono_fin.get(req)
            if req_fin and req_fin > start:
                start = req_fin
        end = add_work_minutes(start, r['min_est'])
        computed[key] = (start, end)
        bono_fin[key] = end
        next_start[rk] = end
        for dep_key in dependientes.get(key, []):
            pendientes[dep_key] -= 1
            if pendientes[dep_key] == 0:
                heapq.heappush(heap, (_prioridad_programado(by_key[dep_key]), dep_key))

    # Ciclo en los datos (no debería pasar: core.dependencias_bono es un DAG real
    # sobre órdenes activas) — planifica lo que falte sin más restricciones para
    # no perder bonos del Gantt.
    for key, r in by_key.items():
        if key not in computed:
            rk = r['recurso_key']
            start = next_start[rk]
            end = add_work_minutes(start, r['min_est'])
            computed[key] = (start, end)
            bono_fin[key] = end
            next_start[rk] = end

    return computed


def _render_programado(r, recurso_id, id_prefix, start, end):
    prev   = r['fecha_prevista_fin']
    fiable = _prev_fiable(prev, r.get('fecha_orden'))
    if r.get('estado_bono') == 3:
        estado, estado_label = "parada", "Bloqueado"
    else:
        estado = _estado_programado(prev, r.get('fecha_orden'))
        estado_label = "Programado" if fiable else "Sin fecha"
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
            rows = conn.execute(text("""
                SELECT
                    de.idempleado::text                    AS id,
                    de.nombre_completo                     AS nombre,
                    COALESCE(de.departamento, 'Sin depto') AS sub,
                    COALESCE(de.departamento, 'Sin depto') AS area
                FROM core.dim_empleados de
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
    MAX_TRAB_MIN = 10 * 9 * 60

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
            bono_fin[(r["idorden"], r["idbono"])] = fin
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
                m.minutos_reales,
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
            bono_fin[(r["idorden"], r["idbono"])] = fin
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

        # ── Empleados: bonos trabajados (completados en la ventana) ────
        trabajados = conn.execute(text("""
            SELECT
                idempleado, idorden, idbono, operacion, articulo,
                cantidad_pedida, fecha_prevista_fin, fecha_orden, minutos_reales,
                piezas_producidas, fecha_inicio_real, fecha_fin_real, fecha_asignacion
            FROM analytics.v_asignaciones_empleado
            WHERE fase = 'TRABAJADO'
              AND (fecha_inicio_real IS NOT NULL OR fecha_asignacion IS NOT NULL)
        """)).mappings().all()

        for r in trabajados:
            inicio = r["fecha_inicio_real"] or r["fecha_asignacion"]
            if inicio is None:
                continue
            min_real = float(r["minutos_reales"] or 0)
            if r["fecha_fin_real"] is not None:
                fin = r["fecha_fin_real"]
            else:
                fin = add_work_minutes(inicio, min_real) if min_real > 0 else inicio + timedelta(minutes=30)
            fin_cap = add_work_minutes(inicio, min(min_real, MAX_TRAB_MIN)) if min_real > 0 else fin
            fin = min(fin, fin_cap)
            if fin <= desde or inicio >= hasta:
                continue
            empleado_items.append({
                "id":           f"trab_{r['idempleado']}_{r['idorden']}_{r['idbono']}",
                "idorden":      str(r["idorden"]),
                "idbono":       r["idbono"],
                "recurso_id":   str(r["idempleado"]),
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
                "estimado":     r["fecha_fin_real"] is None,
                "progreso":     None,
                "operarios":    None,
                "notas":        None,
                "min_real":     float(r["minutos_reales"]) if r["minutos_reales"] is not None else None,
                "piezas":       float(r["piezas_producidas"]) if r["piezas_producidas"] is not None else None,
            })

        # ── Empleados programados: candidatas a la cola del scheduler ──
        prog_emp = conn.execute(text("""
            SELECT
                e.idempleado, e.idorden, e.idbono, e.operacion, e.articulo,
                e.cantidad_pedida, e.fecha_prevista_fin, e.fecha_orden, e.min_estimados, e.situacion, e.estado_bono,
                e.minutos_reales
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
            key = (r['idorden'], r['idbono'])
            if key in computed:
                start, end = computed[key]
                result.append(_render_programado(r, str(r['recurso']), 'maq_prog', start, end))
        return result

    result = list(empleado_items)
    for r in prog_emp:
        key = (r['idorden'], r['idbono'])
        if key in computed:
            start, end = computed[key]
            result.append(_render_programado(r, str(r['idempleado']), 'emp_prog', start, end))
    return result


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
