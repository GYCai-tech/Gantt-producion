"""Bonos vivos cuyo artículo tiene el stock libre en negativo.

Es una consulta de Access pasada a SQL Server tal cual. Coge los bonos vivos
—en espera, activados o bloqueados— y se queda con los de órdenes cuyo
artículo tiene en el almacén Principal menos stock que reserva.

Dos cosas de la consulta original que se han mantenido a propósito:

- El stock es el del artículo de la ORDEN (`Ordenes.IdArticulo`), no el del
  bono. Todos los bonos de una orden salen con el mismo número aunque cada uno
  fabrique una pieza distinta.
- Se resta `StockReservado`, que es la reserva de producción. Lo que se ve es
  el stock libre, no el físico.
"""
from datetime import datetime

from fastapi import APIRouter

from app.routers.api import _erp

router = APIRouter()

#  Los estados de bono que siguen vivos: 0 en espera, 1 activado, 3 bloqueado.
_ESTADOS_VIVOS = (0, 1, 3)
_ALMACEN_PRINCIPAL = 0

#  El GROUP BY viene de la consulta de Access y hace de DISTINCT:
#  `Ordenes_Bonos_Salidas` repite fila cuando un bono declara el mismo artículo
#  más de una vez. Las condiciones del HAVING original van al WHERE porque
#  ninguna es un agregado.
_SQL = """
SELECT ob.IdOrden     AS idorden,
       ob.IdBono      AS idbono,
       obs.IdArticulo AS idarticulo,
       a.Descrip      AS descrip,
       ISNULL(s.Stock, 0) - ISNULL(s.StockReservado, 0) AS stock
FROM Ordenes o
    INNER JOIN Ordenes_Bonos ob          ON ob.IdOrden = o.IdOrden
    INNER JOIN Ordenes_Bonos_Salidas obs ON obs.IdOrden = ob.IdOrden
                                        AND obs.IdBono  = ob.IdBono
    INNER JOIN Articulos a               ON a.IdArticulo = obs.IdArticulo
    INNER JOIN Articulos_Stock s         ON s.IdArticulo = o.IdArticulo
WHERE ob.IdEstado IN (:espera, :activado, :bloqueado)
  AND s.IdAlmacen = :almacen
  AND ISNULL(s.Stock, 0) - ISNULL(s.StockReservado, 0) < 0
GROUP BY ob.IdOrden, ob.IdBono, obs.IdArticulo, a.Descrip, ob.IdEstado, s.IdAlmacen,
         ISNULL(s.Stock, 0) - ISNULL(s.StockReservado, 0)
ORDER BY a.Descrip
"""


#  No es /api/bonos: esa ruta era la del Consultor de Bonos de la v1 y
#  test_app.py vigila que no vuelva.
@router.get("/api/bonos-stock")
def get_bonos():
    params = dict(zip(("espera", "activado", "bloqueado"), _ESTADOS_VIVOS))
    params["almacen"] = _ALMACEN_PRINCIPAL
    bonos = [{
        "idorden":    r["idorden"],
        "idbono":     r["idbono"],
        "idarticulo": (r["idarticulo"] or "").strip(),
        "descrip":    (r["descrip"] or "").strip(),
        "stock":      float(r["stock"] or 0),
    } for r in _erp(_SQL, params)]

    return {
        "generado":        datetime.now(),
        "bonos":           bonos,
        "total_bonos":     len(bonos),
        "total_ordenes":   len({b["idorden"] for b in bonos}),
        "total_articulos": len({b["idarticulo"] for b in bonos}),
    }
