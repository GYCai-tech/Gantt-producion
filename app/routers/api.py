from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text
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
    a_maq.Descrip     AS descrip_maquina,
    am.Area           AS area,
    obs.Cantidad      AS piezas_a_fabricar,
    obs.IdArticulo    AS idarticulo_salida,
    a_sal.Descrip     AS descrip_salida
FROM Ordenes_Bonos_Lineas obl
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
    items = []
    for l in _leer_lineas(d0, d1, estado):
        abierta = l["abierta"]
        fin = l["fin"] or ahora
        min_real = None
        if not abierta and l["inicio"]:
            min_real = round((fin - l["inicio"]).total_seconds() / 60)

        # La línea abierta es trabajo EN CURSO; la cerrada, trabajo hecho.
        # No hay un tercer estado que inventar: el ERP no dice nada más.
        items.append({
            "id":         f"{l['idorden']}-{l['idbono']}-{l['idlinea']}",
            "recurso_id": str(l["idempleado"]) if vista == "empleado" else str(l["matricula"]).strip(),
            "tipo":       "real" if abierta else "trabajado",
            "estado":     "plazo" if abierta else "completado",
            "en_curso":   abierta,
            "estimado":   False,
            "start":      l["inicio"],
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
        })
    return items


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
