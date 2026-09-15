"""Reparto de la demanda y prioridad de los bonos sin liberar, sin tocar el ERP."""
from datetime import datetime

from app.routers import necesidades as nec

D1, D2, D3 = datetime(2026, 6, 1), datetime(2026, 7, 1), datetime(2026, 8, 1)


def linea(pedido, articulo, cantidad, fecha=D1, idlinea=1):
    return {"idpedido": pedido, "idlinea": idlinea, "idarticulo": articulo,
            "descrip": articulo, "cantidad": cantidad, "fecha": fecha}


def bono(orden, articulo, pendientes, estado=nec.BONO_BLOQUEADO, fecha=D1, idbono=10, objetivo=None):
    return {"idorden": orden, "idbono": idbono, "idestado": estado, "idarticulo": articulo,
            "pendientes": pendientes, "objetivo": objetivo or pendientes, "fecha_orden": fecha}


def repartir(demanda, stock=None, bonos=(), copias=None, ficha=None, entradas=None, hoy=None):
    return nec.repartir(demanda=demanda, stock=stock or {}, bonos=list(bonos),
                        copias=copias or {}, ficha=ficha or {}, entradas=entradas or {},
                        hoy=hoy or D3)


def de_linea(res, pedido, idlinea=1):
    return next(l for l in res["lineas"] if l["idpedido"] == pedido and l["idlinea"] == idlinea)


def tramos(res, nid):
    return [(t["fuente"], t["cantidad"]) for t in res["necesidades"][nid]["tramos"]]


def hijas(res, nid):
    return {n["idarticulo"]: n for n in res["necesidades"] if n["padre"] == nid}


def del_bono(res, orden, idbono=10):
    return next(b for b in res["bonos"] if b["idorden"] == orden and b["idbono"] == idbono)


# ── Stock y antigüedad en el reparto ──────────────────────────────────

def test_el_stock_sirve_primero_el_pedido_mas_antiguo():
    # Se pasan al revés para comprobar que manda la fecha, no el orden de entrada.
    res = repartir([linea(2, "A", 6, D2), linea(1, "A", 6, D1)],
                   stock={"A": {"principal": 10}})
    viejo, nuevo = de_linea(res, 1), de_linea(res, 2)

    assert tramos(res, viejo["necesidad"]) == [("principal", 6)]
    assert tramos(res, nuevo["necesidad"]) == [("principal", 4), ("sin_cubrir", 2)]
    assert (viejo["accion"], nuevo["accion"]) == ("servir", "lanzar_orden")


def test_se_cubre_en_cascada_stock_transito_bonos_y_lo_que_falta():
    res = repartir([linea(1, "A", 20)],
                   stock={"A": {"principal": 2, "recepcion": 1, "produccion": 2}},
                   bonos=[bono(7, "A", 5, nec.BONO_BLOQUEADO),
                          bono(8, "A", 4, nec.BONO_ACTIVADO)])

    assert tramos(res, de_linea(res, 1)["necesidad"]) == [
        ("principal", 2), ("transito", 3), ("bono_liberado", 4),
        ("bono_sin_liberar", 5), ("sin_cubrir", 6)]
    assert de_linea(res, 1)["accion"] == "lanzar_orden"


def test_lo_que_esta_en_transito_se_traspasa_no_se_fabrica():
    res = repartir([linea(1, "A", 5)], stock={"A": {"produccion": 5}})
    assert de_linea(res, 1)["accion"] == "traspasar"


def test_el_stock_negativo_no_cubre_nada():
    res = repartir([linea(1, "A", 3)], stock={"A": {"principal": -5}})
    assert tramos(res, de_linea(res, 1)["necesidad"]) == [("sin_cubrir", 3)]


# ── Bonos ─────────────────────────────────────────────────────────────

def test_un_bono_sin_liberar_que_cubre_un_pedido_se_propone_liberar():
    res = repartir([linea(1, "A", 5, D2)], bonos=[bono(7, "A", 8)])
    b = del_bono(res, 7)

    assert de_linea(res, 1)["accion"] == "liberar"
    assert (b["cubre"], b["sobrante"], b["via"]) == (5, 3, "directo")
    assert b["fecha_pedido_mas_antiguo"] == D2
    assert b["pedidos"] == [(1, 1)]


def test_lo_que_ya_esta_en_marcha_se_consume_antes_que_lo_que_no():
    res = repartir([linea(1, "A", 5)],
                   bonos=[bono(1, "A", 5, nec.BONO_BLOQUEADO),
                          bono(2, "A", 5, nec.BONO_ESPERA),
                          bono(3, "A", 5, nec.BONO_ACTIVADO)])
    t = res["necesidades"][de_linea(res, 1)["necesidad"]]["tramos"]
    assert [x["idorden"] for x in t] == [3]


def test_un_bono_terminado_no_cuenta():
    res = repartir([linea(1, "A", 5)], bonos=[bono(7, "A", 0)])
    assert de_linea(res, 1)["accion"] == "lanzar_orden"
    assert res["bonos"] == []


# ── Material de los bonos ─────────────────────────────────────────────

def test_la_necesidad_llega_por_la_cadena_de_bonos():
    """El bono que fabrica la pieza hereda la fecha del pedido del montaje."""
    res = repartir([linea(1, "P", 5, D1)],
                   bonos=[bono(1, "P", 5), bono(2, "M", 10, idbono=20)],
                   entradas={(1, 10): [("M", 10)]})
    pieza = del_bono(res, 2, 20)

    assert (pieza["nivel"], pieza["via"], pieza["necesidad"]) == ("sin_stock", "cadena", 10)
    assert pieza["fecha_pedido_mas_antiguo"] == D1
    assert "como material del bono 1/10" in pieza["motivo"]


def test_el_material_solo_se_toma_de_produccion():
    res = repartir([linea(1, "P", 5)],
                   stock={"M": {"principal": 100, "recepcion": 50, "produccion": 4}},
                   bonos=[bono(1, "P", 5), bono(2, "M", 10, idbono=20)],
                   entradas={(1, 10): [("M", 10)]})
    material = next(n for n in res["necesidades"] if n["tipo"] == "material")

    assert tramos(res, material["id"]) == [("produccion", 4), ("bono_sin_liberar", 6)]
    assert del_bono(res, 2, 20)["cubre"] == 6


def test_el_material_es_proporcional_a_lo_que_cubre_el_bono():
    res = repartir([linea(1, "P", 4)],
                   bonos=[bono(1, "P", 10, objetivo=10)],
                   entradas={(1, 10): [("M", 20)]})
    material = next(n for n in res["necesidades"] if n["tipo"] == "material")
    assert material["cantidad"] == 8


def test_el_material_que_falta_se_propone_lanzar():
    res = repartir([linea(1, "P", 5)],
                   bonos=[bono(1, "P", 5)],
                   entradas={(1, 10): [("M", 10)]})
    assert res["lanzar"] == [{"idarticulo": "M", "cantidad": 10, "fecha_mas_antigua": D1,
                              "lineas": [(1, 1)], "tipos": ["material"]}]


def test_un_material_que_se_contiene_a_si_mismo_no_cuelga():
    res = repartir([linea(1, "P", 1)],
                   bonos=[bono(1, "P", 1000, estado=nec.BONO_ACTIVADO)],
                   entradas={(1, 10): [("P", 1)]})
    assert max(n["nivel"] for n in res["necesidades"]) <= nec.PROFUNDIDAD_MAX


# ── Prioridad: primero el stock, después la antigüedad ────────────────

def test_nivel_de_stock_frente_a_la_necesidad():
    casos = {(0, 5): nec.SIN_PEDIDO, (10, 0): "sin_stock", (10, 5): "stock_insuficiente",
             (10, 10): "stock_suficiente", (10, 15): "stock_suficiente"}
    assert {c: nec.nivel_stock(*c) for c in casos} == casos


def test_primero_sin_stock_luego_insuficiente_luego_suficiente_y_dentro_por_antiguedad():
    res = repartir(
        [linea(1, "A", 5, D2),     # sin stock, pedido reciente
         linea(2, "E", 5, D1),     # sin stock, pedido antiguo
         linea(3, "B", 5, D1),     # stock insuficiente, aunque es antiguo
         linea(4, "C", 5, D1)],    # stock suficiente
        stock={"B": {"principal": 2}, "C": {"principal": 10}},
        bonos=[bono(10, "A", 5), bono(20, "B", 5), bono(30, "C", 5), bono(40, "E", 5),
               bono(50, "Z", 5),                                # nadie pide Z
               bono(60, "B", 1, estado=nec.BONO_ESPERA)])      # liberado: no sale en la lista

    lista = res["sin_liberar"]
    assert [b["idorden"] for b in lista] == [40, 10, 20, 30, 50]
    assert [b["nivel"] for b in lista] == ["sin_stock", "sin_stock", "stock_insuficiente",
                                           "stock_suficiente", nec.SIN_PEDIDO]
    assert [b["prioridad"] for b in lista] == [1, 2, 3, 4, None]


def test_con_stock_suficiente_sale_al_final_y_no_como_sin_pedido():
    """El stock cubre el pedido, pero el bono sigue siendo de ese artículo pedido."""
    res = repartir([linea(1, "C", 5)], stock={"C": {"principal": 10}}, bonos=[bono(1, "C", 5)])
    b = del_bono(res, 1)

    assert (b["nivel"], b["necesidad"], b["stock"], b["cobertura"]) == ("stock_suficiente", 5, 10, 2)
    assert "el stock cubre la necesidad" in b["motivo"]


def test_el_stock_cuenta_principal_recepcion_y_produccion():
    res = repartir([linea(1, "A", 10)],
                   stock={"A": {"principal": 3, "recepcion": 3, "produccion": 4}},
                   bonos=[bono(1, "A", 5)])
    assert (del_bono(res, 1)["stock"], del_bono(res, 1)["nivel"]) == (10, "stock_suficiente")


def test_la_necesidad_suma_todos_los_pedidos_del_articulo():
    res = repartir([linea(1, "A", 4, D2), linea(2, "A", 6, D1)],
                   stock={"A": {"principal": 7}}, bonos=[bono(1, "A", 5)])
    b = del_bono(res, 1)

    assert (b["necesidad"], b["nivel"], b["fecha_pedido_mas_antiguo"]) == (10, "stock_insuficiente", D1)


def test_un_bono_sin_pedido_distingue_material_de_reposicion():
    res = repartir([], bonos=[bono(1, "M", 5), bono(2, "Z", 5), bono(3, "Y", 5, idbono=30)],
                   entradas={(3, 30): [("M", 5)]})

    assert del_bono(res, 1)["tipo_sin_pedido"] == "material_para_otros_bonos"
    assert del_bono(res, 2)["tipo_sin_pedido"] == "reposicion"
    assert {b["nivel"] for b in res["bonos"]} == {nec.SIN_PEDIDO}
    assert res["resumen"]["sin_liberar_por_nivel"][nec.SIN_PEDIDO] == 3


# ── Conjuntos ─────────────────────────────────────────────────────────

def test_un_conjunto_se_desglosa_con_la_copia_de_la_linea_y_no_con_la_ficha():
    res = repartir([linea(1, "K", 2)],
                   stock={"A": {"principal": 20}},
                   copias={(1, 1): [("A", 20), ("B", 2)]},
                   ficha={"K": [("A", 99)]})
    nid = de_linea(res, 1)["necesidad"]
    comp = hijas(res, nid)

    assert tramos(res, nid) == [("desglose", 2)]
    assert tramos(res, comp["A"]["id"]) == [("principal", 20)]
    assert tramos(res, comp["B"]["id"]) == [("sin_cubrir", 2)]
    assert de_linea(res, 1)["accion"] == "lanzar_orden"


def test_un_conjunto_sin_copia_usa_la_ficha_por_unidad():
    res = repartir([linea(1, "K", 3)], ficha={"K": [("A", 2)]})
    assert hijas(res, de_linea(res, 1)["necesidad"])["A"]["cantidad"] == 6


def test_un_conjunto_con_stock_propio_solo_desglosa_lo_que_falta():
    res = repartir([linea(1, "K", 3)],
                   stock={"K": {"principal": 1}},
                   copias={(1, 1): [("A", 30)]})
    nid = de_linea(res, 1)["necesidad"]

    assert tramos(res, nid) == [("principal", 1), ("desglose", 2)]
    assert hijas(res, nid)["A"]["cantidad"] == 20


def test_un_subconjunto_se_desglosa_con_su_ficha():
    res = repartir([linea(1, "K", 1)],
                   copias={(1, 1): [("S", 2)]},
                   ficha={"S": [("C", 5)]})
    sub = hijas(res, de_linea(res, 1)["necesidad"])["S"]
    assert hijas(res, sub["id"])["C"]["cantidad"] == 10


def test_el_componente_de_un_pedido_antiguo_va_antes_que_un_pedido_nuevo():
    """El componente hereda la fecha de su pedido y compite en su sitio."""
    res = repartir([linea(2, "A", 5, D2), linea(1, "K", 1, D1)],
                   stock={"A": {"principal": 5}},
                   copias={(1, 1): [("A", 5)]})
    comp = hijas(res, de_linea(res, 1)["necesidad"])["A"]

    assert tramos(res, comp["id"]) == [("principal", 5)]
    assert de_linea(res, 2)["accion"] == "lanzar_orden"


def test_una_ficha_que_se_contiene_a_si_misma_no_cuelga():
    res = repartir([linea(1, "K", 1)], ficha={"K": [("K", 1)]})
    assert len(res["necesidades"]) == nec.PROFUNDIDAD_MAX + 1
    assert de_linea(res, 1)["accion"] == "lanzar_orden"


# ── Lectura del ERP ───────────────────────────────────────────────────

def test_la_lectura_pide_la_demanda_limpia_y_los_almacenes_acordados(monkeypatch):
    llamadas = []
    monkeypatch.setattr(nec, "_erp", lambda q, p: llamadas.append((q, p)) or [])
    nec.get_necesidades()

    demanda = next(q for q, _ in llamadas if "FROM Pedidos_Cli_Lineas l" in q)
    for filtro in ("l.IdAlbaran IS NULL", "l.IdEstado = 0", "c.IdEstado >= 0", "l.Cantidad > 0"):
        assert filtro in demanda
    _, almacenes = next((q, p) for q, p in llamadas if "Articulos_Stock" in q)
    assert sorted(almacenes.values()) == [0, 1, 2]
    _, estados = next((q, p) for q, p in llamadas if "Ordenes_Bonos ob" in q and "Salidas" in q)
    assert sorted(estados.values()) == [0, 1, 3]
    entradas, estados = next((q, p) for q, p in llamadas if "Ordenes_Bonos_Entradas" in q)
    assert "NoConsume" in entradas and "NoNecesidad" in entradas
    assert sorted(estados.values()) == [0, 1, 3]


def test_cada_almacen_va_a_su_bolsa_sin_restar_negativos(monkeypatch):
    def erp(q, p):
        if "Articulos_Stock" in q:
            return [{"idarticulo": "A ", "idalmacen": 0, "stock": 4},
                    {"idarticulo": "A ", "idalmacen": 1, "stock": -3},
                    {"idarticulo": "A ", "idalmacen": 2, "stock": 5}]
        return []
    monkeypatch.setattr(nec, "_erp", erp)

    assert nec._cargar()["stock"] == {"A": {"principal": 4.0, "recepcion": 0.0, "produccion": 5.0}}


def test_un_bono_calcula_sus_piezas_pendientes_y_lee_su_material(monkeypatch):
    def erp(q, p):
        if "Ordenes_Bonos_Entradas" in q:
            return [{"idorden": 1, "idbono": 10, "idarticulo": "M ", "cantidad": 200}]
        if "Ordenes_Bonos ob" in q:
            return [{"idorden": 1, "idbono": 10, "idestado": 3, "descrip_bono": "Cortar",
                     "matricula": "107", "maquina": "Laser", "fecha_orden": D1,
                     "idarticulo": "A", "descrip_articulo": "Pieza",
                     "objetivo": 100, "hechas": 40, "asignados": 0}]
        return []
    monkeypatch.setattr(nec, "_erp", erp)
    datos = nec._cargar()

    assert datos["bonos"][0]["pendientes"] == 60
    assert datos["entradas"] == {(1, 10): [("M", 200.0)]}


def _fila_demanda(pedido, articulo, no_inventariable):
    return {"idpedido": pedido, "idlinea": 1, "idarticulo": articulo, "descrip": articulo,
            "cantidad": 1, "fecha": D1, "idcliente": "C1", "no_inventariable": no_inventariable}


def test_los_no_inventariables_que_no_son_conjunto_quedan_fuera_pero_a_la_vista(monkeypatch):
    """Gastos de envío, genérico, chatarra: ni se fabrican ni tienen stock."""
    def erp(q, p):
        if "FROM Pedidos_Cli_Lineas l" in q:
            return [_fila_demanda(1, "0000", True),        # gastos de envío
                    _fila_demanda(2, "K", True),           # conjunto por ficha
                    _fila_demanda(3, "Q", True),           # conjunto solo por copia
                    _fila_demanda(4, "X", False)]          # artículo normal
        if "FROM Articulos_Conjuntos" in q:
            return [{"padre": "K", "idarticulo": "A", "unidades": 1}]
        if "Pedidos_Cli_Lineas_Conjuntos x" in q:
            return [{"idpedido": 3, "idlinea": 1, "idarticulo": "B", "unidades": 2}]
        return []
    monkeypatch.setattr(nec, "_erp", erp)
    datos = nec._cargar()

    assert [d["idarticulo"] for d in datos["demanda"]] == ["K", "Q", "X"]
    assert [d["idarticulo"] for d in datos["descartadas"]] == ["0000"]


def test_el_endpoint_devuelve_las_descartadas_aparte(monkeypatch):
    def erp(q, p):
        if "FROM Pedidos_Cli_Lineas l" in q:
            return [_fila_demanda(1, "0000", True)]
        return []
    monkeypatch.setattr(nec, "_erp", erp)
    res = nec.get_necesidades()

    assert res["lineas"] == []
    assert res["resumen"]["descartadas"] == 1
    assert res["descartadas"][0]["idarticulo"] == "0000"
