"""La planificación de cola extraída a `app.calculos.cola`.

Cada caso se corre dos veces —módulo nuevo y router— y se comparan las dos
salidas. Es la única forma de asegurar que sacar `montajes` a parámetro no
movió ningún bono de sitio.
"""
from datetime import datetime, timedelta

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


#  `bono` y `asignados` son los mismos diccionarios de entrada; lo que
#  interesa comparar es la colocación y sus cifras.
#
#  `min_atencion` y `desatendida` quedan fuera porque la v1 no los tiene: no
#  existía la idea de que una máquina pudiera trabajar sola. Sin `atencion`
#  valen siempre `duracion` y False, así que no esconden ninguna diferencia
#  de colocación —eso se comprueba abajo, en
#  `test_sin_atencion_reserva_como_siempre`.
_FUERA = ("bono", "asignados", "min_atencion", "desatendida")


def _comparable(tareas):
    return [{k: v for k, v in t.items() if k not in _FUERA} for t in tareas]


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


# ── máquinas que trabajan solas ─────────────────────────────────────
#  `bono()` usa M1 y `_teoricos` da (setup 1 min, 1 min/pieza), así que un
#  bono de 100 piezas dura 101 minutos: 1 de preparación y 100 de producción.

def plan_con(cola, atencion, ocupado=None, hasta=HASTA):
    return calc_cola.planificar_cola(cola, ocupado or {}, hasta, AHORA,
                                     _teoricos(cola), MEDIAS, MONTAJES, atencion)


def test_sin_atencion_reserva_como_siempre():
    #  El parámetro nuevo no puede mover nada mientras esté vacío: es lo que
    #  permite que los tests diferenciales contra la v1 sigan valiendo.
    entrada = [bono(1, 1, "M1"), bono(2, 1, "M2")]
    assert _comparable(plan(entrada)) == _comparable(plan_con(entrada, {}))
    for tarea in plan(entrada):
        assert tarea["desatendida"] is False
        assert tarea["min_atencion"] == tarea["duracion"]


def test_la_maquina_que_va_sola_suelta_al_operario():
    primero, segundo = plan_con([bono(1, 1, "M1"), bono(2, 1, "M2")], {"M1": 0.2})
    #  101 minutos de bono; al operario le cuestan 1 de montaje + 20 de
    #  vigilancia sobre los 100 de producción.
    assert primero["min_atencion"] == 21
    assert primero["desatendida"] is True
    #  Y el siguiente bono no espera a que la inyectora termine: espera a que
    #  la persona quede libre.
    assert primero["start"] == AHORA
    assert segundo["start"] == AHORA + timedelta(minutes=21)


def test_la_barra_sigue_durando_lo_que_dura_el_bono():
    #  Lo que encoge es la reserva, no el dibujo: en la fila del operario
    #  tiene que verse la máquina corriendo bajo su nombre hasta el final.
    tarea, = plan_con([bono(1, 1, "M1")], {"M1": 0.2})
    assert tarea["end"] - tarea["start"] == timedelta(minutes=101)
    assert tarea["huecos"]["1"] == (tarea["start"], tarea["end"])
    assert tarea["duracion"] == 101


def test_la_preparacion_no_se_descuenta_nunca():
    #  Montar el utillaje lo hace la persona entera aunque luego la máquina
    #  vaya sola: con atención 0 sigue costando el montaje.
    tarea, = plan_con([bono(1, 1, "M1")], {"M1": 0.0})
    assert tarea["min_atencion"] == 1


def test_un_bono_sin_estimar_no_se_descuenta():
    #  Su bloque nominal es un hueco puesto a ojo; aplicarle una fracción
    #  sería afinar una conjetura.
    tarea, = calc_cola.planificar_cola([bono(1, 1, "M1")], {}, HASTA, AHORA,
                                       {}, MEDIAS, MONTAJES, {"M1": 0.1})
    assert tarea["sin_tiempo"] is True
    assert tarea["min_atencion"] == calc_cola.MIN_BLOQUE_SIN_TIEMPO


def test_no_lleva_mas_de_tres_maquinas_a_la_vez():
    entrada = [bono(i, 1, f"M{i}") for i in range(1, 5)]
    atencion = {f"M{i}": 0.1 for i in range(1, 5)}
    tareas = plan_con(entrada, atencion)
    #  Los tres primeros se encadenan por los 11 minutos de atención...
    assert [t["start"] for t in tareas[:3]] == [
        AHORA, AHORA + timedelta(minutes=11), AHORA + timedelta(minutes=22)]
    #  ...y el cuarto espera a que se libere la primera máquina, no a la
    #  atención: ya lleva tres encima.
    assert tareas[3]["start"] == tareas[0]["end"]


def test_la_linea_abierta_en_maquina_sola_no_ata_al_operario():
    fin = AHORA + timedelta(minutes=100)
    activo = {"idempleado": "1", "matricula": "M1", "libre_desde": fin}
    ocupado = calc_cola.ocupacion_actual([activo], HASTA, AHORA, {"M1": 0.2})
    assert ocupado[("maquina", "M1")] == [(AHORA, fin)]
    assert ocupado[("empleado", "1")] == [(AHORA, AHORA + timedelta(minutes=20))]
    #  Suelto pero responsable: cuenta para el tope de simultáneas.
    assert ocupado[("vigila", "1")] == [(AHORA, fin)]


# ── medir_atencion ──────────────────────────────────────────────────

def _linea(empleado, matricula, desde_h, horas):
    ini = datetime(2026, 9, 7, desde_h)
    return {"idempleado": empleado, "matricula": matricula,
            "inicio": ini, "fin": ini + timedelta(hours=horas)}


def test_medir_atencion_no_distingue_quien_va_solo():
    #  Esta es la razón de que la ficha decida y la medida no: el solape es
    #  SIMÉTRICO. La máquina que cicla sola y el trabajo manual que se hace
    #  mientras tanto salen los dos como "desatendidos", y solo producción
    #  sabe cuál de los dos puede quedarse sin nadie delante.
    lineas = [_linea("1", "AUTO", 7, 4), _linea("1", "MANO", 7, 2)]
    medido = calc_cola.medir_atencion(lineas, min_horas=1)
    assert medido["AUTO"] == 0.5    # 2 de sus 4 horas con otra cosa abierta
    assert medido["MANO"] == 0.0    # las 2 suyas, enteras


def test_medir_atencion_cuenta_la_union_y_no_la_suma():
    #  Con tres máquinas a la vez, sumar los solapes por pares daría más
    #  minutos solapados que fichados y una atención negativa.
    lineas = [_linea("1", "AUTO", 7, 4), _linea("1", "A", 7, 2), _linea("1", "B", 7, 2)]
    assert calc_cola.medir_atencion(lineas, min_horas=1)["AUTO"] == 0.5


def test_medir_atencion_ignora_lo_que_no_tiene_historico():
    lineas = [_linea("1", "AUTO", 7, 4), _linea("1", "POCO", 7, 1)]
    medido = calc_cola.medir_atencion(lineas, min_horas=2)
    assert "POCO" not in medido
    assert "AUTO" in medido


def test_operarios_distintos_no_se_solapan_entre_si():
    #  Dos personas en dos máquinas a la vez no hacen automática a ninguna.
    lineas = [_linea("1", "AUTO", 7, 4), _linea("2", "OTRA", 7, 4)]
    medido = calc_cola.medir_atencion(lineas, min_horas=1)
    assert medido == {"AUTO": 1.0, "OTRA": 1.0}
