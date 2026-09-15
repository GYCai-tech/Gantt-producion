"""Bonos vivos de órdenes cuyo artículo tiene el stock libre en negativo.

Es una consulta de Access pasada a SQL Server tal cual. Coge los bonos vivos
—en espera, activados o bloqueados— y se queda con los de órdenes cuyo
artículo tiene en el almacén Principal menos stock que reserva.

Cada fila junta dos artículos, y conviene no confundirlos:

- El del BONO (`Ordenes_Bonos_Salidas.IdArticulo`): lo que fabrica ese bono,
  que puede ser una pieza intermedia de Producción, como una varilla cortada.
- El de la ORDEN (`Ordenes.IdArticulo`): el producto final. Su stock es el
  que se filtra y se enseña, y por eso todos los bonos de una orden salen con
  el mismo número. El -20 de la varilla 60007034 es el del frente de jaulón
  que fabrica su orden, no el de la varilla.

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

#  El GROUP BY viene de la consulta de Access y hace de DISTINCT:
#  `Ordenes_Bonos_Salidas` repite fila cuando un bono declara el mismo artículo
#  más de una vez. Las condiciones del HAVING original van al WHERE porque
#  ninguna es un agregado. `IdEstado` ya estaba en el GROUP BY, así que sacarlo
#  en el SELECT para poder filtrar no cambia las filas.
_SQL = """
SELECT ob.IdOrden     AS idorden,
       ob.IdBono      AS idbono,
       ob.IdEstado    AS idestado,
       obs.IdArticulo AS idarticulo,
       a.Descrip      AS descrip,
       s.IdAlmacen    AS idalmacen,
       ISNULL(s.Stock, 0) - ISNULL(s.StockReservado, 0) AS stock,
       ao.Descrip     AS descrip_orden,
       ISNULL(s.Stock, 0) - ISNULL(s.StockReservado, 0) - ISNULL(s.StockMinimo, 0) AS stock_sobre_minimo
FROM Ordenes o
    INNER JOIN Ordenes_Bonos ob          ON ob.IdOrden = o.IdOrden
    INNER JOIN Ordenes_Bonos_Salidas obs ON obs.IdOrden = ob.IdOrden
                                        AND obs.IdBono  = ob.IdBono
    INNER JOIN Articulos a               ON a.IdArticulo = obs.IdArticulo
    INNER JOIN Articulos_Stock s         ON s.IdArticulo = o.IdArticulo
    INNER JOIN Articulos ao              ON ao.IdArticulo = s.IdArticulo
WHERE ob.IdEstado IN (:espera, :activado, :bloqueado)
  AND s.IdAlmacen = :almacen
  AND ISNULL(s.Stock, 0) - ISNULL(s.StockReservado, 0) < 0
GROUP BY ob.IdOrden, ob.IdBono, obs.IdArticulo, a.Descrip, ob.IdEstado, s.IdAlmacen,
         ISNULL(s.Stock, 0) - ISNULL(s.StockReservado, 0), ao.Descrip,
         ISNULL(s.Stock, 0) - ISNULL(s.StockReservado, 0) - ISNULL(s.StockMinimo, 0)
ORDER BY a.Descrip
"""


#  No es /api/bonos: esa ruta era la del Consultor de Bonos de la v1 y
#  test_app.py vigila que no vuelva.
@router.get("/api/bonos-stock")
def get_bonos():
    params = dict(zip(("espera", "activado", "bloqueado"), _ESTADOS_VIVOS))
    params["almacen"] = _ALMACEN_PRINCIPAL
    bonos = [{
        "idorden":            r["idorden"],
        "idbono":             r["idbono"],
        "estado":             r["idestado"],
        "estado_label":       _ESTADO_NOMBRE.get(r["idestado"], str(r["idestado"])),
        "idarticulo":         (r["idarticulo"] or "").strip(),
        "descrip":            (r["descrip"] or "").strip(),
        "idalmacen":          r["idalmacen"],
        "stock":              float(r["stock"] or 0),
        "descrip_orden":      (r["descrip_orden"] or "").strip(),
        "stock_sobre_minimo": float(r["stock_sobre_minimo"] or 0),
    } for r in _erp(_SQL, params)]

    return {
        "generado":        datetime.now(),
        "bonos":           bonos,
        "total_bonos":     len(bonos),
        "total_ordenes":   len({b["idorden"] for b in bonos}),
        "total_articulos": len({b["idarticulo"] for b in bonos}),
        "por_estado":      {e: sum(1 for b in bonos if b["estado"] == e) for e in _ESTADOS_VIVOS},
    }
