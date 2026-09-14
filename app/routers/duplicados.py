"""Artículos que dos órdenes vivas están fabricando a la vez.

Es la consulta de Access "Bonos duplicados", que hasta ahora había que abrir a
mano. Coge los bonos vivos —en espera, activados o bloqueados— con el artículo
que sacan, y se queda con los artículos que aparecen más de una vez.

Medido un lunes cualquiera: 31 artículos repetidos entre 66 bonos de 41
órdenes, y **los 31 son de órdenes distintas**, ninguno es un duplicado dentro
de la misma orden. La cifra baila de hora en hora, según se cierran bonos. El
caso típico es una orden vieja que se quedó a medias y otra nueva lanzada para
lo mismo: 60104070 lo tienen la 4467, con cero unidades, y la 6718, con cien.

Por eso la tabla enseña la fecha y el lote de cada orden: sin eso no se sabe
cuál es la vieja, que es justo la decisión que hay que tomar al mirarlas.

A diferencia de la consulta de Access, aquí no se pierde nada. Aquella
resolvía cada grupo con `First(IdOrden)`, que devuelve UNA fila cualquiera del
grupo y esconde la otra mitad del problema: para decidir hacen falta las dos.
"""
from collections import OrderedDict
from datetime import datetime

from fastapi import APIRouter

from app.routers.api import _erp

router = APIRouter()

#  Los estados de bono que siguen vivos: 0 en espera, 1 activado, 3 bloqueado.
#  Fuera quedan -1 anulado y 2 finalizado, que ya no compiten por nada.
_ESTADOS_VIVOS = (0, 1, 3)

_ESTADO_NOMBRE = {0: "En espera", 1: "Activado", 3: "Bloqueado"}

#  El GROUP BY no es decorativo: `Ordenes_Bonos_Salidas` repite fila cuando un
#  bono declara el mismo artículo más de una vez, igual que en el resto de
#  consultas de la app. Sin él, un bono solo se contaría como duplicado de sí
#  mismo.
_SQL = """
WITH vivos AS (
    SELECT
        ob.IdOrden                AS idorden,
        ob.IdBono                 AS idbono,
        obs.IdArticulo            AS idarticulo,
        a.Descrip                 AS descrip_articulo,
        ob.IdEstado               AS idestado,
        ob.Descrip                AS descrip_bono,
        ob.Matricula              AS matricula,
        amaq.Descrip              AS maquina,
        ob.Area                   AS area,
        o.FechaOrden              AS fecha_orden,
        o.Lote                    AS lote,
        MAX(obs.Cantidad)         AS objetivo,
        MAX(obs.CantidadTotal)    AS hechas,
        (SELECT COUNT(*) FROM Pers_EmpleadosOrdenBono p
          WHERE p.Orden = ob.IdOrden AND p.Bono = ob.IdBono)      AS asignados,
        (SELECT COUNT(*) FROM Ordenes_Bonos_Lineas l
          WHERE l.IdOrden = ob.IdOrden AND l.IdBono = ob.IdBono)  AS fichajes
    FROM Ordenes o
        INNER JOIN Ordenes_Bonos ob          ON ob.IdOrden    = o.IdOrden
        INNER JOIN Ordenes_Bonos_Salidas obs ON obs.IdOrden   = ob.IdOrden
                                            AND obs.IdBono    = ob.IdBono
        INNER JOIN Articulos a               ON a.IdArticulo  = obs.IdArticulo
        -- LEFT, como en la pestaña de no asignadas: sin máquina son las
        -- operaciones de fuera (lacado, zincado) y también se duplican.
        LEFT  JOIN Articulos amaq            ON amaq.IdArticulo = ob.Matricula
    WHERE ob.IdEstado IN (:espera, :activado, :bloqueado)
    GROUP BY ob.IdOrden, ob.IdBono, obs.IdArticulo, a.Descrip, ob.IdEstado,
             ob.Descrip, ob.Matricula, amaq.Descrip, ob.Area, o.FechaOrden, o.Lote
)
SELECT * FROM vivos
WHERE idarticulo IN (SELECT idarticulo FROM vivos GROUP BY idarticulo HAVING COUNT(*) > 1)
ORDER BY idarticulo, fecha_orden, idorden, idbono
"""


@router.get("/api/bonos-duplicados")
def get_duplicados():
    filas = _erp(_SQL, dict(zip(("espera", "activado", "bloqueado"), _ESTADOS_VIVOS)))

    grupos: OrderedDict = OrderedDict()
    for r in filas:
        g = grupos.setdefault(r["idarticulo"], {
            "articulo": (r["idarticulo"] or "").strip(),
            "descrip":  (r["descrip_articulo"] or "").strip(),
            "bonos":    [],
        })
        g["bonos"].append({
            "idorden":     r["idorden"],
            "idbono":      r["idbono"],
            "estado":      r["idestado"],
            "estado_label": _ESTADO_NOMBRE.get(r["idestado"], str(r["idestado"])),
            "descrip":     (r["descrip_bono"] or "").strip(),
            "matricula":   (r["matricula"] or "").strip(),
            "maquina":     (r["maquina"] or "").strip(),
            "area":        (r["area"] or "").strip(),
            "fecha":       r["fecha_orden"],
            "lote":        (r["lote"] or "").strip(),
            "objetivo":    float(r["objetivo"] or 0),
            "hechas":      float(r["hechas"] or 0),
            "asignados":   int(r["asignados"] or 0),
            # Un bono sin ningún fichaje no se ha tocado nunca. Es el dato que
            # separa "se está haciendo dos veces" de "se lanzó dos veces y
            # nadie ha empezado ninguna".
            "fichajes":    int(r["fichajes"] or 0),
        })

    for g in grupos.values():
        ordenes = {b["idorden"] for b in g["bonos"]}
        g["ordenes"] = len(ordenes)
        g["piezas"] = sum(b["objetivo"] for b in g["bonos"])
        g["sin_tocar"] = all(b["fichajes"] == 0 for b in g["bonos"])
        g["areas"] = sorted({b["area"] for b in g["bonos"] if b["area"]})

    lista = list(grupos.values())
    bonos = [b for g in lista for b in g["bonos"]]
    return {
        "generado": datetime.now(),
        "grupos": lista,
        "total_grupos": len(lista),
        "total_bonos": len(bonos),
        "total_ordenes": len({b["idorden"] for b in bonos}),
        "piezas": sum(b["objetivo"] for b in bonos),
        "areas": sorted({b["area"] for b in bonos if b["area"]}),
    }
