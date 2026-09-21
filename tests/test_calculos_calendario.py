"""El calendario extraído a `app.calculos.calendario`.

Además de comprobar la jornada por su cuenta, contrasta cada función contra la
que todavía vive en el router: mientras las dos coexistan, esta prueba es la
que garantiza que la extracción no cambió una coma.
"""
from datetime import datetime, timedelta

from app.calculos import calendario
from tests.referencia import necesita_v1, v1 as api

VIERNES = datetime(2026, 9, 4, 8)
LUNES = datetime(2026, 9, 7, 8)


@necesita_v1
def test_la_jornada_es_de_siete_a_quince():
    assert (calendario.JORNADA_INICIO, calendario.JORNADA_FIN) == (7, 15)
    assert calendario.JORNADA_INICIO == api.JORNADA_INICIO
    assert calendario.JORNADA_FIN == api.JORNADA_FIN


def test_dentro_de_jornada_el_hueco_es_ahora_mismo():
    t = VIERNES.replace(hour=10, minute=30)
    assert calendario.siguiente_hueco(t) == t


def test_antes_de_abrir_espera_a_la_apertura():
    assert calendario.siguiente_hueco(VIERNES.replace(hour=5)) == VIERNES.replace(hour=7)


def test_cerrada_la_jornada_salta_al_siguiente_laborable():
    assert calendario.siguiente_hueco(VIERNES.replace(hour=15)) == LUNES.replace(hour=7)
    assert calendario.siguiente_hueco(datetime(2026, 9, 5, 9)) == LUNES.replace(hour=7)


def test_sumar_no_desborda_la_jornada():
    #  480 minutos son la jornada entera; uno más cae al día siguiente.
    assert calendario.sumar_laborables(VIERNES.replace(hour=7), 480) == VIERNES.replace(hour=15)
    assert calendario.sumar_laborables(VIERNES.replace(hour=7), 481) == LUNES.replace(hour=7, minute=1)


def test_medir_y_sumar_son_la_misma_regla():
    inicio = VIERNES.replace(hour=9)
    assert calendario.minutos_laborables_entre(inicio, calendario.sumar_laborables(inicio, 600)) == 600


@necesita_v1
def test_identica_al_router_en_una_semana_entera():
    """Barrido minuto a minuto (cada 17 min, 7 días) contra el original."""
    t = datetime(2026, 9, 4, 0, 0)
    for _ in range(600):
        assert calendario.siguiente_hueco(t) == api._siguiente_hueco(t)
        for minutos in (0, 1, 7.5, 60, 479, 480, 481, 1000):
            assert calendario.sumar_laborables(t, minutos) == api._sumar_laborables(t, minutos)
        assert (calendario.minutos_laborables_entre(t, t + timedelta(hours=30))
                == api._minutos_laborables_entre(t, t + timedelta(hours=30)))
        t += timedelta(minutes=17)
