"""Urgencia de bonos: bonos vivos de órdenes cuyo artículo no tiene stock libre.

Es una consulta de Access pasada a SQL Server. Coge los bonos vivos —en espera,
activados o bloqueados— y se queda con los de órdenes cuyo artículo tiene menos
stock que reserva.

Cada fila junta dos artículos, y conviene no confundirlos:

- El del BONO (`Ordenes_Bonos_Salidas.IdArticulo`): lo que fabrica ese bono,
  que puede ser una pieza intermedia de Producción, como una varilla cortada.
- El de la ORDEN (`Ordenes.IdArticulo`): el producto final. Su stock es el
  que se filtra y se enseña, y por eso todos los bonos de una orden salen con
  el mismo número. El -20 de la varilla 60007034 es el del frente de jaulón
  que fabrica su orden, no el de la varilla.

DOS ALMACENES, NO UNO. La consulta original solo miraba el Principal. Mirando
también PRODUCCIÓN aparecen agujeros que estaban ocultos y son los gordos: de
los 170 artículos con bonos vivos, 51 están en negativo en Principal y 17 en
Producción —faltan 16.410 pasadores de plástico, 4.662 juntas de goma—, y solo
2 lo están en los dos a la vez.

Un bono entra si su artículo está en negativo en ALGUNO de los dos, no si la
suma da negativa. Es la lectura de urgencia: un agujero en Producción sigue
siendo un agujero aunque en Principal sobre material. La diferencia medida es
de un artículo (66 frente a 65) y además no es material, sino un concepto de
reparación; el filtro de la pantalla decide cuál de los dos almacenes se mira.

Se resta `StockReservado`, la reserva, y en la última columna también
`Articulos_Stock.StockMinimo`: cuánto falta para volver al mínimo.
"""
from datetime import datetime

from fastapi import APIRouter

from app.routers.api import _erp

router = APIRouter()

#  Los estados de bono que siguen vivos: 0 en espera, 1 activado, 3 bloqueado.
_ESTADOS_VIVOS = (0, 1, 3)
_ESTADO_NOMBRE = {0: "En espera", 1: "Activado", 3: "Bloqueado"}

_ALMACEN_PRINCIPAL = 0
_ALMACEN_PRODUCCION = 2

#  El stock por artículo se agrega aparte en un CTE en vez de repetir el JOIN a
#  `Articulos_Stock` por almacén: así cada artículo aporta sus dos saldos en una
#  fila y el WHERE puede preguntar por los dos a la vez.
#
#  El GROUP BY viene de la consulta de Access y hace de DISTINCT:
#  `Ordenes_Bonos_Salidas` repite fila cuando un bono declara el mismo artículo
#  más de una vez.
_SQL = """
WITH stock AS (
    SELECT s.IdArticulo,
           SUM(CASE WHEN s.IdAlmacen = :principal
                    THEN ISNULL(s.Stock, 0) - ISNULL(s.StockReservado, 0) ELSE 0 END) AS libre_principal,
           SUM(CASE WHEN s.IdAlmacen = :produccion
                    THEN ISNULL(s.Stock, 0) - ISNULL(s.StockReservado, 0) ELSE 0 END) AS libre_produccion,
           SUM(CASE WHEN s.IdAlmacen = :principal
                    THEN ISNULL(s.StockMinimo, 0) ELSE 0 END) AS minimo_principal
    FROM Articulos_Stock s
    GROUP BY s.IdArticulo
)
SELECT ob.IdOrden      AS idorden,
       ob.IdBono       AS idbono,
       ob.IdEstado     AS idestado,
       obs.IdArticulo  AS idarticulo,
       a.Descrip       AS descrip,
       ao.Descrip      AS descrip_orden,
       ob.Matricula    AS matricula,
       a_maq.Descrip   AS descrip_maquina,
       st.libre_principal  AS libre_principal,
       st.libre_produccion AS libre_produccion,
       st.libre_principal - st.minimo_principal AS sobre_minimo
FROM Ordenes o
    INNER JOIN Ordenes_Bonos ob          ON ob.IdOrden = o.IdOrden
    INNER JOIN Ordenes_Bonos_Salidas obs ON obs.IdOrden = ob.IdOrden
                                        AND obs.IdBono  = ob.IdBono
    INNER JOIN Articulos a               ON a.IdArticulo = obs.IdArticulo
    INNER JOIN Articulos ao              ON ao.IdArticulo = o.IdArticulo
    INNER JOIN stock st                  ON st.IdArticulo = o.IdArticulo
    --  LEFT: 17 de los 452 bonos vivos no declaran matrícula. Con un JOIN
    --  desaparecerían de la pantalla justo por no saber en qué máquina van.
    LEFT JOIN Articulos a_maq            ON a_maq.IdArticulo = ob.Matricula
WHERE ob.IdEstado IN (:espera, :activado, :bloqueado)
  AND (st.libre_principal < 0 OR st.libre_produccion < 0)
GROUP BY ob.IdOrden, ob.IdBono, ob.IdEstado, obs.IdArticulo, a.Descrip, ao.Descrip,
         ob.Matricula, a_maq.Descrip,
         st.libre_principal, st.libre_produccion, st.minimo_principal
ORDER BY a.Descrip
"""


#  No es /api/bonos: esa ruta es la del Consultor de Bonos (app/routers/consultor.py).
@router.get("/api/bonos-stock")
def get_bonos():
    params = dict(zip(("espera", "activado", "bloqueado"), _ESTADOS_VIVOS))
    params["principal"] = _ALMACEN_PRINCIPAL
    params["produccion"] = _ALMACEN_PRODUCCION

    bonos = [{
        "idorden":          r["idorden"],
        "idbono":           r["idbono"],
        "estado":           r["idestado"],
        "estado_label":     _ESTADO_NOMBRE.get(r["idestado"], str(r["idestado"])),
        "idarticulo":       (r["idarticulo"] or "").strip(),
        "descrip":          (r["descrip"] or "").strip(),
        "descrip_orden":    (r["descrip_orden"] or "").strip(),
        "matricula":        (r["matricula"] or "").strip(),
        "maquina":          (r["descrip_maquina"] or "").strip(),
        "libre_principal":  float(r["libre_principal"] or 0),
        "libre_produccion": float(r["libre_produccion"] or 0),
        "sobre_minimo":     float(r["sobre_minimo"] or 0),
    } for r in _erp(_SQL, params)]

    return {
        "generado":        datetime.now(),
        "bonos":           bonos,
        "total_bonos":     len(bonos),
        "total_ordenes":   len({b["idorden"] for b in bonos}),
        "total_articulos": len({b["idarticulo"] for b in bonos}),
        "por_estado":      {e: sum(1 for b in bonos if b["estado"] == e) for e in _ESTADOS_VIVOS},
    }
