"""La proyección de una barra abierta: cuánto le queda y si va retrasada.

Va con datos sintéticos y no toca el ERP a propósito. La rama que calcula por
piezas solo se puede ver en vivo cuando algún bono las declara, y eso pasa en
11 de 565 bonos abiertos: sin estos tests, la aritmética quedaría sin cubrir
casi siempre.
"""
from datetime import datetime

from app.routers.api import _proyectar

AHORA = datetime(2026, 9, 4, 12, 0)


def _item(inicio=datetime(2026, 9, 4, 11, 0)):
    return {"start": inicio, "end": AHORA, "estado": "plazo", "sin_tiempo": False}


def _linea(objetivo=600):
    return {
        "idorden": 6372, "idbono": 30, "matricula": "015",
        "idarticulo_salida": "10501002", "idtrabajo": 923,
        "piezas_a_fabricar": objetivo,
    }


def _medias(min_pieza, n=10):
    # Una media es minutos/piezas: se fabrica para que dé el ritmo pedido.
    return {
        "articulo": {"10501002": {"n": n, "minutos": min_pieza * 1000, "piezas": 1000}},
        "trabajo": {}, "maquina": {},
    }


def _avance(minutos, piezas, operarios=1, montaje=0):
    return {(6372, 30): {
        "minutos": minutos + montaje, "min_produccion": minutos,
        "min_montaje": montaje, "piezas": piezas, "operarios": operarios,
    }}


def test_con_piezas_declaradas_lo_que_queda_sale_de_las_piezas_pendientes():
    item = _item()
    _proyectar(item, _linea(600), AHORA, {}, _medias(5.6), _avance(minutos=3400, piezas=360))

    assert item["base_estimacion"] == "piezas"
    assert item["piezas_pendientes"] == 240
    assert item["min_restantes"] == round(240 * 5.6)      # 1.344, no "3.351 - 3.400"
    assert item["progreso_piezas"] == 60
    # 180 min el viernes, 480 lunes, 480 martes y 204 miércoles.
    # La reserva completa se conserva; el Gantt recorta la ventana visible.
    assert item["fin_estimado"] == datetime(2026, 9, 9, 10, 24)


def test_el_tiempo_que_gasto_otro_operario_no_acorta_lo_que_queda():
    """El fallo que motivó el cambio: restando minutos, un bono que cambia de
    manos le carga a quien lo tiene ahora el tiempo del compañero anterior."""
    solo = _item()
    _proyectar(solo, _linea(600), AHORA, {}, _medias(5.6), _avance(minutos=1675, piezas=360))
    compartido = _item()
    _proyectar(compartido, _linea(600), AHORA, {}, _medias(5.6), _avance(minutos=3400, piezas=360))

    # Mismas piezas hechas => mismo trabajo pendiente, gastara lo que gastara
    # quien pasó antes por el bono.
    assert solo["min_restantes"] == compartido["min_restantes"]


def test_ritmo_peor_que_el_esperado_marca_riesgo_pero_sigue_pintando_lo_que_falta():
    item = _item()
    # 3.400 min para 360 piezas = 9,44 min/pieza frente a 5,6 esperados.
    _proyectar(item, _linea(600), AHORA, {}, _medias(5.6), _avance(minutos=3400, piezas=360))

    assert item["excedido"] is True
    assert item["estado"] == "riesgo"
    assert item["min_pieza_real"] == 9.444
    assert item["min_restantes"] > 0        # ir tarde no borra el trabajo pendiente
    assert "fin_estimado" in item


def test_un_desvio_dentro_de_la_tolerancia_no_marca_riesgo():
    item = _item()
    # 6,0 min/pieza frente a 5,6: un 7 %, por debajo del 15 % de tolerancia.
    _proyectar(item, _linea(600), AHORA, {}, _medias(5.6), _avance(minutos=360 * 6.0, piezas=360))

    assert item["excedido"] is False
    assert item["estado"] == "plazo"


def test_sin_piezas_declaradas_cae_al_presupuesto_de_minutos():
    item = _item()
    _proyectar(item, _linea(100), AHORA, {}, _medias(10.0), _avance(minutos=200, piezas=0))

    assert item["base_estimacion"] == "minutos"
    assert item["min_estimados"] == 1000            # 100 piezas x 10 min
    assert item["min_restantes"] == 800             # 1.000 - 200 gastados
    assert item["excedido"] is False


def test_sin_piezas_y_pasado_de_presupuesto_marca_riesgo():
    item = _item()
    _proyectar(item, _linea(100), AHORA, {}, _medias(10.0), _avance(minutos=1200, piezas=0))

    assert item["excedido"] is True
    assert item["estado"] == "riesgo"
    assert item["min_restantes"] == 0


def test_el_tiempo_teorico_gana_a_la_media():
    item = _item()
    teoricos = {(6372, 30): (30.0, 2.0)}            # setup 30 min, 2 min/pieza
    _proyectar(item, _linea(600), AHORA, teoricos, _medias(5.6), _avance(minutos=0, piezas=0))

    assert item["origen_estimado"] == "teorico"
    assert item["min_pieza"] == 2.0
    # Sin minutos gastados el bono no ha arrancado, asi que el setup cuenta.
    assert item["min_estimados"] == 600 * 2 + 30


def test_el_setup_no_se_cobra_si_el_bono_ya_arranco():
    item = _item()
    teoricos = {(6372, 30): (30.0, 2.0)}
    _proyectar(item, _linea(600), AHORA, teoricos, _medias(5.6), _avance(minutos=100, piezas=0))

    assert item["min_estimados"] == 600 * 2          # la preparacion ya esta pagada


def test_sin_teorico_ni_media_se_marca_sin_tiempo():
    item = _item()
    vacias = {"articulo": {}, "trabajo": {}, "maquina": {}}
    _proyectar(item, _linea(600), AHORA, {}, vacias, _avance(minutos=100, piezas=0))

    assert item["sin_tiempo"] is True
    assert item["estado"] == "sin-estimar"
    assert item["end"] == AHORA                      # la barra se queda en "ahora"


def test_una_media_con_pocos_bonos_no_se_usa():
    item = _item()
    _proyectar(item, _linea(600), AHORA, {}, _medias(5.6, n=2), _avance(minutos=100, piezas=0))

    assert item["sin_tiempo"] is True                 # hacen falta al menos 3 bonos


def test_dos_operarios_a_la_vez_terminan_en_la_mitad_de_reloj():
    uno = _item()
    _proyectar(uno, _linea(600), AHORA, {}, _medias(5.6), _avance(3400, 360, operarios=1))
    dos = _item()
    _proyectar(dos, _linea(600), AHORA, {}, _medias(5.6), _avance(3400, 360, operarios=2))

    assert dos["min_restantes"] == uno["min_restantes"] // 2


def test_el_montaje_no_provoca_falsa_alerta_de_ritmo():
    item = _item()
    _proyectar(item, _linea(100), AHORA, {}, _medias(1),
               _avance(minutos=10, piezas=10, montaje=60))
    assert item['min_pieza_real'] == 1
    assert item['estado'] == 'plazo'
    assert item['min_consumidos'] == 10
    assert item['min_montaje_consumidos'] == 60
    assert item['min_restantes'] == 90


def test_sin_piezas_el_montaje_no_descuenta_presupuesto_de_produccion():
    item = _item()
    _proyectar(item, _linea(100), AHORA, {}, _medias(1),
               _avance(minutos=10, piezas=0, montaje=60))
    assert item['min_estimados'] == 100
    assert item['min_restantes'] == 90


def test_montaje_terminado_no_se_vuelve_a_cobrar_al_iniciar_produccion():
    item = _item()
    _proyectar(item, _linea(100), AHORA, {(6372, 30): (60, 1)}, _medias(1),
               _avance(minutos=0, piezas=0, montaje=60))
    assert item['min_estimados'] == 100
    assert item['min_restantes'] == 100


def test_todas_las_piezas_hechas_siguen_pendientes_de_cierre():
    item = _item()
    _proyectar(item, _linea(100), AHORA, {}, _medias(1),
               _avance(minutos=500, piezas=100, montaje=60))
    assert item['estado'] == 'pendiente-cierre'
    assert item['excedido'] is False
    assert item['min_restantes'] == 0


def test_sin_dato_de_avance_no_se_inventa_que_el_bono_acaba_de_empezar():
    item = _item()
    _proyectar(item, _linea(100), AHORA, {}, _medias(1), {})
    assert item['sin_tiempo'] is True
    assert 'fin_estimado' not in item


def test_proyeccion_fuera_de_jornada_empieza_el_siguiente_laborable():
    item = _item()
    _proyectar(item, _linea(100), AHORA.replace(hour=16), {}, _medias(1),
               _avance(minutos=10, piezas=10))
    assert item['fin_estimado'] == datetime(2026, 9, 7, 8, 30)
