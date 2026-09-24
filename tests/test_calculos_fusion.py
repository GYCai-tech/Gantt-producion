"""La fusión de barras extraída a `app.calculos.fusion`.

Estas funciones MUTAN los items que absorben, así que cada comparación con el
router parte de una copia propia: si compartieran la lista, la segunda llamada
vería el trabajo de la primera y la prueba no diría nada.
"""
import copy
from datetime import datetime, timedelta

import pytest

from app.calculos import fusion
from tests.referencia import necesita_v1, v1 as api

AHORA = datetime(2026, 9, 7, 12)


def barra(inicio_min, dur_min_, tipo="trabajado", montaje=False, recurso="1",
          bono=10, en_curso=False, **extra):
    inicio = datetime(2026, 9, 7, 9) + timedelta(minutes=inicio_min)
    it = {
        "id": f"1-{bono}-{inicio_min}",
        "idorden": 1, "idbono": bono, "recurso_id": recurso,
        "tipo": tipo, "estado": "completado", "es_montaje": montaje,
        "en_curso": en_curso,
        "start": inicio, "end": inicio + timedelta(minutes=dur_min_),
        "min_real": dur_min_,
        "art": "Articulo", "art_id": "A", "area": "CHAPA",
        "operacion": "Maquina M1", "operarios": "Operario 1", "piezas": 100,
    }
    it.update(extra)
    return it


CASOS = {
    #  30 min de producción y detrás dos rayas de 1 min: la misma sesión.
    "trozos seguidos": [barra(0, 30), barra(31, 1), barra(33, 1)],
    "trozo delante de un trabajo largo": [barra(0, 1), barra(2, 40)],
    "hueco demasiado grande": [barra(0, 30), barra(45, 1)],
    "dos barras largas no se funden": [barra(0, 30), barra(31, 30)],
    "operarios distintos no se funden": [barra(0, 30), barra(31, 1, recurso="2")],
    "montaje y produccion no se mezclan aqui": [barra(0, 10, montaje=True), barra(11, 1)],
    "el trozo abierto manda": [barra(0, 30), barra(31, 1, tipo="real", en_curso=True,
                                                  min_restantes=45)],
    "montaje pegado a su produccion": [barra(0, 20, montaje=True), barra(21, 60)],
    "montaje de otro dia": [barra(0, 20, montaje=True), barra(600, 60)],
    "montaje sin produccion detras": [barra(0, 20, montaje=True)],
    "produccion en curso tras el montaje": [barra(0, 20, montaje=True),
                                            barra(21, 60, tipo="real", en_curso=True)],
}


@pytest.mark.parametrize("nombre", list(CASOS))
@necesita_v1
def test_fundir_da_lo_mismo_que_el_router(nombre):
    nuevos, viejos = copy.deepcopy(CASOS[nombre]), copy.deepcopy(CASOS[nombre])
    assert (fusion.fundir_montaje(fusion.fundir_troceados(nuevos))
            == api._fundir_montaje(api._fundir_troceados(viejos)))


@necesita_v1
def test_dur_min_mide_la_barra():
    it = barra(0, 45)
    assert fusion.dur_min(it) == 45.0 == api._dur_min(it)


def test_los_trozos_se_funden_en_una_sola_barra():
    salida = fusion.fundir_troceados(copy.deepcopy(CASOS["trozos seguidos"]))
    assert len(salida) == 1
    #  El ancho llega hasta el último trozo; los minutos REALES se suman sin
    #  contar los huecos que no se trabajaron.
    assert salida[0]["end"] == datetime(2026, 9, 7, 9, 34)
    assert salida[0]["min_real"] == 32


def test_el_montaje_pegado_se_marca_dentro_de_la_produccion():
    salida = fusion.fundir_montaje(copy.deepcopy(CASOS["montaje pegado a su produccion"]))
    assert len(salida) == 1
    assert salida[0]["start"] == datetime(2026, 9, 7, 9)
    assert salida[0]["min_montaje"] == 20
    #  20 minutos de los 81 que mide la barra fundida (hay un minuto de hueco
    #  entre el fin del montaje y el arranque de la producción).
    assert salida[0]["pct_montaje"] == 24.7


def test_el_montaje_de_otro_dia_se_queda_aparte():
    assert len(fusion.fundir_montaje(copy.deepcopy(CASOS["montaje de otro dia"]))) == 2


# ── continuar ────────────────────────────────────────────────────────

def _pendiente(minutos=300.0, origen="media_articulo", objetivo=600.0, hechas=0.0,
               min_pieza=0.5, min_hombre=300.0, a_la_vez=1):
    return {"minutos": minutos, "origen": origen, "objetivo": objetivo,
            "hechas": hechas, "min_pieza": min_pieza, "min_hombre": min_hombre,
            "a_la_vez": a_la_vez}


CASOS_CONTINUAR = {
    "con reserva conocida": [barra(0, 20, montaje=True, _pendiente=_pendiente(),
                                   libre_desde=datetime(2026, 9, 8, 10))],
    "sin libre_desde": [barra(0, 20, montaje=True, _pendiente=_pendiente())],
    "sin dimensionar": [barra(0, 20, montaje=True,
                              _pendiente=_pendiente(minutos=None, min_hombre=None))],
    "ya no queda nada": [barra(0, 20, montaje=True, _pendiente=_pendiente(minutos=0.0))],
    "ritmo poco fiable": [barra(0, 20, montaje=True,
                                _pendiente=_pendiente(origen="media_maquina"))],
    "sin pendiente": [barra(0, 20, montaje=True)],
}


@pytest.mark.parametrize("nombre", list(CASOS_CONTINUAR))
@necesita_v1
def test_continuar_da_lo_mismo_que_el_router(nombre):
    nuevos, viejos = copy.deepcopy(CASOS_CONTINUAR[nombre]), copy.deepcopy(CASOS_CONTINUAR[nombre])
    #  `matricula` no está en la v1: la continuación empezó a heredarla para
    #  que el Gantt la ponga en el carril de SU máquina y no le abra uno
    #  propio. Se compara todo lo demás, que es lo que no puede moverse.
    salida = [{k: v for k, v in b.items() if k != "matricula"}
              for b in fusion.continuar(nuevos, AHORA)]
    assert salida == api._continuar(viejos, AHORA)
    #  Y en los dos casos la clave interna se consume: no llega al frontend.
    assert all("_pendiente" not in it for it in nuevos)


def test_la_barra_de_continuacion_arranca_donde_acaba_el_montaje():
    barras = fusion.continuar(copy.deepcopy(CASOS_CONTINUAR["con reserva conocida"]), AHORA)
    assert len(barras) == 1
    #  El montaje acabó a las 09:20, antes de `ahora`: la continuación arranca
    #  en `ahora` y llega hasta la reserva que dejó la proyección.
    assert barras[0]["start"] == AHORA
    assert barras[0]["end"] == datetime(2026, 9, 8, 10)
    assert barras[0]["tipo"] == "programado"
    assert barras[0]["estado"] == "continuacion"


def test_sin_dimensionar_se_dibuja_el_bloque_nominal():
    barras = fusion.continuar(copy.deepcopy(CASOS_CONTINUAR["sin dimensionar"]), AHORA)
    assert barras[0]["fin_indeterminado"] is True
    assert barras[0]["estado"] == "sin-estimar"
    assert barras[0]["end"] == AHORA + timedelta(minutes=fusion.MIN_BLOQUE_SIN_TIEMPO)


def test_dimensionada_en_cero_no_dibuja_nada():
    assert fusion.continuar(copy.deepcopy(CASOS_CONTINUAR["ya no queda nada"]), AHORA) == []
