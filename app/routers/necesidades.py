"""Qué bonos sin liberar hay que soltar primero, y por qué.

Parte de la demanda de clientes y la reparte, del pedido más antiguo al más
reciente, contra lo que ya existe: stock del almacén Principal, stock en
tránsito (Recepción y Producción), bonos ya liberados y bonos sin liberar.
Cuando un bono cubre una necesidad, su material pasa a ser necesidad también,
con la misma fecha de pedido. Así la demanda llega por la cadena hasta la
última pieza intermedia.

Decisiones tomadas con los datos del ERP (medido en septiembre de 2026):

· DEMANDA LIMPIA = líneas sin albarán, en estado Pedido (0), de pedidos no
  anulados y con cantidad positiva. Sin estos filtros la demanda se multiplica:
  el ERP deja sin albarán más de 2.000 líneas ANULADAS. Las líneas "Marcada
  albarán" (estado 1) quedan fuera porque la PDA ya sacó su stock al
  prepararlas ("Preparacion PDA <pedido>", 51 de 51): contarlas descontaría el
  mismo stock dos veces. Una entrega parcial parte la línea en el ERP, así que
  lo pendiente es la suma de las líneas sin albarán.

· Fuera también los artículos NO INVENTARIABLES que no son conjunto: gastos
  de envío (0000), el genérico (0), chatarra, horas de mano de obra. No se
  fabrican ni tienen stock (23 líneas). Se devuelven aparte en `descartadas`,
  porque el genérico es texto libre de un producto real y hay que revisarlo a
  mano. Un no inventariable que SÍ es conjunto se queda: es lo normal.

· ALMACENES. Lo que se vende, incluidos los componentes de los conjuntos, se
  sirve desde el Principal. Lo fabricado entra en Producción y la PDA lo lleva
  al Principal con "TRASPASO PDA", a menudo pasando por Recepción. El MATERIAL
  que consume un bono sale siempre de Producción (1.633 de 1.633 entradas de
  bonos vivos). Cuarentena no cuenta.

· StockReservado NO se usa. Son reservas de producción, no demanda: en 323
  artículos hay reserva sin ningún pedido pendiente.

· CONJUNTOS. Se cubren primero con su propio stock y sus bonos. Lo que falta
  se desglosa con la COPIA guardada en la línea del pedido (cantidades
  totales, 289 de 292 casos), no con la ficha actual, que puede haber cambiado:
  es lo que descuadró el conjunto 90110004 del pedido 12368.

· MATERIAL DE LOS BONOS. `Ordenes_Bonos_Entradas.Cantidad` es el total que
  consume el bono para su objetivo (36.245 de 37.266 casos), así que lo que
  necesita por la parte que cubre es proporcional.

· PRIORIDAD de un bono sin liberar: PRIMERO EL STOCK, DESPUÉS LA ANTIGÜEDAD.
  Arriba los bonos cuyo artículo no tiene nada de stock; después los que
  tienen stock pero no llega a la necesidad; al final los que tienen stock
  suficiente. Dentro de cada grupo, el pedido más antiguo primero. La
  necesidad de un artículo es todo lo que piden los pedidos pendientes para
  él, directamente, como componente o como material de otro bono. El stock es
  el utilizable de Principal, Recepción y Producción. Un bono cuyo artículo no
  pide nadie va aparte, separando el material para otros bonos de la
  reposición de stock.

La lógica vive en `repartir`, que es una función pura: no toca el ERP y se
prueba con datos inventados. `_cargar` solo lee y normaliza.
"""
import heapq
from datetime import datetime

from fastapi import APIRouter

from app.routers.api import _erp

router = APIRouter()

ALMACEN_PRINCIPAL = 0
ALMACEN_RECEPCION = 1
ALMACEN_PRODUCCION = 2
_BOLSA_ALMACEN = {ALMACEN_PRINCIPAL: "principal", ALMACEN_RECEPCION: "recepcion",
                  ALMACEN_PRODUCCION: "produccion"}

#  En espera (0) y activado (1) ya están en el programa de producción del
#  operario; bloqueado (3) es trabajo sin liberar a planta.
BONO_ESPERA, BONO_ACTIVADO, BONO_BLOQUEADO = 0, 1, 3

#  Orden en que se consumen los bonos de un artículo: lo que ya está en marcha
#  primero, lo que está sin liberar al final.
_ORDEN_BONO = {BONO_ACTIVADO: 0, BONO_ESPERA: 1, BONO_BLOQUEADO: 2}

#  Tope de la cadena. La más larga medida tiene 7 saltos; el tope solo evita un
#  bucle si una ficha o una entrada se contuvieran a sí mismas.
PROFUNDIDAD_MAX = 10

_EPS = 1e-6

#  De menos a más grave. La acción de una necesidad es la más grave de su árbol.
ACCIONES = ("servir", "traspasar", "en_marcha", "liberar", "lanzar_orden")
_GRAVEDAD = {a: i for i, a in enumerate(ACCIONES)}
_ACCION_FUENTE = {
    "principal": "servir",
    "produccion": "servir",        # material disponible en Producción
    "transito": "traspasar",
    "bono_liberado": "en_marcha",
    "bono_sin_liberar": "liberar",
    "sin_cubrir": "lanzar_orden",
}

#  Prioridad de un bono sin liberar: primero el stock de su artículo frente a
#  la necesidad, de peor a mejor; dentro, el pedido más antiguo.
NIVELES_STOCK = ("sin_stock", "stock_insuficiente", "stock_suficiente")
SIN_PEDIDO = "sin_pedido"
_ORDEN_NIVEL = {n: i for i, n in enumerate(NIVELES_STOCK + (SIN_PEDIDO,))}
_TEXTO_NIVEL = {
    "sin_stock": "no hay nada de stock",
    "stock_insuficiente": "el stock no llega a cubrir la necesidad",
    "stock_suficiente": "el stock cubre la necesidad",
}

_DEMANDA_SQL = """
SELECT l.IdPedido  AS idpedido,
       l.IdLinea   AS idlinea,
       l.IdArticulo AS idarticulo,
       a.Descrip   AS descrip,
       a.NoInventariable AS no_inventariable,
       l.Cantidad  AS cantidad,
       c.Fecha     AS fecha,
       c.IdCliente AS idcliente
FROM Pedidos_Cli_Lineas l
    JOIN Pedidos_Cli_Cabecera c ON c.IdPedido = l.IdPedido
    LEFT JOIN Articulos a      ON a.IdArticulo = l.IdArticulo
WHERE l.IdAlbaran IS NULL
  AND l.IdEstado = 0
  AND c.IdEstado >= 0
  AND l.Cantidad > 0
"""

#  La copia de componentes que guardó cada línea pendiente. Unidades = total de
#  la línea, no por unidad.
_COPIAS_SQL = """
SELECT x.IdPedido AS idpedido, x.IdLinea AS idlinea,
       x.IdArticulo AS idarticulo, x.Unidades AS unidades
FROM Pedidos_Cli_Lineas_Conjuntos x
    JOIN Pedidos_Cli_Lineas l  ON l.IdPedido = x.IdPedido AND l.IdLinea = x.IdLinea
    JOIN Pedidos_Cli_Cabecera c ON c.IdPedido = l.IdPedido
WHERE l.IdAlbaran IS NULL
  AND l.IdEstado = 0
  AND c.IdEstado >= 0
  AND l.Cantidad > 0
"""

#  La ficha actual de los conjuntos. Unidades = por unidad de conjunto.
_FICHA_SQL = """
SELECT IdArticuloPadre AS padre, IdArticulo AS idarticulo, Unidades AS unidades
FROM Articulos_Conjuntos
"""

_STOCK_SQL = """
SELECT IdArticulo AS idarticulo, IdAlmacen AS idalmacen, SUM(Stock) AS stock
FROM Articulos_Stock
WHERE IdAlmacen IN (:principal, :recepcion, :produccion)
GROUP BY IdArticulo, IdAlmacen
"""

#  En Ordenes_Bonos_Salidas `Cantidad` es el objetivo y `CantidadTotal` lo ya
#  producido: al revés que en Ordenes_Bonos (ver la cola en api.py).
_BONOS_SQL = """
SELECT ob.IdOrden     AS idorden,
       ob.IdBono      AS idbono,
       ob.IdEstado    AS idestado,
       ob.Descrip     AS descrip_bono,
       ob.Matricula   AS matricula,
       amaq.Descrip   AS maquina,
       o.FechaOrden   AS fecha_orden,
       obs.IdArticulo AS idarticulo,
       asal.Descrip   AS descrip_articulo,
       obs.Cantidad   AS objetivo,
       obs.CantidadTotal AS hechas,
       (SELECT COUNT(*) FROM Pers_EmpleadosOrdenBono p
         WHERE p.Orden = ob.IdOrden AND p.Bono = ob.IdBono) AS asignados
FROM Ordenes_Bonos ob
    JOIN Ordenes o                 ON o.IdOrden = ob.IdOrden
    JOIN Ordenes_Bonos_Salidas obs ON obs.IdOrden = ob.IdOrden AND obs.IdBono = ob.IdBono
    LEFT JOIN Articulos asal       ON asal.IdArticulo = obs.IdArticulo
    LEFT JOIN Articulos amaq       ON amaq.IdArticulo = ob.Matricula
WHERE ob.IdEstado IN (:espera, :activado, :bloqueado)
"""

#  El material de cada bono vivo. Cantidad = total para el objetivo del bono.
#  NoConsume y NoNecesidad marcan entradas que el propio ERP no cuenta.
_ENTRADAS_SQL = """
SELECT e.IdOrden AS idorden, e.IdBono AS idbono, e.IdArticulo AS idarticulo,
       SUM(e.Cantidad) AS cantidad
FROM Ordenes_Bonos_Entradas e
    JOIN Ordenes_Bonos ob ON ob.IdOrden = e.IdOrden AND ob.IdBono = e.IdBono
WHERE ob.IdEstado IN (:espera, :activado, :bloqueado)
  AND ISNULL(e.NoConsume, 0) = 0
  AND ISNULL(e.NoNecesidad, 0) = 0
  AND e.Cantidad > 0
GROUP BY e.IdOrden, e.IdBono, e.IdArticulo
"""


# ─────────────────────────────────────────────────────────────────────
#  El reparto
# ─────────────────────────────────────────────────────────────────────

def nivel_stock(necesidad: float, stock: float) -> str:
    """Dónde cae un artículo según su stock frente a lo que se necesita."""
    if necesidad <= _EPS:
        return SIN_PEDIDO
    if stock <= _EPS:
        return "sin_stock"
    if stock < necesidad - _EPS:
        return "stock_insuficiente"
    return "stock_suficiente"


def _componentes(necesidad: dict, falta: float, copias: dict, ficha: dict) -> list:
    """Los componentes de lo que falta de un conjunto, o [] si no lo es.

    Solo una línea de pedido (nivel 0) tiene copia guardada; el resto se
    desglosa con la ficha."""
    if necesidad["nivel"] == 0:
        copia = copias.get((necesidad["idpedido"], necesidad["idlinea"]))
        if copia:
            factor = falta / necesidad["cantidad"]
            return [(a, u * factor) for a, u in copia if u * factor > _EPS]
    return [(a, u * falta) for a, u in ficha.get(necesidad["idarticulo"], ())
            if u * falta > _EPS]


def repartir(demanda: list, stock: dict, bonos: list, copias: dict, ficha: dict,
             entradas: dict = None, hoy: datetime = None) -> dict:
    """Reparte la demanda por antigüedad contra stock y bonos. No toca el ERP.

    demanda:  [{idpedido, idlinea, idarticulo, descrip, cantidad, fecha}]
    stock:    {idarticulo: {"principal": x, "recepcion": y, "produccion": z}}
    bonos:    [{idorden, idbono, idestado, idarticulo, pendientes, objetivo, fecha_orden}]
    copias:   {(idpedido, idlinea): [(idarticulo, unidades_totales_de_la_linea)]}
    ficha:    {idarticulo_padre: [(idarticulo, unidades_por_unidad)]}
    entradas: {(idorden, idbono): [(idarticulo, cantidad_total_para_el_objetivo)]}

    Las necesidades se atienden con una cola ordenada por (fecha del pedido,
    pedido, línea, nivel). Un componente o un material heredan la clave de su
    línea, así que se atienden antes que cualquier pedido más reciente que pida
    el mismo artículo.
    """
    entradas = entradas or {}
    hoy = hoy or datetime.now()

    def bolsa(*claves):
        return {a: sum(max(0.0, s.get(c, 0.0)) for c in claves) for a, s in stock.items()}

    # "transito" a secas se acepta como Recepción, por comodidad de quien llama.
    stock_inicial = bolsa("principal", "recepcion", "produccion", "transito")
    principal = bolsa("principal")
    recepcion = bolsa("recepcion", "transito")
    produccion = bolsa("produccion")

    vivos = [b for b in bonos if b["pendientes"] > _EPS]
    bolsas_bono: dict = {}
    for b in sorted(vivos, key=lambda b: (_ORDEN_BONO.get(b["idestado"], 9),
                                          b["fecha_orden"] or datetime.max,
                                          b["idorden"], b["idbono"])):
        bolsas_bono.setdefault(b["idarticulo"], []).append(b)
    queda_bono = {(b["idorden"], b["idbono"]): b["pendientes"] for b in vivos}
    cubre: dict = {}

    necesidades: list = []
    hijos: dict = {}
    cola: list = []

    def encolar(idarticulo, cantidad, fecha, linea, padre, nivel, tipo, descrip=None, bono_origen=None):
        nid = len(necesidades)
        necesidades.append({
            "id": nid, "padre": padre, "nivel": nivel, "tipo": tipo,
            "idpedido": linea[0], "idlinea": linea[1],
            "idarticulo": idarticulo, "descrip": descrip,
            "cantidad": cantidad, "fecha": fecha, "bono_origen": bono_origen, "tramos": [],
        })
        if padre is not None:
            hijos.setdefault(padre, []).append(nid)
        heapq.heappush(cola, (fecha, linea[0], linea[1], nivel, nid))
        return nid

    def anotar(n, fuente, cantidad, **extra):
        ultimo = n["tramos"][-1] if n["tramos"] else None
        if ultimo and not extra and ultimo["fuente"] == fuente and len(ultimo) == 2:
            ultimo["cantidad"] += cantidad
        else:
            n["tramos"].append(dict(fuente=fuente, cantidad=cantidad, **extra))

    lineas: dict = {}
    for d in demanda:
        clave = (d["idpedido"], d["idlinea"])
        nid = encolar(d["idarticulo"], d["cantidad"], d["fecha"], clave, None, 0, "venta", d.get("descrip"))
        lineas[clave] = dict(d, necesidad=nid)

    while cola:
        nid = heapq.heappop(cola)[-1]
        n = necesidades[nid]
        art, falta = n["idarticulo"], n["cantidad"]
        linea = (n["idpedido"], n["idlinea"])

        if n["tipo"] == "venta":
            fuentes = (("principal", principal), ("transito", recepcion), ("transito", produccion))
        else:
            fuentes = (("produccion", produccion),)
        for fuente, disponible in fuentes:
            x = min(falta, disponible.get(art, 0.0))
            if x > _EPS:
                disponible[art] -= x
                falta -= x
                anotar(n, fuente, x)

        for b in bolsas_bono.get(art, ()):
            if falta <= _EPS:
                break
            k = (b["idorden"], b["idbono"])
            x = min(falta, queda_bono[k])
            if x <= _EPS:
                continue
            queda_bono[k] -= x
            falta -= x
            liberado = b["idestado"] != BONO_BLOQUEADO
            anotar(n, "bono_liberado" if liberado else "bono_sin_liberar", x,
                   idorden=b["idorden"], idbono=b["idbono"])
            cubre.setdefault(k, []).append({
                "necesidad": nid, "cantidad": x, "fecha": n["fecha"],
                "idpedido": n["idpedido"], "idlinea": n["idlinea"],
            })
            # Lo que cubre este bono tira de su material, con la misma fecha.
            objetivo = b.get("objetivo") or b["pendientes"]
            if n["nivel"] < PROFUNDIDAD_MAX and objetivo > _EPS:
                for material, total in entradas.get(k, ()):
                    cant = x * total / objetivo
                    if cant > _EPS:
                        encolar(material, cant, n["fecha"], linea, nid, n["nivel"] + 1,
                                "material", bono_origen=k)

        if falta > _EPS:
            componentes = (_componentes(n, falta, copias, ficha)
                           if n["nivel"] < PROFUNDIDAD_MAX else [])
            if componentes:
                anotar(n, "desglose", falta)
                for comp, cant in componentes:
                    encolar(comp, cant, n["fecha"], linea, nid, n["nivel"] + 1, n["tipo"])
            else:
                anotar(n, "sin_cubrir", falta)

    # La acción de cada necesidad es la más grave de sus tramos y de sus hijas.
    # Las hijas se crean después que su madre: recorrer al revés las da resueltas.
    for n in reversed(necesidades):
        acciones = [_ACCION_FUENTE[t["fuente"]] for t in n["tramos"] if t["fuente"] in _ACCION_FUENTE]
        acciones += [necesidades[h]["accion"] for h in hijos.get(n["id"], ())]
        n["accion"] = max(acciones, key=_GRAVEDAD.get) if acciones else "servir"
        for t in n["tramos"]:
            t["cantidad"] = round(t["cantidad"], 4)
        n["cantidad"] = round(n["cantidad"], 4)

    salida_lineas = sorted(
        (dict(d, accion=necesidades[d["necesidad"]]["accion"]) for d in lineas.values()),
        key=lambda l: (l["fecha"], l["idpedido"], l["idlinea"]))

    # Necesidad total de cada artículo y el pedido más antiguo que lo pide.
    necesidad_art: dict = {}
    mas_antigua_art: dict = {}
    for n in necesidades:
        a = n["idarticulo"]
        necesidad_art[a] = necesidad_art.get(a, 0.0) + n["cantidad"]
        previa = mas_antigua_art.get(a)
        if previa is None or ((n["fecha"], n["idpedido"], n["idlinea"])
                              < (previa["fecha"], previa["idpedido"], previa["idlinea"])):
            mas_antigua_art[a] = n

    consumidos = {material for lista in entradas.values() for material, _ in lista}
    salida_bonos = [
        _bono_clasificado(b, cubre.get((b["idorden"], b["idbono"]), []), consumidos, hoy,
                          necesidad_art.get(b["idarticulo"], 0.0),
                          stock_inicial.get(b["idarticulo"], 0.0),
                          mas_antigua_art.get(b["idarticulo"]))
        for b in vivos]
    salida_bonos.sort(key=lambda b: (_ORDEN_NIVEL[b["nivel"]],
                                     b["fecha_pedido_mas_antiguo"] or datetime.max,
                                     b["idorden"], b["idbono"]))
    sin_liberar = [b for b in salida_bonos if not b["liberado"]]
    for i, b in enumerate((b for b in sin_liberar if b["nivel"] != SIN_PEDIDO), 1):
        b["prioridad"] = i

    faltas: dict = {}
    for n in necesidades:
        for t in n["tramos"]:
            if t["fuente"] != "sin_cubrir":
                continue
            f = faltas.setdefault(n["idarticulo"], {
                "idarticulo": n["idarticulo"], "cantidad": 0.0,
                "fecha_mas_antigua": n["fecha"], "lineas": set(), "tipos": set(),
            })
            f["cantidad"] += t["cantidad"]
            f["fecha_mas_antigua"] = min(f["fecha_mas_antigua"], n["fecha"])
            f["lineas"].add((n["idpedido"], n["idlinea"]))
            f["tipos"].add(n["tipo"])
    lanzar = sorted(
        (dict(f, cantidad=round(f["cantidad"], 4), lineas=sorted(f["lineas"]), tipos=sorted(f["tipos"]))
         for f in faltas.values()),
        key=lambda f: (f["fecha_mas_antigua"], f["idarticulo"]))

    return {
        "sin_liberar": sin_liberar,
        "lineas": salida_lineas,
        "necesidades": necesidades,
        "bonos": salida_bonos,
        "lanzar": lanzar,
        "resumen": {
            "lineas": len(salida_lineas),
            "por_accion": {a: sum(1 for l in salida_lineas if l["accion"] == a) for a in ACCIONES},
            "sin_liberar": len(sin_liberar),
            "sin_liberar_por_nivel": {
                nivel: sum(1 for b in sin_liberar if b["nivel"] == nivel)
                for nivel in NIVELES_STOCK + (SIN_PEDIDO,)},
            "articulos_sin_cubrir": len(lanzar),
        },
    }


def _bono_clasificado(b: dict, cubiertas: list, consumidos: set, hoy: datetime,
                      necesidad: float, stock: float, mas_antigua: dict) -> dict:
    """El bono con su nivel de stock, su pedido más antiguo y el motivo."""
    cubierto = sum(c["cantidad"] for c in cubiertas)
    nivel = nivel_stock(necesidad, stock) if mas_antigua else SIN_PEDIDO

    if nivel == SIN_PEDIDO:
        fecha = dias = via = None
        tipo_sin_pedido = "material_para_otros_bonos" if b["idarticulo"] in consumidos else "reposicion"
        motivo = ("Ningún pedido pide este artículo: fabrica material para otros bonos que tampoco llegan a un pedido."
                  if tipo_sin_pedido == "material_para_otros_bonos" else
                  "Ningún pedido pide este artículo ni lo consume ningún bono: reposición de stock.")
    else:
        fecha = mas_antigua["fecha"]
        dias = (hoy - fecha).days
        tipo_sin_pedido = None
        via = "directo" if mas_antigua["tipo"] == "venta" else "cadena"
        como = ("" if via == "directo" else
                " como material del bono {}/{}".format(*mas_antigua["bono_origen"]))
        motivo = (f"Pedido más antiguo {mas_antigua['idpedido']}-{mas_antigua['idlinea']} del "
                  f"{fecha:%d/%m/%Y}, hace {dias} días{como}. "
                  f"Necesidad {necesidad:g}, stock {stock:g}: {_TEXTO_NIVEL[nivel]}.")

    return dict(
        b,
        liberado=b["idestado"] != BONO_BLOQUEADO,
        nivel=nivel,
        prioridad=None,
        necesidad=round(necesidad, 4),
        stock=round(stock, 4),
        cobertura=round(stock / necesidad, 4) if necesidad > _EPS else None,
        fecha_pedido_mas_antiguo=fecha,
        dias=dias,
        via=via,
        tipo_sin_pedido=tipo_sin_pedido,
        cubre=round(cubierto, 4),
        sobrante=round(max(0.0, b["pendientes"] - cubierto), 4),
        pedidos=sorted({(c["idpedido"], c["idlinea"]) for c in cubiertas}),
        para_stock=cubierto <= _EPS,
        motivo=motivo,
    )


# ─────────────────────────────────────────────────────────────────────
#  Lectura del ERP
# ─────────────────────────────────────────────────────────────────────

def _txt(v) -> str:
    return (v or "").strip()


def _num(v) -> float:
    return float(v or 0)


def _cargar() -> dict:
    """Lee y normaliza lo que necesita `repartir`. Solo SELECT."""
    estados = {"espera": BONO_ESPERA, "activado": BONO_ACTIVADO, "bloqueado": BONO_BLOQUEADO}

    demanda = [{
        "idpedido": r["idpedido"], "idlinea": r["idlinea"],
        "idarticulo": _txt(r["idarticulo"]), "descrip": _txt(r["descrip"]),
        "cantidad": _num(r["cantidad"]), "fecha": r["fecha"],
        "idcliente": _txt(r["idcliente"]),
        "no_inventariable": bool(r["no_inventariable"]),
    } for r in _erp(_DEMANDA_SQL, {})]

    copias: dict = {}
    for r in _erp(_COPIAS_SQL, {}):
        copias.setdefault((r["idpedido"], r["idlinea"]), []).append(
            (_txt(r["idarticulo"]), _num(r["unidades"])))

    ficha: dict = {}
    for r in _erp(_FICHA_SQL, {}):
        ficha.setdefault(_txt(r["padre"]), []).append((_txt(r["idarticulo"]), _num(r["unidades"])))

    # Un no inventariable que no es conjunto no se fabrica ni tiene stock:
    # gastos de envío, el genérico, chatarra, horas. Fuera, pero a la vista.
    descartadas = [d for d in demanda
                   if d["no_inventariable"] and d["idarticulo"] not in ficha
                   and (d["idpedido"], d["idlinea"]) not in copias]
    fuera = {(d["idpedido"], d["idlinea"]) for d in descartadas}
    demanda = [d for d in demanda if (d["idpedido"], d["idlinea"]) not in fuera]

    # Cada almacén se recorta a cero por separado: un negativo en Recepción no
    # puede comerse el stock real que hay en Producción.
    stock: dict = {}
    for r in _erp(_STOCK_SQL, {"principal": ALMACEN_PRINCIPAL,
                               "recepcion": ALMACEN_RECEPCION,
                               "produccion": ALMACEN_PRODUCCION}):
        s = stock.setdefault(_txt(r["idarticulo"]), {"principal": 0.0, "recepcion": 0.0, "produccion": 0.0})
        s[_BOLSA_ALMACEN[r["idalmacen"]]] += max(0.0, _num(r["stock"]))

    bonos = [{
        "idorden": r["idorden"], "idbono": r["idbono"], "idestado": r["idestado"],
        "descrip": _txt(r["descrip_bono"]), "matricula": _txt(r["matricula"]),
        "maquina": _txt(r["maquina"]), "fecha_orden": r["fecha_orden"],
        "idarticulo": _txt(r["idarticulo"]), "descrip_articulo": _txt(r["descrip_articulo"]),
        "objetivo": _num(r["objetivo"]), "hechas": _num(r["hechas"]),
        "pendientes": max(0.0, _num(r["objetivo"]) - _num(r["hechas"])),
        "asignados": int(r["asignados"] or 0),
    } for r in _erp(_BONOS_SQL, estados)]

    entradas: dict = {}
    for r in _erp(_ENTRADAS_SQL, estados):
        entradas.setdefault((r["idorden"], r["idbono"]), []).append(
            (_txt(r["idarticulo"]), _num(r["cantidad"])))

    return {"demanda": demanda, "stock": stock, "bonos": bonos, "copias": copias,
            "ficha": ficha, "entradas": entradas, "descartadas": descartadas}


@router.get("/api/necesidades")
def get_necesidades():
    datos = _cargar()
    descartadas = datos.pop("descartadas")
    resultado = repartir(**datos)
    resultado["descartadas"] = descartadas
    resultado["resumen"]["descartadas"] = len(descartadas)
    resultado["generado"] = datetime.now()
    return resultado
