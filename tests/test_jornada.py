"""La cola de bonos programados se coloca dentro de la jornada laboral.

Sin esto, un bono de 8 horas encolado a las 15:00 acabaría de madrugada y la
barra caería fuera del eje del Gantt, que solo pinta de 07:00 a 15:00.
"""
from datetime import datetime

from app.routers.api import _siguiente_hueco, _sumar_laborables

# 2026-09-04 es viernes; 05 y 06, fin de semana; 07, lunes.
VIERNES = datetime(2026, 9, 4)
LUNES   = datetime(2026, 9, 7)


def test_dentro_de_jornada_no_mueve_nada():
    t = VIERNES.replace(hour=10, minute=30)
    assert _siguiente_hueco(t) == t


def test_antes_de_abrir_espera_a_las_siete():
    assert _siguiente_hueco(VIERNES.replace(hour=5)) == VIERNES.replace(hour=7)


def test_despues_de_cerrar_salta_al_dia_siguiente():
    assert _siguiente_hueco(VIERNES.replace(hour=15)) == LUNES.replace(hour=7)
    assert _siguiente_hueco(VIERNES.replace(hour=21)) == LUNES.replace(hour=7)


def test_el_fin_de_semana_se_salta():
    sabado = datetime(2026, 9, 5, 9)
    domingo = datetime(2026, 9, 6, 9)
    assert _siguiente_hueco(sabado) == LUNES.replace(hour=7)
    assert _siguiente_hueco(domingo) == LUNES.replace(hour=7)


def test_lo_que_cabe_en_el_dia_se_queda_en_el_dia():
    assert _sumar_laborables(VIERNES.replace(hour=9), 90) == VIERNES.replace(hour=10, minute=30)


def test_lo_que_no_cabe_continua_el_siguiente_dia_laborable():
    # Quedan 60 min de viernes (14:00-15:00); los otros 60 van al lunes.
    assert _sumar_laborables(VIERNES.replace(hour=14), 120) == LUNES.replace(hour=8)


def test_una_jornada_entera_son_480_minutos():
    # 07:00-15:00. Medido sobre 6 meses de fichajes: las 15:00 concentran 506
    # cierres y las 16:00 solo 98, y 77 de 110 dias cierran entre 15:00 y 15:03.
    assert _sumar_laborables(VIERNES.replace(hour=7), 480) == VIERNES.replace(hour=15)
    # 481 ya no cabe: el minuto sobrante abre el lunes.
    assert _sumar_laborables(VIERNES.replace(hour=7), 481) == LUNES.replace(hour=7, minute=1)


def test_arrancar_fuera_de_hora_no_regala_tiempo():
    # A las 21:00 del viernes no queda jornada: empieza el lunes a las 7.
    assert _sumar_laborables(VIERNES.replace(hour=21), 60) == LUNES.replace(hour=8)


def test_un_bono_largo_cruza_varios_dias():
    # 960 min = dos jornadas completas: viernes entero + lunes entero.
    assert _sumar_laborables(VIERNES.replace(hour=7), 960) == LUNES.replace(hour=15)


def test_medicion_y_suma_laborable_coinciden_al_cruzar_un_fin_de_semana():
    from app.routers.api import _minutos_laborables_entre
    inicio = VIERNES.replace(hour=14)
    fin = _sumar_laborables(inicio, 600)
    assert _minutos_laborables_entre(inicio, fin) == 600
