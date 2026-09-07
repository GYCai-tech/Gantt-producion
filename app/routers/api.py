from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError

from app.db import get_erp_engine

router = APIRouter(prefix="/api")


# ─────────────────────────────────────────────────────────────────────
#  Líneas de bono: la ÚNICA consulta de la que sale todo lo que se pinta
# ─────────────────────────────────────────────────────────────────────
#  Traducción a T-SQL de la consulta de Access que define esta pantalla:
#  las líneas de bono en estado 1, con el operario que las fichó, la máquina
#  en la que se hicieron y el artículo que producen.
#
#  Dos columnas se añaden sobre la consulta original porque el Gantt no se
#  puede pintar sin ellas:
#    · Hinicial/Hfinal — inicio y fin reales de la línea (la barra). `Fecha`
#      es solo el instante en que el ERP grabó la línea, no su duración.
#    · Apellidos — `Nombre` a secas repite mucho (hay varios "José"), y las
#      filas del Gantt son personas: necesitan un nombre distinguible.
#
#  El JOIN con Ordenes_Bonos_Salidas puede duplicar una línea si un bono
#  declara más de un artículo de salida (medido: 3 bonos de 16.386, pero en
#  el Gantt saldría como dos barras idénticas superpuestas). Se deduplica en
#  Python por (orden, bono, línea), que es la identidad real de una barra.
# ─────────────────────────────────────────────────────────────────────

_LINEAS_QUERY = """
SELECT
    obl.IdOrden       AS idorden,
    obl.IdBono        AS idbono,
    obl.IdLinea       AS idlinea,
    obl.IdOperacion   AS idoperacion,
    obl.IdEmpleado    AS idempleado,
    ed.Nombre         AS nombre,
    ed.Apellidos      AS apellidos,
    obl.Fecha         AS fecha,
    obl.Hinicial      AS hinicial,
    obl.Hfinal        AS hfinal,
    obl.Matricula     AS matricula,
    ob.IdTrabajo      AS idtrabajo,
    a_maq.Descrip     AS descrip_maquina,
    am.Area           AS area,
    COALESCE(NULLIF(obs.Cantidad, 0), ob.CantidadTotal) AS piezas_a_fabricar,
    obs.IdArticulo    AS idarticulo_salida,
    a_sal.Descrip     AS descrip_salida
FROM Ordenes_Bonos_Lineas obl
    JOIN Ordenes_Bonos ob          ON obl.IdOrden     = ob.IdOrden
                                  AND obl.IdBono      = ob.IdBono
    JOIN Articulos_Maquinas am     ON obl.Matricula   = am.IdArticulo
    JOIN Articulos a_maq           ON am.IdArticulo   = a_maq.IdArticulo
    JOIN Ordenes_Bonos_Salidas obs ON obl.IdOrden     = obs.IdOrden
                                  AND obl.IdBono      = obs.IdBono
    JOIN Articulos a_sal           ON obs.IdArticulo  = a_sal.IdArticulo
    JOIN Empleados_Datos ed        ON obl.IdEmpleado  = ed.IdEmpleado
WHERE obl.IdEstado = :estado
  AND CAST(obl.Fecha AS date) BETWEEN :desde AND :hasta
ORDER BY obl.Fecha
"""


def _erp(query: str, params: dict):
    try:
        with get_erp_engine().connect() as conn:
            return conn.execute(text(query), params).mappings().all()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=503, detail=f"No se pudo consultar el ERP: {e.__class__.__name__}")


def _nombre_completo(r) -> str:
    partes = [p.strip() for p in (r["nombre"], r["apellidos"]) if p and p.strip()]
    return " ".join(partes) or f"#{r['idempleado']}"


def _leer_lineas(desde: date, hasta: date, estado: int = 1) -> list[dict]:
    """Las líneas de bono del rango, deduplicadas y con inicio/fin resueltos."""
    filas = _erp(_LINEAS_QUERY, {"desde": desde, "hasta": hasta, "estado": estado})

    lineas, vistas = [], set()
    for r in filas:
        clave = (r["idorden"], r["idbono"], r["idlinea"])
        if clave in vistas:
            continue
        vistas.add(clave)

        # `inicio`/`fin` son lo que dibuja la barra. Si el ERP no tiene hora de
        # inicio, la línea no es pintable: cae a `Fecha` (instante de grabado).
        # Sin `Hfinal` la línea sigue abierta.
        lineas.append({
            "idorden":           r["idorden"],
            "idbono":            r["idbono"],
            "idlinea":           r["idlinea"],
            "idoperacion":       r["idoperacion"],
            "idempleado":        r["idempleado"],
            "empleado":          _nombre_completo(r),
            "fecha":             r["fecha"],
            "inicio":            r["hinicial"] or r["fecha"],
            "fin":               r["hfinal"],
            "abierta":           r["hfinal"] is None,
            "matricula":         r["matricula"],
            "idtrabajo":         r["idtrabajo"],
            "descrip_maquina":   r["descrip_maquina"],
            "area":              (r["area"] or "").strip() or None,
            "piezas_a_fabricar": r["piezas_a_fabricar"],
            "idarticulo_salida": r["idarticulo_salida"],
            "descrip_salida":    r["descrip_salida"],
        })
    return lineas


@router.get("/lineas")
def get_lineas(
    dia: Optional[date] = Query(None, description="Día a consultar (YYYY-MM-DD). Por defecto, hoy."),
    estado: int = Query(1, description="IdEstado de la línea de bono. 1 = activa."),
):
    """La consulta en crudo, un día. No la usa el Gantt (que va por /items),
    pero es el sitio donde mirar qué está devolviendo el ERP."""
    dia = dia or date.today()
    lineas = _leer_lineas(dia, dia, estado)
    return {"dia": dia, "ahora": datetime.now(), "total": len(lineas), "lineas": lineas}


# ─────────────────────────────────────────────────────────────────────
#  GRUPOS  (las filas del Gantt)
# ─────────────────────────────────────────────────────────────────────
#  El frontend carga los grupos UNA vez (y al cambiar de vista), no al
#  navegar entre días. Por eso la lista no puede depender de la ventana
#  visible: se toman todos los operarios/máquinas que alguna vez han tenido
#  una línea de bono (29 y 103 respectivamente — el censo es pequeño). Así
#  ninguna barra se queda sin fila a la que colgarse, se navegue a donde se
#  navegue.
# ─────────────────────────────────────────────────────────────────────

_AREAS_RECIENTES_DIAS = 90


@router.get("/grupos")
def get_grupos(vista: str = Query("empleado", pattern="^(maquina|empleado)$")):
    if vista == "maquina":
        filas = _erp("""
            SELECT DISTINCT
                obl.Matricula  AS id,
                a.Descrip      AS nombre,
                am.Area        AS area
            FROM Ordenes_Bonos_Lineas obl
                JOIN Articulos_Maquinas am ON obl.Matricula = am.IdArticulo
                JOIN Articulos a           ON am.IdArticulo = a.IdArticulo
            ORDER BY a.Descrip
        """, {})
        return [{
            "id":     str(r["id"]).strip(),
            "nombre": (r["nombre"] or "").strip() or str(r["id"]).strip(),
            "sub":    f"Matrícula {str(r['id']).strip()}",
            "area":   (r["area"] or "").strip() or "Sin área",
        } for r in filas]

    # Operarios: el censo completo, sin depender de fechas.
    censo = _erp("""
        SELECT DISTINCT
            obl.IdEmpleado AS idempleado,
            ed.Nombre      AS nombre,
            ed.Apellidos   AS apellidos
        FROM Ordenes_Bonos_Lineas obl
            JOIN Empleados_Datos ed ON obl.IdEmpleado = ed.IdEmpleado
    """, {})

    # El área de un operario no es su departamento del ERP sino la de las
    # máquinas en las que trabaja: es lo que agrupa de verdad en planta. Solo
    # se mira la actividad reciente, para que quien cambió de sección no
    # arrastre para siempre las áreas de su puesto anterior.
    areas_por_empleado: dict[str, set] = {}
    for r in _erp("""
        SELECT DISTINCT obl.IdEmpleado AS idempleado, am.Area AS area
        FROM Ordenes_Bonos_Lineas obl
            JOIN Articulos_Maquinas am ON obl.Matricula = am.IdArticulo
        WHERE obl.Fecha >= DATEADD(day, :dias, GETDATE())
          AND am.Area IS NOT NULL
    """, {"dias": -_AREAS_RECIENTES_DIAS}):
        area = (r["area"] or "").strip()
        if area:
            areas_por_empleado.setdefault(str(r["idempleado"]), set()).add(area)

    grupos = []
    for r in censo:
        gid = str(r["idempleado"])
        areas = sorted(areas_por_empleado.get(gid, ())) or ["Sin máquina"]
        grupos.append({
            "id":     gid,
            "nombre": _nombre_completo(r),
            "sub":    ", ".join(areas),
            "areas":  areas,
        })
    return sorted(grupos, key=lambda g: g["nombre"])


# ─────────────────────────────────────────────────────────────────────
#  ITEMS  (las barras del Gantt)
# ─────────────────────────────────────────────────────────────────────

def _dia_local(dt: datetime) -> date:
    """El frontend manda `days[0].toISOString()`: medianoche LOCAL escrita en
    UTC. Hay que devolverla a hora local antes de quedarse con el día o, en
    horario español, se pierde un día entero.

    Depende de la zona del proceso, así que el contenedor fija TZ=Europe/Madrid
    (ver docker-compose.yml); si no, dentro de Docker sería UTC."""
    return (dt.astimezone() if dt.tzinfo else dt).date()


# ─────────────────────────────────────────────────────────────────────
#  DURACIÓN ESTIMADA DE UN BONO
# ─────────────────────────────────────────────────────────────────────
#  Cadena de prioridad, en este orden:
#    1. tiempo TEÓRICO del escandallo del ERP (Trabajos_ManoObra + montaje)
#    2. MEDIA de los registros reales: artículo → trabajo → máquina
#    3. nada: la barra se marca "sin tiempo" y salta el aviso
#
#  Cobertura medida el 2026-09-04 sobre los 570 bonos abiertos:
#    · escandallo .......................  9 bonos ( 1,6 %)
#    · campos Media*/Moda* de Ordenes_Bonos: 0-1,8 % — están vacíos, por eso
#      la media NO se lee del ERP sino que se calcula aquí desde el histórico
#      de líneas ya cerradas
#    · media del histórico ............... los 13 bonos del Gantt de hoy
#      (6 por artículo, 7 por máquina)
#
#  Dos limitaciones conocidas y asumidas:
#    · la media por máquina es gruesa — una misma máquina hace piezas muy
#      distintas — pero es el último escalón antes del aviso;
#    · es min/pieza pura, sin término de preparación. En bonos de pocas piezas
#      el setup es la mayor parte del tiempo, así que ahí se queda corta.
# ─────────────────────────────────────────────────────────────────────

_ESTIMA_TTL_S     = 600   # el escandallo y el histórico no cambian por minutos
_HIST_MESES       = 18
_MIN_BONOS_MEDIA  = 3     # con menos bonos, la media es ruido
_HORAS_LINEA_VIVA = 24    # una línea abierta más vieja que esto es fantasma, no trabajo

_SQL_TEORICO = """
WITH mano_obra AS (
    SELECT IdTrabajo, SUM(Duracion) * 1440.0 AS MinPieza
    FROM Trabajos_ManoObra
    WHERE Duracion > 0        -- 0 no es una medición, es la casilla sin rellenar
    GROUP BY IdTrabajo
)
SELECT
    ob.IdOrden AS idorden,
    ob.IdBono  AS idbono,
    -- Duracion viene en DÍAS (IdUnidadDuracion='D'): x1440 -> min/pieza.
    -- Se prefiere el detalle de Trabajos_ManoObra sobre Trabajos_Fases.TiempoMO,
    -- que es la copia desnormalizada y a veces está sin recalcular.
    COALESCE(mo.MinPieza, NULLIF(tf.TiempoMO, 0) * 1440.0) AS min_pieza,
    COALESCE(NULLIF(ob.TiempoMontaje, 0), 0)
      + COALESCE(NULLIF(ob.TiempoDesMontaje, 0), 0)        AS setup_min
FROM Ordenes_Bonos ob
    LEFT JOIN mano_obra mo      ON mo.IdTrabajo = ob.IdTrabajo
    LEFT JOIN Trabajos_Fases tf ON tf.IdTrabajo = ob.IdTrabajo
WHERE ob.IdEstado IN (0, 1, 3)   -- solo bonos abiertos; los cerrados ya tienen minutos reales
  AND COALESCE(mo.MinPieza, NULLIF(tf.TiempoMO, 0) * 1440.0) > 0
"""

_SQL_MEDIAS = """
WITH bono_min AS (
    SELECT obl.IdOrden, obl.IdBono,
           SUM(DATEDIFF(minute, obl.Hinicial, obl.Hfinal)) AS minutos
    FROM Ordenes_Bonos_Lineas obl
    WHERE obl.Hinicial IS NOT NULL
      AND obl.Hfinal   IS NOT NULL
      AND obl.Hfinal   > obl.Hinicial
      AND obl.Fecha   >= DATEADD(month, :meses, GETDATE())
    GROUP BY obl.IdOrden, obl.IdBono
)
SELECT
    obs.IdArticulo AS idarticulo,
    ob.IdTrabajo   AS idtrabajo,
    ob.Matricula   AS matricula,
    COUNT(*)          AS n,
    SUM(bm.minutos)   AS minutos,
    SUM(obs.Cantidad) AS piezas
FROM bono_min bm
    JOIN Ordenes_Bonos ob          ON ob.IdOrden  = bm.IdOrden AND ob.IdBono  = bm.IdBono
    JOIN Ordenes_Bonos_Salidas obs ON obs.IdOrden = bm.IdOrden AND obs.IdBono = bm.IdBono
WHERE ob.IdEstado = 2            -- solo bonos terminados: los abiertos aún no miden nada
  AND obs.Cantidad > 0
GROUP BY obs.IdArticulo, ob.IdTrabajo, ob.Matricula
"""

_cache_estima = {"ts": None, "teoricos": {}, "medias": {"articulo": {}, "trabajo": {}, "maquina": {}}}


def _cargar_estimaciones():
    """Escandallo y medias históricas, cacheados _ESTIMA_TTL_S segundos.

    Si el ERP falla se reutiliza la última caché aunque esté caducada: es
    preferible estimar con datos de hace diez minutos que marcar de golpe
    todas las barras como "sin tiempo"."""
    ahora = datetime.now()
    ts = _cache_estima["ts"]
    if ts is not None and (ahora - ts).total_seconds() < _ESTIMA_TTL_S:
        return _cache_estima["teoricos"], _cache_estima["medias"]

    try:
        with get_erp_engine().connect() as conn:
            teoricos = {
                (int(r["idorden"]), int(r["idbono"])):
                    (float(r["setup_min"] or 0), float(r["min_pieza"]))
                for r in conn.execute(text(_SQL_TEORICO)).mappings()
            }
            medias = {"articulo": {}, "trabajo": {}, "maquina": {}}
            for r in conn.execute(text(_SQL_MEDIAS), {"meses": -_HIST_MESES}).mappings():
                for nivel, clave in (("articulo", r["idarticulo"]),
                                     ("trabajo",  r["idtrabajo"]),
                                     ("maquina",  r["matricula"])):
                    if clave is None:
                        continue
                    if isinstance(clave, str):
                        clave = clave.strip()
                    acc = medias[nivel].setdefault(clave, {"n": 0, "minutos": 0.0, "piezas": 0.0})
                    acc["n"]       += int(r["n"] or 0)
                    acc["minutos"] += float(r["minutos"] or 0)
                    acc["piezas"]  += float(r["piezas"] or 0)
    except SQLAlchemyError as e:
        print(f"[items] estimaciones no disponibles, se reutiliza la caché: {e.__class__.__name__}")
        return _cache_estima["teoricos"], _cache_estima["medias"]

    _cache_estima.update(ts=ahora, teoricos=teoricos, medias=medias)
    return teoricos, medias


def _estimar(linea: dict, teoricos: dict, medias: dict):
    """(min/pieza, setup en minutos, origen) para el bono, o (None, 0, None).

    Devuelve el RITMO, no el total: quien llama multiplica por las piezas que
    de verdad quedan por hacer. Es la diferencia entre "cuánto cuesta el bono
    entero" y "cuánto falta", que es lo que hay que pintar."""
    teorico = teoricos.get((linea["idorden"], linea["idbono"]))
    if teorico:
        setup, min_pieza = teorico
        return min_pieza, setup, "teorico"

    matricula = (linea["matricula"] or "").strip()
    for origen, nivel, clave in (
        ("media_articulo", "articulo", linea["idarticulo_salida"]),
        ("media_trabajo",  "trabajo",  linea["idtrabajo"]),
        ("media_maquina",  "maquina",  matricula),
    ):
        acc = medias[nivel].get(clave)
        if acc and acc["n"] >= _MIN_BONOS_MEDIA and acc["piezas"] > 0:
            return acc["minutos"] / acc["piezas"], 0.0, origen

    return None, 0.0, None


def _avance_por_bono(lineas: list[dict], ahora: datetime) -> dict:
    """Lo que lleva cada bono: minutos gastados, piezas declaradas y cuántos
    operarios lo tienen abierto ahora.

    Se cuentan TODAS las líneas del bono, no solo las de la ventana visible:
    un bono que empezó ayer ya lleva tiempo consumido y lo que queda por hacer
    hoy es menos. Son minutos-hombre (dos operarios a la vez gastan dos
    minutos por cada minuto de reloj), igual que la media histórica.

    OJO con las líneas fantasma: el ERP tiene líneas abiertas que nadie cerró
    hace meses o años. Contarlas hasta GETDATE() dispara el consumo (medido:
    un bono con 3.383 min "gastados" que en realidad lleva unas horas) y además
    infla el recuento de operarios activos, que es el divisor del tiempo que
    queda. Una línea abierta solo cuenta si empezó en las últimas
    _HORAS_LINEA_VIVA horas; el resto aporta cero."""
    ordenes = sorted({l["idorden"] for l in lineas})
    if not ordenes:
        return {}

    consulta = text(f"""
        SELECT IdOrden AS idorden, IdBono AS idbono,
               SUM(DATEDIFF(minute, Hinicial,
                     CASE WHEN Hfinal IS NOT NULL THEN Hfinal
                          WHEN Hinicial > DATEADD(hour, -{_HORAS_LINEA_VIVA}, GETDATE()) THEN GETDATE()
                          ELSE Hinicial END)) AS minutos,
               COUNT(DISTINCT CASE
                     WHEN Hfinal IS NULL
                      AND Hinicial > DATEADD(hour, -{_HORAS_LINEA_VIVA}, GETDATE())
                     THEN IdEmpleado END) AS operarios_activos,
               SUM(ISNULL(TotalPiezas, 0)) AS piezas
        FROM Ordenes_Bonos_Lineas
        WHERE Hinicial IS NOT NULL
          AND IdOrden IN :ordenes
        GROUP BY IdOrden, IdBono
    """).bindparams(bindparam("ordenes", expanding=True))

    try:
        with get_erp_engine().connect() as conn:
            filas = conn.execute(consulta, {"ordenes": ordenes}).mappings().all()
    except SQLAlchemyError:
        return {}

    return {
        (r["idorden"], r["idbono"]): {
            "minutos":   float(r["minutos"] or 0),
            "operarios": max(1, int(r["operarios_activos"] or 0)),
            "piezas":    float(r["piezas"] or 0),
        }
        for r in filas
    }


@router.get("/items")
def get_items(
    vista: str = Query("empleado", pattern="^(maquina|empleado)$"),
    desde: Optional[datetime] = None,
    hasta: Optional[datetime] = None,
    estado: int = Query(1, description="IdEstado de la línea de bono. 1 = activa."),
):
    hoy = date.today()
    d0 = _dia_local(desde) if desde else hoy
    # `hasta` llega como el día siguiente al último visible (fin exclusivo).
    d1 = _dia_local(hasta - timedelta(seconds=1)) if hasta else hoy
    if d1 < d0:
        d1 = d0

    ahora = datetime.now()
    lineas = _leer_lineas(d0, d1, estado)
    teoricos, medias = _cargar_estimaciones()
    avance = _avance_por_bono([l for l in lineas if l["abierta"]], ahora)

    items = []
    for l in lineas:
        abierta = l["abierta"]
        inicio  = l["inicio"]
        fin     = l["fin"] or ahora
        min_real = None if abierta else round((fin - inicio).total_seconds() / 60)

        # La línea abierta es trabajo EN CURSO; la cerrada, trabajo hecho.
        # No hay un tercer estado que inventar: el ERP no dice nada más.
        item = {
            "id":         f"{l['idorden']}-{l['idbono']}-{l['idlinea']}",
            "recurso_id": str(l["idempleado"]) if vista == "empleado" else str(l["matricula"]).strip(),
            "tipo":       "real" if abierta else "trabajado",
            "estado":     "plazo" if abierta else "completado",
            "en_curso":   abierta,
            "estimado":   False,
            "start":      inicio,
            "end":        fin,
            "idorden":    l["idorden"],
            "idbono":     l["idbono"],
            "art":        l["descrip_salida"],
            # En la vista de operarios interesa saber la máquina; en la de
            # máquinas, quién estaba en ella.
            "operacion":  (f"{l['matricula']} · {l['descrip_maquina']}"
                           if vista == "empleado" else l["empleado"]),
            "operarios":  l["empleado"],
            "piezas":     l["piezas_a_fabricar"],
            "min_real":   min_real,
            "sin_tiempo": False,
        }

        # Solo se estima lo que sigue abierto: una línea cerrada ya tiene su
        # tiempo real medido y no hay nada que predecir.
        if abierta:
            _proyectar(item, l, ahora, teoricos, medias, avance)

        items.append(item)

    # La cola solo tiene sentido si la ventana llega a hoy o más allá: en un
    # día pasado no había "programado", había lo que pasó.
    if d1 >= hoy:
        ocupado_hasta = {}
        for it in items:
            if it["en_curso"]:
                rid = it["recurso_id"]
                ocupado_hasta[rid] = max(ocupado_hasta.get(rid, ahora), it["end"])
        hasta_dt = datetime.combine(d1, datetime.min.time()).replace(hour=JORNADA_FIN)
        items += _encolar(vista, ocupado_hasta, hasta_dt, ahora, teoricos, medias)

    return items


#  Margen antes de dar un bono por retrasado. El ritmo real contra el esperado
#  baila solo con que la preparación caiga dentro o fuera de lo ya declarado;
#  sin margen, el ámbar sería ruido.
_TOLERANCIA_RITMO = 0.15


# ─────────────────────────────────────────────────────────────────────
#  COLA: los bonos que un operario tiene asignados y aún no ha empezado
# ─────────────────────────────────────────────────────────────────────
#  Fuente: `persV_DatosAsociadoEmpleado`, vista del ERP sobre
#  `Pers_EmpleadosOrdenBono` (Orden, Bono → IdEmpleado). Ahí SÍ está la
#  asignación operario↔bono; `Ordenes_Bonos.IdEmpleado`, que es donde parecía
#  que debía vivir, está a NULL en los 562 bonos abiertos.
#
#  La vista trae ya resuelto todo lo que hace falta para una barra: máquina,
#  área, artículo, piezas objetivo, piezas hechas y la posición manual
#  (`Conf_OrdenesBonos.ordenar`). Medido: 19.517 asignaciones, 25 empleados,
#  238 de los 562 bonos abiertos con operario.
#
#  OJO con `Fabricadas`: sale de `Ordenes_Bonos_Salidas.CantidadTotal`. En esa
#  tabla `Cantidad` es el objetivo y `CantidadTotal` lo ya producido — justo al
#  revés que en `Ordenes_Bonos`, donde `CantidadTotal` es el objetivo.
#
#  Se toma solo `IdEstado = 0` (aún sin arrancar): los de estado 1 ya salen
#  como barras reales de su propio fichaje.
# ─────────────────────────────────────────────────────────────────────

_COLA_QUERY = """
SELECT
    v.IdEmpleado                              AS idempleado,
    ed.Nombre                                 AS nombre,
    ed.Apellidos                              AS apellidos,
    v.idorden                                 AS idorden,
    v.IdBono                                  AS idbono,
    v.ordenar                                 AS ordenar,
    v.CdgMaq                                  AS matricula,
    v.Maquina                                 AS descrip_maquina,
    v.Area                                    AS area,
    v.ArtFabricar                             AS descrip_salida,
    TRY_CAST(v.PiezasFabricar AS decimal(18,4)) AS objetivo,
    v.Fabricadas                              AS fabricadas,
    ob.IdTrabajo                              AS idtrabajo,
    obs.IdArticulo                            AS idarticulo_salida
FROM persV_DatosAsociadoEmpleado v
    JOIN Ordenes_Bonos ob            ON ob.IdOrden  = v.idorden AND ob.IdBono  = v.IdBono
    JOIN Empleados_Datos ed          ON ed.IdEmpleado = v.IdEmpleado
    LEFT JOIN Ordenes_Bonos_Salidas obs ON obs.IdOrden = v.idorden AND obs.IdBono = v.IdBono
WHERE ob.IdEstado = 0
"""

#  Un bono en cola sin tiempo estimado no se puede dimensionar. Se le da un
#  bloque nominal para que siga ocupando su sitio en la cola (si no, los que
#  van detrás se adelantarían como si no existiera) y va marcado `sin_tiempo`.
_MIN_BLOQUE_SIN_TIEMPO = 60


# ─────────────────────────────────────────────────────────────────────
#  SEMÁFORO: ¿puede ESTE operario trabajar ESTE bono ahora mismo?
# ─────────────────────────────────────────────────────────────────────
#  `dbo.persFTrazaordenesOperariosColor(idorden, idbono, idempleado)` es la
#  función del ERP que alimenta el semáforo del programa de producción. Es la
#  misma que consume la vista `PersVTrazaordenesOperarios`; se invoca aquí
#  directamente sobre nuestra consulta en vez de usar esa vista, porque la
#  vista además reescribe `ordenar = 0` como 999 — un valor que se colaría
#  como posición real y colocaría los bonos sin secuencia por delante de los
#  que sí la tienen.
#
#  Devuelve un RGB concatenado. El rojo NO es `IdEstado = 3`: de las 86 filas
#  rojas medidas, cero son bonos bloqueados. Es disponibilidad *para esa
#  persona* — falta material, lo tiene cogido otro, falta una fase previa. Por
#  eso el mismo bono puede ser verde para uno y rojo para otro.
#
#  La función está WITH ENCRYPTION: es una caja negra. Sabemos QUE algo está
#  rojo, no POR QUÉ.
# ─────────────────────────────────────────────────────────────────────

_SEMAFORO = {
    '153255255': 'en_curso',    # azul  — la está trabajando ahora mismo
    '000204051': 'disponible',  # verde — puede ponerse con ella
    '255051051': 'bloqueada',   # rojo  — la tiene asignada pero no puede
}

#  Orden en que se sirve la cola. Lo que se puede hacer va primero; dentro de
#  cada grupo sigue mandando la secuencia manual del ERP.
_PRIO_SEMAFORO = {'en_curso': 0, 'disponible': 1, 'bloqueada': 2}

#  La función se evalúa fila a fila: la consulta pasa de 12 ms a ~360 ms. Se
#  cachea porque el color cambia cuando llega material o alguien coge un bono
#  —minutos, no segundos— y el Gantt se refresca solo cada pocos minutos.
_SEMAFORO_TTL_S = 120
_cache_semaforo = {"ts": None, "mapa": {}}

_SEMAFORO_QUERY = """
SELECT v.idorden, v.IdBono AS idbono, v.IdEmpleado AS idempleado, col.color
FROM persV_DatosAsociadoEmpleado v
    JOIN Ordenes_Bonos ob ON ob.IdOrden = v.idorden AND ob.IdBono = v.IdBono
    OUTER APPLY dbo.persFTrazaordenesOperariosColor(v.idorden, v.IdBono, v.IdEmpleado) col
WHERE ob.IdEstado = 0
"""


def _cargar_semaforo() -> dict:
    """{(idorden, idbono, idempleado): 'disponible'|'bloqueada'|'en_curso'}.

    Si el ERP falla se reutiliza la última caché aunque esté caducada: es mejor
    ordenar con colores de hace unos minutos que perder el semáforo entero y
    volver a servir la cola en un orden que el operario no puede seguir."""
    ahora = datetime.now()
    ts = _cache_semaforo["ts"]
    if ts is not None and (ahora - ts).total_seconds() < _SEMAFORO_TTL_S:
        return _cache_semaforo["mapa"]
    try:
        filas = _erp(_SEMAFORO_QUERY, {})
    except HTTPException:
        print("[items] semáforo no disponible, se reutiliza la caché")
        return _cache_semaforo["mapa"]

    mapa = {
        (r["idorden"], r["idbono"], r["idempleado"]): _SEMAFORO.get(r["color"], 'disponible')
        for r in filas
    }
    _cache_semaforo.update(ts=ahora, mapa=mapa)
    return mapa

JORNADA_INICIO = 7
JORNADA_FIN    = 16


def _siguiente_hueco(dt: datetime) -> datetime:
    """El primer instante laborable a partir de `dt` (07:00–16:00, L-V)."""
    t = dt
    for _ in range(14):
        if t.weekday() >= 5:
            t = (t + timedelta(days=1)).replace(hour=JORNADA_INICIO, minute=0, second=0, microsecond=0)
            continue
        if t.hour < JORNADA_INICIO:
            return t.replace(hour=JORNADA_INICIO, minute=0, second=0, microsecond=0)
        if t.hour >= JORNADA_FIN:
            t = (t + timedelta(days=1)).replace(hour=JORNADA_INICIO, minute=0, second=0, microsecond=0)
            continue
        return t
    return t


def _sumar_laborables(inicio: datetime, minutos: float) -> datetime:
    """Avanza `minutos` de trabajo desde `inicio` sin salirse de la jornada.

    Cuenta la jornada entera (07:00–16:00 = 540 min) sin descontar el descanso
    de 11:00–11:15 a propósito: el eje del Gantt tampoco lo comprime, lo pinta
    como una banda. Descontarlo aquí desalinearía las barras del eje."""
    t = _siguiente_hueco(inicio)
    restante = float(minutos)
    for _ in range(400):
        fin_jornada = t.replace(hour=JORNADA_FIN, minute=0, second=0, microsecond=0)
        hueco = (fin_jornada - t).total_seconds() / 60
        if restante <= hueco:
            return t + timedelta(minutes=restante)
        restante -= hueco
        t = _siguiente_hueco(fin_jornada)
    return t


def _leer_cola() -> list[dict]:
    """Los bonos asignados y aún sin empezar, deduplicados por (bono, operario).

    La vista repite fila cuando un bono declara más de un artículo de salida,
    igual que la consulta de líneas."""
    filas = _erp(_COLA_QUERY, {})
    semaforo = _cargar_semaforo()
    cola, vistas = [], set()
    for r in filas:
        clave = (r["idorden"], r["idbono"], r["idempleado"])
        if clave in vistas:
            continue
        vistas.add(clave)
        cola.append({
            # Si el ERP no devuelve color, se asume disponible: es preferible
            # ofrecer trabajo de más que esconderlo por un fallo de la función.
            "semaforo":          semaforo.get(clave, 'disponible'),
            "idorden":           r["idorden"],
            "idbono":            r["idbono"],
            "idempleado":        r["idempleado"],
            "empleado":          _nombre_completo(r),
            "ordenar":           int(r["ordenar"] or 0),
            "matricula":         (r["matricula"] or "").strip(),
            "descrip_maquina":   r["descrip_maquina"],
            "area":              (r["area"] or "").strip() or None,
            "descrip_salida":    r["descrip_salida"],
            "idarticulo_salida": r["idarticulo_salida"],
            "idtrabajo":         r["idtrabajo"],
            "piezas_a_fabricar": float(r["objetivo"] or 0),
            "fabricadas":        float(r["fabricadas"] or 0),
        })
    return cola


def _encolar(vista: str, ocupado_hasta: dict, hasta_dt: datetime,
             ahora: datetime, teoricos, medias) -> list[dict]:
    """Las barras 'programado': la cola de cada recurso, una detrás de otra.

    Cada bono arranca cuando el recurso queda libre —después de lo que está
    haciendo ahora— y dura lo que falta por fabricar.

    El orden lo manda primero el semáforo del ERP y después la secuencia manual
    (`ordenar`). Es deliberado: con el 40% de la cola en rojo, un plan que
    ignore la disponibilidad es ficción — el operario no puede seguirlo y acaba
    abriendo otro bono por su cuenta. Poniendo delante lo que sí puede hacer, la
    cola se reordena sola: cuando llega el material el bono pasa a verde en el
    ERP y sube de posición sin que nadie replanifique nada. La secuencia manual
    no se pierde, sigue mandando *dentro* de lo que es viable.

    Se corta en cuanto la cola se sale de la ventana visible: encolar meses de
    trabajo que nadie va a ver solo gasta tiempo."""
    por_recurso: dict[str, list] = {}
    for b in _leer_cola():
        rid = str(b["idempleado"]) if vista == "empleado" else b["matricula"]
        if not rid:
            continue
        if vista == "maquina":
            # El semáforo es por (bono, operario), pero una máquina no tiene
            # varios operarios: tiene un bono. Si el bono está asignado a dos
            # personas saldría dos veces, duplicando su tiempo en la cola de la
            # máquina. Se queda una sola barra, con el mejor color: a la máquina
            # le basta con que ALGUIEN pueda hacerlo.
            gemelo = next((x for x in por_recurso.get(rid, [])
                           if x["idorden"] == b["idorden"] and x["idbono"] == b["idbono"]), None)
            if gemelo is not None:
                if _PRIO_SEMAFORO[b["semaforo"]] < _PRIO_SEMAFORO[gemelo["semaforo"]]:
                    gemelo["semaforo"] = b["semaforo"]
                continue
        por_recurso.setdefault(rid, []).append(b)

    items = []
    for rid, bonos in por_recurso.items():
        # 1º lo que se puede trabajar, 2º la secuencia manual del ERP
        # (`ordenar` = 0 significa "sin colocar a mano": esos van al final).
        bonos.sort(key=lambda b: (_PRIO_SEMAFORO[b["semaforo"]],
                                  b["ordenar"] or 10_000,
                                  b["idorden"], b["idbono"]))
        cursor = _siguiente_hueco(max(ocupado_hasta.get(rid, ahora), ahora))

        for b in bonos:
            if cursor > hasta_dt:
                break
            min_pieza, setup, origen = _estimar(b, teoricos, medias)
            pendientes = max(0.0, b["piezas_a_fabricar"] - b["fabricadas"])
            sin_tiempo = not min_pieza or min_pieza <= 0
            dur = _MIN_BLOQUE_SIN_TIEMPO if sin_tiempo else setup + pendientes * min_pieza
            if dur <= 0:
                continue

            inicio = _siguiente_hueco(cursor)
            fin    = _sumar_laborables(inicio, dur)
            cursor = fin

            items.append({
                "id":         f"P-{b['idorden']}-{b['idbono']}-{rid}",
                "recurso_id": rid,
                "tipo":       "programado",
                # El semáforo manda sobre el color de la barra; "sin-estimar"
                # solo se impone cuando además no sabemos cuánto dura.
                "estado":     ("sin-estimar" if sin_tiempo else
                               "parada" if b["semaforo"] == "bloqueada" else
                               "disponible"),
                "semaforo":   b["semaforo"],
                "en_curso":   False,
                "estimado":   True,
                "start":      inicio,
                "end":        fin,
                "idorden":    b["idorden"],
                "idbono":     b["idbono"],
                "art":        b["descrip_salida"],
                "operacion":  (f"{b['matricula']} · {b['descrip_maquina']}"
                               if vista == "empleado" else b["empleado"]),
                "operarios":  b["empleado"],
                "piezas":     b["piezas_a_fabricar"],
                "min_real":   None,
                "sin_tiempo": sin_tiempo,
                "orden_manual":     b["ordenar"] or None,
                "origen_estimado":  origen,
                "piezas_objetivo":  b["piezas_a_fabricar"] or None,
                "piezas_hechas":    b["fabricadas"],
                "piezas_pendientes": pendientes,
                "min_pieza":        round(min_pieza, 3) if min_pieza else None,
                "min_restantes":    round(dur),
                "base_estimacion":  "piezas",
            })
    return items


def _proyectar(item: dict, linea: dict, ahora: datetime, teoricos, medias, avance) -> None:
    """Estira la barra abierta hasta su fin estimado, o la marca sin tiempo.

    Lo que queda por delante se calcula **en piezas**, no en minutos:

        (objetivo − declaradas) × min/pieza

    Restar minutos era lo anterior y estaba mal: un bono puede cambiar de
    manos, y entonces la barra de quien lo tiene ahora heredaba el tiempo
    que gastó otro (medido en 6372/30: 1.434 de los 3.400 minutos eran de un
    compañero que lo dejó hace días). En piezas eso no pasa — da igual quién
    hizo las anteriores, lo que falta es lo que falta.

    El problema es que **solo 11 de 565 bonos abiertos declaran piezas**. Sin
    ese dato no hay forma de saber lo avanzado, así que se cae al criterio
    viejo (presupuesto de minutos menos lo gastado) y el item lo dice en
    `base_estimacion` para que no haya que adivinarlo."""
    min_pieza, setup, origen = _estimar(linea, teoricos, medias)
    item["origen_estimado"] = origen

    if not min_pieza or min_pieza <= 0:
        item["sin_tiempo"] = True
        item["estado"] = "sin-estimar"
        return

    gasto     = avance.get((linea["idorden"], linea["idbono"]), {"minutos": 0.0, "operarios": 1, "piezas": 0.0})
    objetivo  = float(linea["piezas_a_fabricar"] or 0)
    hechas    = min(gasto["piezas"], objetivo) if objetivo else gasto["piezas"]
    consumido = gasto["minutos"]

    item["min_pieza"]       = round(min_pieza, 3)
    item["min_consumidos"]  = round(consumido)
    item["piezas_objetivo"] = objetivo or None
    item["piezas_hechas"]   = hechas
    # La preparación solo cuenta si el bono aún no ha arrancado; si ya hay
    # minutos gastados, esa preparación ya está pagada.
    item["min_estimados"]   = round(objetivo * min_pieza + (setup if consumido == 0 else 0))

    if hechas > 0 and objetivo > 0:
        pendientes = max(0.0, objetivo - hechas)
        restante   = pendientes * min_pieza
        ritmo_real = consumido / hechas
        item["base_estimacion"] = "piezas"
        item["piezas_pendientes"] = pendientes
        item["min_pieza_real"]    = round(ritmo_real, 3)
        item["progreso_piezas"]   = round(hechas / objetivo * 100)
        item["excedido"]          = ritmo_real > min_pieza * (1 + _TOLERANCIA_RITMO)
    else:
        # Sin piezas declaradas no se puede medir el avance real.
        restante = max(0.0, item["min_estimados"] - consumido)
        item["base_estimacion"] = "minutos"
        item["excedido"] = consumido > item["min_estimados"]

    if item["excedido"]:
        # Va por encima del ritmo esperado. Se sigue dibujando lo que queda
        # (el trabajo pendiente no desaparece por ir tarde), pero en ámbar.
        item["estado"] = "riesgo"

    # Los minutos son minutos-HOMBRE. Para llevarlos al eje de tiempo se
    # reparten entre los operarios que tienen el bono abierto ahora mismo;
    # en la práctica casi siempre es uno (Ordenes_Bonos.Operarios = 1).
    restante_reloj = restante / gasto["operarios"]
    item["min_restantes"] = round(restante_reloj)
    if restante_reloj <= 0:
        return

    fin_estimado = ahora + timedelta(minutes=restante_reloj)
    item["end"]          = fin_estimado
    item["fin_estimado"] = fin_estimado

    # `progreso` es el relleno visual de la barra: qué parte de ella ya ha
    # transcurrido, para que lo sólido acabe justo en la línea de ahora.
    # El avance en piezas va aparte, en `progreso_piezas`.
    total = (fin_estimado - item["start"]).total_seconds()
    if total > 0:
        transcurrido = (ahora - item["start"]).total_seconds()
        item["progreso"] = round(max(0.0, min(1.0, transcurrido / total)) * 100)


# ─────────────────────────────────────────────────────────────────────
#  REFRESCO
# ─────────────────────────────────────────────────────────────────────
#  Ya no hay ETL que lanzar: los datos vienen del ERP en vivo, así que cada
#  carga de /items ya trae lo último. Se mantienen las dos rutas porque el
#  botón "Actualizar" del frontend espera el protocolo de Prefect
#  (lanzar → sondear estado → recargar); se responde COMPLETED de inmediato
#  y el frontend recarga, que es exactamente lo que hace falta.
# ─────────────────────────────────────────────────────────────────────

@router.post("/refrescar")
def refrescar():
    return {"flow_run_id": "erp-en-vivo", "estado": "COMPLETED"}


@router.get("/refrescar/{flow_run_id}")
def refrescar_estado(flow_run_id: str):
    return {"flow_run_id": flow_run_id, "estado": "COMPLETED"}
