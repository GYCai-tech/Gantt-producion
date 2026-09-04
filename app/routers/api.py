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
    return items


#  Margen antes de dar un bono por retrasado. El ritmo real contra el esperado
#  baila solo con que la preparación caiga dentro o fuera de lo ya declarado;
#  sin margen, el ámbar sería ruido.
_TOLERANCIA_RITMO = 0.15


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
