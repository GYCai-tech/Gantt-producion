"""Los bonos bloqueados en el ERP, que son los que no tiene nadie.

`Ordenes_Bonos.IdEstado = 3` es "bloqueado", y en la práctica es trabajo que
todavía no se ha repartido: de los 216 que hay, **200 no tienen ningún operario
asignado** en `Pers_EmpleadosOrdenBono`.

Esta pestaña existe porque el Gantt no puede enseñarlos. Tanto `_COLA_QUERY`
como el semáforo filtran `IdEstado = 0`, así que un bono en estado 3 no es que
salga en rojo: **no existe para la app**. Eso deja un agujero incómodo —parece
que a un área no le queda trabajo cuando en realidad lo tiene retenido— y aquí
se ve entero.

Una diferencia con la consulta original de Access: la máquina va con LEFT JOIN.
Con INNER se perdían 14 bonos que no tienen `Matricula` porque son operaciones
de fuera —Lacar, Zincar, Soldar, Pintar—, y precisamente esos son trabajo sin
asignar que hay que ver.
"""
from collections import OrderedDict
from datetime import datetime

from fastapi import APIRouter

from app.routers.api import _erp

router = APIRouter()

#  El estado de bono que el ERP usa para "bloqueado". Ver `_ESTADOS_BONO` en
#  api.py: -1 anulado, 0 espera, 1 activado, 2 finalizado, 3 bloqueado.
_ESTADO_BLOQUEADO = 3

_SQL = """
SELECT
    o.IdOrden        AS idorden,
    ao.Descrip       AS art_orden,
    o.IdArticulo     AS idart_orden,
    ob.IdBono        AS idbono,
    ob.Descrip       AS descrip_bono,
    ob.Matricula     AS matricula,
    amaq.Descrip     AS maquina,
    ob.Area          AS area,
    obs.IdArticulo   AS art_salida,
    asal.Descrip     AS descrip_salida,
    asal.ModeloArticulo AS modelo,
    -- El ModeloArticulo del articulo que sale esta vacio en los 221 bonos
    -- bloqueados: en el ERP ese campo solo esta relleno en 54 articulos de
    -- 25.815, y todos son maquinas y repuestos ("SYNCRO 41", "TruPunch
    -- 3000"). Por eso se trae tambien el de la maquina del bono, que es el
    -- unico que tiene algo que enseñar.
    amaq.ModeloArticulo AS modelo_maquina,
    ob.CantidadTotal AS cantidad,
    (SELECT COUNT(*) FROM Pers_EmpleadosOrdenBono p
      WHERE p.Orden = ob.IdOrden AND p.Bono = ob.IdBono) AS asignados
FROM Ordenes o
    INNER JOIN Articulos ao              ON ao.IdArticulo  = o.IdArticulo
    INNER JOIN Ordenes_Bonos ob          ON ob.IdOrden     = o.IdOrden
    INNER JOIN Ordenes_Bonos_Salidas obs ON obs.IdOrden    = o.IdOrden
                                        AND obs.IdBono     = ob.IdBono
    INNER JOIN Articulos asal            ON asal.IdArticulo = obs.IdArticulo
    -- LEFT y no INNER: sin maquina son las operaciones de fuera (lacado,
    -- zincado...), y son las que mas falta hace ver sin repartir.
    LEFT  JOIN Articulos amaq            ON amaq.IdArticulo = ob.Matricula
WHERE ob.IdEstado = :estado
ORDER BY o.IdOrden DESC, ob.IdBono
"""


@router.get("/api/ordenes-no-asignadas")
def get_no_asignadas():
    filas = _erp(_SQL, {"estado": _ESTADO_BLOQUEADO})

    # Agrupadas por orden, conservando el orden que trae el ERP (IdOrden
    # descendente): lo ultimo lanzado es lo que se esta mirando.
    ordenes: OrderedDict = OrderedDict()
    for r in filas:
        o = ordenes.setdefault(r["idorden"], {
            "idorden":  r["idorden"],
            "articulo": (r["idart_orden"] or "").strip(),
            "descrip":  (r["art_orden"] or "").strip(),
            "bonos":    [],
        })
        o["bonos"].append({
            "idbono":    r["idbono"],
            "descrip":   (r["descrip_bono"] or "").strip(),
            "matricula": (r["matricula"] or "").strip(),
            "maquina":   (r["maquina"] or "").strip(),
            "area":      (r["area"] or "").strip(),
            "art_salida":     (r["art_salida"] or "").strip(),
            "descrip_salida": (r["descrip_salida"] or "").strip(),
            "modelo":    (r["modelo"] or "").strip(),
            "modelo_maquina": (r["modelo_maquina"] or "").strip(),
            "cantidad":  float(r["cantidad"] or 0),
            # 16 de los 216 SÍ tienen a alguien: estan bloqueados por otro
            # motivo, y merecen distinguirse de los que no tiene nadie.
            "asignados": int(r["asignados"] or 0),
        })

    bonos = [b for o in ordenes.values() for b in o["bonos"]]
    return {
        "generado": datetime.now(),
        "ordenes": list(ordenes.values()),
        "total_ordenes": len(ordenes),
        "total_bonos": len(bonos),
        "sin_asignar": sum(1 for b in bonos if b["asignados"] == 0),
        "areas": sorted({b["area"] for b in bonos if b["area"]}),
    }
