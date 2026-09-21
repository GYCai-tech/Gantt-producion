"""La estimación extraída a `app.calculos.estimacion`.

El punto caliente es `minutos_montaje`: en el router lee la global mutable
`_cache_estima["montajes"]` y aquí recibe ese mismo diccionario por parámetro.
Por eso casi todas las pruebas comparan las dos versiones con la global puesta
a lo mismo que se pasa: si divergen, la extracción cambió el cálculo.
"""
from datetime import datetime

import pytest

from app.calculos import estimacion
from tests.referencia import necesita_v1, v1 as api

AHORA = datetime(2026, 9, 4, 12, 0)

MONTAJES = {
    "maquina": {"107": {"n": 20, "minutos": 220.0},   # 11 min de media
                "001": {"n": 10, "minutos": 1130.0},  # 113 min
                "999": {"n": 2,  "minutos": 400.0}},  # menos de 3 bonos: no vale
    "trabajo": {923: {"n": 5, "minutos": 250.0}},     # 50 min
}


@pytest.fixture
def montajes_en_la_global(monkeypatch):
    """Deja la caché global del router de referencia con los mismos montajes
    que se pasan por parámetro, para poder comparar contra él.

    Sin la referencia no hay nada que parchear: el código nuevo recibe
    `montajes` como argumento y no lee ninguna global."""
    if api is not None:
        monkeypatch.setitem(api._cache_estima, "montajes", MONTAJES)
    return MONTAJES


def _linea(matricula="107", idtrabajo=923, objetivo=600, **extra):
    linea = {
        "idorden": 6372, "idbono": 30, "matricula": matricula,
        "idarticulo_salida": "10501002", "idtrabajo": idtrabajo,
        "piezas_a_fabricar": objetivo,
    }
    linea.update(extra)
    return linea


def _medias(min_pieza, n=10):
    return {
        "articulo": {"10501002": {"n": n, "minutos": min_pieza * 1000, "piezas": 1000}},
        "trabajo": {}, "maquina": {},
    }


def _avance(minutos, piezas, operarios=1, montaje=0, montando=1):
    return {(6372, 30): {
        "minutos": minutos + montaje, "min_produccion": minutos,
        "min_montaje": montaje, "piezas": piezas,
        "operarios": operarios, "montando": montando,
    }}


def _item(inicio=datetime(2026, 9, 4, 11, 0)):
    return {"start": inicio, "end": AHORA, "estado": "plazo", "sin_tiempo": False}


# ── minutos_montaje: el dict por parámetro da lo mismo que la global ──

@pytest.mark.parametrize("matricula, esperado", [
    ("107", 11.0),        # histórico de la máquina, que es lo primero que se mira
    (" 001 ", 113.0),     # la matrícula llega con espacios del ERP
    ("999", 50.0),        # la máquina no llega a 3 bonos: cae al trabajo
    ("SIN", 50.0),        # sin máquina conocida, el trabajo
])
@necesita_v1
def test_minutos_montaje_por_parametro(matricula, esperado, montajes_en_la_global):
    linea = _linea(matricula=matricula)
    assert estimacion.minutos_montaje(linea, MONTAJES) == esperado
    assert estimacion.minutos_montaje(linea, MONTAJES) == api._minutos_montaje(linea)


@necesita_v1
def test_minutos_montaje_sin_historico_usa_la_media_global(montajes_en_la_global):
    linea = _linea(matricula="SIN", idtrabajo=4242)
    assert estimacion.minutos_montaje(linea, MONTAJES) == 31
    assert estimacion.minutos_montaje(linea, MONTAJES) == api._minutos_montaje(linea)


def test_minutos_montaje_no_toca_ninguna_global():
    """Dos diccionarios distintos dan dos respuestas distintas en la misma
    llamada: es lo que antes no se podía hacer con la caché global."""
    linea = _linea(matricula="107")
    otro = {"maquina": {"107": {"n": 20, "minutos": 2000.0}}, "trabajo": {}}
    assert estimacion.minutos_montaje(linea, MONTAJES) == 11.0
    assert estimacion.minutos_montaje(linea, otro) == 100.0


def test_matricula_a_none_no_revienta(montajes_en_la_global):
    linea = _linea(matricula=None)
    assert estimacion.minutos_montaje(linea, MONTAJES) == 50.0


# ── estimar ──────────────────────────────────────────────────────────

@necesita_v1
def test_estimar_prefiere_el_escandallo(montajes_en_la_global):
    teoricos = {(6372, 30): (20.0, 5.0)}
    assert (estimacion.estimar(_linea(), teoricos, _medias(5.6), MONTAJES)
            == api._estimar(_linea(), teoricos, _medias(5.6)))
    min_pieza, setup, origen = estimacion.estimar(_linea(), teoricos, _medias(5.6), MONTAJES)
    assert (min_pieza, setup, origen) == (5.0, 20.0, "teorico")


def test_el_setup_sale_del_montaje_si_el_erp_no_lo_declara(montajes_en_la_global):
    #  El escandallo trae ritmo pero no preparación: la preparación es del
    #  histórico de montajes de la máquina (11 min en la 107).
    teoricos = {(6372, 30): (0.0, 5.0)}
    assert estimacion.estimar(_linea(), teoricos, _medias(5.6), MONTAJES)[1] == 11.0


def test_escandallo_disparatado_cae_al_historico(montajes_en_la_global):
    #  345 min/pieza contra un histórico de 5,86 son 59x: se descarta.
    teoricos = {(6372, 30): (0.0, 345.0)}
    min_pieza, _, origen = estimacion.estimar(_linea(), teoricos, _medias(5.86), MONTAJES)
    assert origen == "media_articulo"
    assert round(min_pieza, 2) == 5.86


def test_sin_ningun_dato_no_hay_ritmo(montajes_en_la_global):
    vacias = {"articulo": {}, "trabajo": {}, "maquina": {}}
    min_pieza, setup, origen = estimacion.estimar(_linea(), {}, vacias, MONTAJES)
    assert (min_pieza, origen) == (None, None)
    assert setup == 11.0


# ── proyectar: la misma salida que el router, item a item ────────────

ESCENARIOS = [
    ("ritmo por piezas", _linea(), {}, _medias(5.6), _avance(3400, 360)),
    ("sin piezas declaradas", _linea(), {}, _medias(10.0), _avance(200, 0)),
    ("presupuesto agotado", _linea(objetivo=100), {}, _medias(10.0), _avance(1200, 0)),
    ("todas las piezas hechas", _linea(), {}, _medias(5.6), _avance(360 * 6.0, 360)),
    ("escandallo bueno", _linea(objetivo=50), {(6372, 30): (10.0, 5.86)}, _medias(5.86), _avance(0, 0)),
    ("escandallo disparatado", _linea(objetivo=50), {(6372, 30): (0.0, 345.0)},
     {"articulo": {}, "trabajo": {}, "maquina": {}}, _avance(0, 0)),
    ("solo media de maquina", _linea(), {},
     {"articulo": {}, "trabajo": {}, "maquina": {"107": {"n": 9, "minutos": 100, "piezas": 10}}},
     _avance(100, 10)),
    ("sin avance del bono", _linea(), {}, _medias(1), {}),
    ("cuadrilla de dos", _linea(), {}, _medias(5.6), _avance(3400, 360, operarios=2)),
    ("montaje abierto", _linea(idoperacion=1), {}, _medias(5.6), _avance(0, 0, montando=1)),
    ("montaje de cuadrilla", _linea(idoperacion=1), {}, _medias(5.6), _avance(30, 100, montando=3)),
    ("desmontaje", _linea(idoperacion=2), {}, _medias(5.6), _avance(0, 0)),
]


@pytest.mark.parametrize("caso", ESCENARIOS, ids=[c[0] for c in ESCENARIOS])
@necesita_v1
def test_proyectar_da_lo_mismo_que_el_router(caso, montajes_en_la_global):
    _, linea, teoricos, medias, avance = caso
    nuevo, viejo = _item(), _item()
    estimacion.proyectar(nuevo, linea, AHORA, teoricos, medias, MONTAJES, avance)
    api._proyectar(viejo, linea, AHORA, teoricos, medias, avance)
    assert nuevo == viejo


def test_proyectar_reparte_los_minutos_hombre(montajes_en_la_global):
    uno, dos = _item(), _item()
    estimacion.proyectar(uno, _linea(), AHORA, {}, _medias(5.6), MONTAJES,
                         _avance(3400, 360, operarios=1))
    estimacion.proyectar(dos, _linea(), AHORA, {}, _medias(5.6), MONTAJES,
                         _avance(3400, 360, operarios=2))
    assert dos["min_restantes"] == round(uno["min_restantes"] / 2)
    assert dos["min_hombre"] == uno["min_hombre"]


def test_un_montaje_deja_la_reserva_de_lo_que_falta_por_fabricar(montajes_en_la_global):
    item = _item()
    estimacion.proyectar(item, _linea(idoperacion=1, matricula="001"), AHORA, {},
                         _medias(1), MONTAJES, _avance(0, 0))
    #  113 min de montaje en la 001, empezado a las 11:00: queda hasta las 13:53.
    assert item["min_restantes"] == 53
    assert item["_pendiente"]["objetivo"] == 600
    #  Y el recurso sigue reservado más allá del montaje: 600 piezas a 1 min.
    assert item["libre_desde"] > item["end"]
