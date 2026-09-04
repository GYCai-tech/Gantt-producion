from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db import get_erp_engine

router = APIRouter(prefix="/api")


# ─────────────────────────────────────────────────────────────────────
#  Líneas de bono del día
# ─────────────────────────────────────────────────────────────────────
#  Traducción a T-SQL de la consulta de Access que define esta pantalla:
#  las líneas de bono en estado 1 de un día concreto, con el operario que
#  las fichó, la máquina en la que se hicieron y el artículo que producen.
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
  AND CAST(obl.Fecha AS date) = :dia
ORDER BY obl.Fecha
"""


def _nombre_completo(r) -> str:
    return " ".join(p.strip() for p in (r["nombre"], r["apellidos"]) if p) or f"#{r['idempleado']}"


@router.get("/lineas")
def get_lineas(
    dia: Optional[date] = Query(None, description="Día a consultar (YYYY-MM-DD). Por defecto, hoy."),
    estado: int = Query(1, description="IdEstado de la línea de bono. 1 = activa."),
):
    """Las líneas de bono de un día, tal cual las tiene el ERP ahora mismo."""
    dia = dia or date.today()

    try:
        with get_erp_engine().connect() as conn:
            filas = conn.execute(text(_LINEAS_QUERY), {"dia": dia, "estado": estado}).mappings().all()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=503, detail=f"No se pudo consultar el ERP: {e.__class__.__name__}")

    lineas, vistas = [], set()
    for r in filas:
        clave = (r["idorden"], r["idbono"], r["idlinea"])
        if clave in vistas:
            continue
        vistas.add(clave)

        # `inicio`/`fin` son lo que dibuja la barra. Si el ERP no tiene hora de
        # inicio, la línea no es pintable: cae a `Fecha` (instante de grabado).
        # Sin `Hfinal` la línea sigue abierta: el frontend la estira hasta ahora.
        inicio = r["hinicial"] or r["fecha"]
        lineas.append({
            "idorden":           r["idorden"],
            "idbono":            r["idbono"],
            "idlinea":           r["idlinea"],
            "idoperacion":       r["idoperacion"],
            "idempleado":        r["idempleado"],
            "empleado":          _nombre_completo(r),
            "fecha":             r["fecha"],
            "inicio":            inicio,
            "fin":               r["hfinal"],
            "abierta":           r["hfinal"] is None,
            "matricula":         r["matricula"],
            "descrip_maquina":   r["descrip_maquina"],
            "piezas_a_fabricar": r["piezas_a_fabricar"],
            "idarticulo_salida": r["idarticulo_salida"],
            "descrip_salida":    r["descrip_salida"],
        })

    return {
        "dia": dia,
        "ahora": datetime.now(),
        "total": len(lineas),
        "lineas": lineas,
    }
