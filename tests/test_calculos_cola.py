"""La planificación de cola extraída a `app.calculos.cola`.

Cada caso se corre dos veces —módulo nuevo y router— y se comparan las dos
salidas. Es la única forma de asegurar que sacar `montajes` a parámetro no
movió ningún bono de sitio.
"""
from datetime import datetime

import pytest

from app.calculos import cola as calc_cola
from tests.referencia import necesita_v1, v1 as api

AHORA = datetime(2026, 9, 7, 12)
HASTA = datetime(2026, 9, 11, 15)
MEDIAS = {"articulo": {}, "trabajo": {}, "maquina": {}}
MONTAJES = {"maquina": {"M1": {"n": 8, "minutos": 160.0}}, "trabajo": {}}


@pytest.fixture(autouse=True)
def montajes_en_la_global(monkeypatch):
    """Solo hace falta para el router de referencia: el código nuevo recibe
    `montajes` por parámetro y no lee ninguna global."""
    if api is not None:
        monkeypatch.setitem(api._cache_estima, "montajes", MONTAJES)


def bono(orden=1, empleado=1, maquina="M1", cantidad=100, semaforo="disponible",
         secuencia=1, arrancado=False, hechas=0, montado=False):
    return {
        "idorden": orden, "idbono": 10, "idempleado": empleado,
        "empleado": f"Operario {empleado}", "matricula": maquina,
        "descrip_maquina": f"Maquina {maquina}", "idtrabajo": 1,
        "descrip_salida": "Articulo", "idarticulo_salida": "A",
        "area": "CHAPA",
        "piezas_a_fabricar": cantidad, "fabricadas": hechas,
        "ordenar": secuencia, "semaforo": semaforo,
        "arrancado": arrancado, "montado": montado,
        "ultimo_fichaje": datetime(2026, 9, 7, 9) if arrancado else None,
    }


def _teoricos(cola):
    return {(b["idorden"], b["idbono"]): (1, 1) for b in cola}


def plan(cola, ocupado=None, hasta=HASTA):
    return calc_cola.planificar_cola(cola, ocupado or {}, hasta, AHORA,
                                     _teoricos(cola), MEDIAS, MONTAJES)


def _comparable(tareas):
    #  `bono` y `asignados` son los mismos diccionarios de entrada; lo que
    #  interesa comparar es la colocación y sus cifras.
    return [{k: v for k, v in t.items() if k not in ("bono", "asignados")}
            for t in tareas]


CASOS = {
    "dos operarios en la misma maquina": [bono(1, 1), bono(2, 2)],
    "un operario en dos maquinas": [bono(1, 1, "M1"), bono(2, 1, "M2")],
    "recursos independientes": [bono(1, 1, "M1"), bono(2, 2, "M2")],
    "bono compartido por dos": [bono(1, 1), bono(1, 2), bono(2, 2, "M2")],
    "cuadrilla de tres": [bono(1, 1), bono(1, 2), bono(1, 3)],
    "bloqueado detras del disponible": [bono(1, semaforo="bloqueada"), bono(2, secuencia=20)],
    "reanudado por delante": [bono(1, secuencia=9, arrancado=True, hechas=10),
                              bono(2, secuencia=1)],
    "ya montado no paga preparacion": [bono(1, montado=True), bono(2)],
    "sin piezas pendientes": [bono(1, cantidad=10, hechas=10), bono(2)],
    "sin maquina": [bono(1, maquina=None), bono(2, maquina=None, empleado=2)],
}


@pytest.mark.parametrize("nombre", list(CASOS))
@necesita_v1
def test_planificar_cola_da_lo_mismo_que_el_router(nombre):
    entrada = CASOS[nombre]
    nuevo = calc_cola.planificar_cola(entrada, {}, HASTA, AHORA,
                                      _teoricos(entrada), MEDIAS, MONTAJES)
    viejo = api._planificar_cola(entrada, {}, HASTA, AHORA, _teoricos(entrada), MEDIAS)
    assert _comparable(nuevo) == _comparable(viejo)


def test_sin_tiempo_estimado_ocupa_el_bloque_nominal():
    entrada = [bono(1)]
    tarea, = calc_cola.planificar_cola(entrada, {}, HASTA, AHORA, {}, MEDIAS, MONTAJES)
    assert tarea["sin_tiempo"] is True
    assert tarea["duracion"] == calc_cola.MIN_BLOQUE_SIN_TIEMPO


def test_la_cuadrilla_reparte_los_minutos_hombre():
    tarea, = plan([bono(1, 1), bono(1, 2), bono(1, 3)])
    assert tarea["a_la_vez"] == 3
    assert tarea["duracion"] == tarea["min_hombre"] / 3


def test_dos_asignados_no_son_cuadrilla():
    #  Con dos, "asignado" significa "que lo coja quien pueda": no se reparte.
    tarea, = plan([bono(1, 1), bono(1, 2)])
    assert tarea["a_la_vez"] == 1
    assert tarea["duracion"] == tarea["min_hombre"]


def test_fuera_de_la_ventana_no_se_pinta_pero_si_se_reserva():
    corto = plan([bono(1, 1), bono(2, 1)], hasta=AHORA)
    assert corto == []


# ── ocupacion_actual / hueco_para / sin_salida ──────────────────────

@necesita_v1
def test_ocupacion_reserva_intervalos_por_recurso():
    fin = datetime(2026, 9, 8, 11, 30)
    activo = {"idempleado": "1", "matricula": "M1", "libre_desde": fin}
    ocupado = calc_cola.ocupacion_actual([activo], HASTA, AHORA)
    assert ocupado == api._ocupacion_actual([activo], HASTA, AHORA)
    assert ocupado == {("empleado", "1"): [(AHORA, fin)],
                       ("maquina", "M1"): [(AHORA, fin)]}


def test_sin_libre_desde_se_reserva_la_ventana_entera():
    abierto = {"idempleado": "1", "matricula": "M1"}
    ocupado = calc_cola.ocupacion_actual([abierto], HASTA, AHORA)
    assert ocupado[("empleado", "1")] == [(AHORA, HASTA)]


def test_liberar_en_ahora_suelta_el_recurso():
    cerrado = {"idempleado": "1", "matricula": "M1", "libre_desde": AHORA}
    assert calc_cola.ocupacion_actual([cerrado], HASTA, AHORA) == {}


@necesita_v1
def test_hueco_para_encuentra_el_sitio_libre_de_hoy():
    #  Reservado mañana de 07:00 a 09:00: hoy por la tarde sigue libre.
    manana = [(datetime(2026, 9, 8, 7), datetime(2026, 9, 8, 9))]
    inicio, fin = calc_cola.hueco_para(manana, AHORA, 60)
    assert (inicio, fin) == api._hueco_para(manana, AHORA, 60)
    assert inicio == AHORA


def test_hueco_para_esquiva_lo_ocupado():
    choque = [(AHORA, datetime(2026, 9, 7, 13))]
    inicio, _ = calc_cola.hueco_para(choque, AHORA, 60)
    assert inicio == datetime(2026, 9, 7, 13)


@necesita_v1
def test_sin_salida_solo_cuenta_al_parado_con_todo_en_rojo():
    lista = [bono(1, 1, semaforo="bloqueada"), bono(2, 1, semaforo="bloqueada"),
             bono(3, 2, semaforo="bloqueada"), bono(4, 3, semaforo="disponible")]
    assert calc_cola.sin_salida(lista, set()) == {"1": 2, "2": 1}
    assert calc_cola.sin_salida(lista, {"1"}) == {"2": 1}
    assert calc_cola.sin_salida(lista, set()) == api._sin_salida(lista, set())
