"""Tests de la logica pura del scheduler en app/routers/api.py.

No tocan la base de datos: solo prueban funciones que reciben/devuelven
datos en memoria (jornada laboral, fiabilidad de fechas, fusion de sesiones
de fichaje y el list-scheduling de bonos programados).
"""
from datetime import datetime, timedelta
from collections import defaultdict

from app.routers.api import (
    add_work_minutes,
    _prev_fiable,
    _estado_programado,
    _min_est_neto,
    _fusionar_sesiones,
    _planificar_programados,
    _estimar_fin,
)

# Martes 9:00 -- dentro de jornada, lejos de fin de semana/descanso.
MARTES_9 = datetime(2024, 1, 2, 9, 0)


class TestAddWorkMinutes:
    def test_minutos_dentro_del_mismo_tramo(self):
        inicio = datetime(2024, 1, 2, 9, 0)
        assert add_work_minutes(inicio, 60) == datetime(2024, 1, 2, 10, 0)

    def test_cero_minutos_no_avanza(self):
        inicio = datetime(2024, 1, 2, 9, 0)
        assert add_work_minutes(inicio, 0) == inicio

    def test_cruza_el_descanso_11_00_11_15(self):
        # 10:30 + 60min trabajados: 30min hasta las 11:00, salta el descanso,
        # 30min mas desde las 11:15 -> termina a las 11:45.
        inicio = datetime(2024, 1, 2, 10, 30)
        assert add_work_minutes(inicio, 60) == datetime(2024, 1, 2, 11, 45)

    def test_cruza_a_el_dia_siguiente(self):
        # 15:30 + 60min: 30min hasta las 16:00 (fin de jornada), 30min mas
        # al dia siguiente desde las 7:00 -> termina a las 7:30.
        inicio = datetime(2024, 1, 2, 15, 30)
        assert add_work_minutes(inicio, 60) == datetime(2024, 1, 3, 7, 30)

    def test_salta_el_fin_de_semana(self):
        # Viernes 15:30 + 60min: el resto de la jornada se consume el viernes,
        # el resto salta sabado/domingo y cae el lunes a las 7:30.
        viernes = datetime(2024, 1, 5, 15, 30)
        assert add_work_minutes(viernes, 60) == datetime(2024, 1, 8, 7, 30)


class TestPrevFiable:
    def test_sin_fecha_prevista_no_es_fiable(self):
        assert _prev_fiable(None, MARTES_9) is False

    def test_sin_fecha_orden_no_es_fiable(self):
        assert _prev_fiable(MARTES_9, None) is False

    def test_igual_a_fecha_orden_es_relleno_automatico_no_fiable(self):
        # El ERP rellena fecha_prevista_fin = fecha_orden cuando no hay dato real.
        assert _prev_fiable(MARTES_9, MARTES_9) is False

    def test_diferencia_menor_a_un_dia_no_es_fiable(self):
        assert _prev_fiable(MARTES_9 + timedelta(hours=12), MARTES_9) is False

    def test_diferencia_de_varios_dias_es_fiable(self):
        assert _prev_fiable(MARTES_9 + timedelta(days=5), MARTES_9) is True


class TestEstadoProgramado:
    def test_sin_fecha_prevista(self):
        assert _estado_programado(None) == "sin-estimar"

    def test_fecha_no_fiable(self):
        assert _estado_programado(MARTES_9, MARTES_9) == "sin-estimar"

    def test_fecha_fiable_en_el_pasado_es_retrasada(self):
        pasado = datetime(2020, 1, 1)
        orden = pasado - timedelta(days=10)
        assert _estado_programado(pasado, orden) == "retrasada"

    def test_fecha_fiable_en_el_futuro_es_plazo(self):
        futuro = datetime.now() + timedelta(days=365)
        orden = futuro - timedelta(days=10)
        assert _estado_programado(futuro, orden) == "plazo"


class TestMinEstNeto:
    def test_resta_lo_ya_trabajado(self):
        r = {"min_estimados": 100, "minutos_reales": 30}
        assert _min_est_neto(r) == 70

    def test_valores_ausentes_cuentan_como_cero(self):
        assert _min_est_neto({}) == 0

    def test_aplica_el_tope(self):
        r = {"min_estimados": 1000, "minutos_reales": 0}
        assert _min_est_neto(r, cap_min=500) == 500


class TestFusionarSesiones:
    def _sesion(self, inicio, fin, minutos=30):
        return {
            "recurso": "1", "idorden": 100, "idbono": 10,
            "idlinea": 1, "idnum": 1,
            "inicio": inicio, "fin": fin,
            "operacion": "Cortar", "articulo": "ART1",
            "cantidad_pedida": 10, "estado_bono": 1,
            "minutos_trabajados": minutos,
        }

    def test_funde_sesiones_contiguas(self):
        rows = [
            self._sesion(datetime(2024, 1, 2, 10, 0), datetime(2024, 1, 2, 10, 30)),
            self._sesion(datetime(2024, 1, 2, 10, 30), datetime(2024, 1, 2, 11, 0)),
        ]
        segmentos = _fusionar_sesiones(rows)
        assert len(segmentos) == 1
        assert segmentos[0]["inicio"] == datetime(2024, 1, 2, 10, 0)
        assert segmentos[0]["fin"] == datetime(2024, 1, 2, 11, 0)
        assert segmentos[0]["min_total"] == 60

    def test_no_funde_sesiones_con_hueco(self):
        rows = [
            self._sesion(datetime(2024, 1, 2, 10, 0), datetime(2024, 1, 2, 10, 30)),
            self._sesion(datetime(2024, 1, 2, 11, 0), datetime(2024, 1, 2, 11, 30)),
        ]
        segmentos = _fusionar_sesiones(rows)
        assert len(segmentos) == 2

    def test_sesion_abierta_queda_marcada(self):
        rows = [self._sesion(datetime(2024, 1, 2, 10, 0), None)]
        segmentos = _fusionar_sesiones(rows)
        assert segmentos[0]["abierto"] is True


class TestEstimarFin:
    def test_sin_estimacion_avanza_10_minutos(self):
        ahora = MARTES_9
        fin = _estimar_fin(ahora, min_est=0, min_real=0, ahora=ahora)
        assert fin == ahora + timedelta(minutes=10)

    def test_con_estimacion_proyecta_lo_que_falta(self):
        ahora = MARTES_9
        fin = _estimar_fin(ahora, min_est=60, min_real=0, ahora=ahora)
        assert fin == add_work_minutes(ahora, 60)

    def test_estimacion_ya_superada_usa_minimo_de_10_minutos(self):
        ahora = MARTES_9
        fin = _estimar_fin(ahora, min_est=60, min_real=90, ahora=ahora)
        assert fin == add_work_minutes(ahora, 10)


class TestPlanificarProgramados:
    def test_respeta_dependencia_entre_bonos_del_mismo_recurso(self):
        rows = [
            {"idorden": 1, "idbono": 10, "recurso_key": "emp:1", "min_est": 60,
             "fecha_prevista_fin": None, "fecha_orden": None},
            {"idorden": 1, "idbono": 20, "recurso_key": "emp:1", "min_est": 30,
             "fecha_prevista_fin": None, "fecha_orden": None},
        ]
        deps = {(1, 20): [(1, 10)]}
        bono_fin = {}
        next_start = defaultdict(lambda: MARTES_9)

        computed = _planificar_programados(rows, deps, bono_fin, next_start, MARTES_9)

        start10, end10 = computed[(1, 10, "emp:1")]
        start20, end20 = computed[(1, 20, "emp:1")]
        # El colchón de seguridad (ver test_nunca_arranca_en_ahora_mismo)
        # desplaza el inicio del primero +10min sobre "ahora".
        assert start10 == MARTES_9 + timedelta(minutes=10)
        assert end10 == add_work_minutes(start10, 60)
        # El dependiente no puede empezar antes de que termine su requisito.
        assert start20 >= end10
        assert end20 == add_work_minutes(start20, 30)

    def test_nunca_arranca_en_ahora_mismo(self):
        # Si el recurso está libre, next_start cae justo en "ahora" sin
        # margen -- sin colchón, la barra parecería ya en marcha en cuanto
        # el navegador tarda unos minutos en pintarla.
        rows = [
            {"idorden": 1, "idbono": 10, "recurso_key": "emp:1", "min_est": 30,
             "fecha_prevista_fin": None, "fecha_orden": None},
        ]
        computed = _planificar_programados(rows, {}, {}, defaultdict(lambda: MARTES_9), MARTES_9)
        start, end = computed[(1, 10, "emp:1")]
        assert start > MARTES_9
        assert start == MARTES_9 + timedelta(minutes=10)

    def test_bono_sin_dependencias_ni_minutos_se_omite(self):
        rows = [
            {"idorden": 1, "idbono": 10, "recurso_key": "emp:1", "min_est": 0,
             "fecha_prevista_fin": None, "fecha_orden": None},
        ]
        computed = _planificar_programados(rows, {}, {}, defaultdict(lambda: MARTES_9), MARTES_9)
        assert computed == {}
