"""Reservas conjuntas y continuidad, con casos de taller sin tocar el ERP."""
from datetime import datetime

from app.routers import api

AHORA = datetime(2026, 9, 7, 12)
HASTA = datetime(2026, 9, 11, 15)
MEDIAS = {'articulo': {}, 'trabajo': {}, 'maquina': {}}


def bono(orden=1, empleado=1, maquina='M1', cantidad=100, semaforo='disponible', secuencia=1):
    return {
        'idorden': orden, 'idbono': 10, 'idempleado': empleado,
        'empleado': f'Operario {empleado}', 'matricula': maquina,
        'descrip_maquina': f'Maquina {maquina}', 'idtrabajo': 1,
        'descrip_salida': 'Articulo', 'idarticulo_salida': 'A',
        # El area es la de la maquina del bono; el ERP no la deja nunca a NULL
        # (0 de 212 filas en PersVTrazaordenesOperarios), asi que el fixture
        # tampoco: si el item la pierde, es un fallo que hay que ver.
        'area': 'CHAPA',
        'piezas_a_fabricar': cantidad, 'fabricadas': 0,
        'ordenar': secuencia, 'semaforo': semaforo,
    }


def plan(cola, ocupado=None, hasta=HASTA):
    teoricos = {(b['idorden'], b['idbono']): (1, 1) for b in cola}
    return api._planificar_cola(cola, ocupado or {}, hasta, AHORA, teoricos, MEDIAS)


def test_dos_operarios_comparten_maquina_sin_solaparse():
    tareas = plan([bono(1, 1), bono(2, 2)])
    assert tareas[0]['start'] == AHORA
    assert tareas[1]['start'] == tareas[0]['end']


def test_un_operario_no_se_programa_a_la_vez_en_dos_maquinas():
    tareas = plan([bono(1, 1, 'M1'), bono(2, 1, 'M2')])
    assert tareas[1]['start'] == tareas[0]['end']


def test_recursos_independientes_pueden_trabajar_en_paralelo():
    tareas = plan([bono(1, 1, 'M1'), bono(2, 2, 'M2')])
    assert tareas[0]['start'] == tareas[1]['start'] == AHORA


def test_ambas_vistas_representan_el_mismo_plan(monkeypatch):
    cola = [bono(1, 1), bono(2, 2), bono(2, 3)]
    monkeypatch.setattr(api, '_leer_cola', lambda: cola)
    teoricos = {(b['idorden'], b['idbono']): (1, 1) for b in cola}
    empleados = api._encolar('empleado', {}, HASTA, AHORA, teoricos, MEDIAS)
    maquinas = api._encolar('maquina', {}, HASTA, AHORA, teoricos, MEDIAS)
    assert len(empleados) == 3
    assert len(maquinas) == 2
    por_bono = {i['idorden']: (i['start'], i['end']) for i in maquinas}
    for i in empleados:
        assert (i['start'], i['end']) == por_bono[i['idorden']]


def test_bono_compartido_reserva_a_todos_sin_duplicar_trabajo():
    tareas = plan([bono(1, 1), bono(1, 2), bono(2, 2, 'M2')])
    assert len(tareas) == 2
    assert tareas[0]['duracion'] == 101
    assert tareas[1]['start'] == tareas[0]['end']


def test_la_reserva_activa_se_respeta_en_ambos_recursos():
    fin = datetime(2026, 9, 8, 11, 30)
    activo = {'idempleado': '1', 'matricula': 'M1', 'libre_desde': fin}
    ocupado = api._ocupacion_actual([activo], HASTA, AHORA)
    tareas = plan([bono(1, 2, 'M1'), bono(2, 1, 'M2')], ocupado)
    # Sin el len, un plan vacío haría pasar el `all` sin comprobar nada.
    assert len(tareas) == 2
    assert all(t['start'] == fin for t in tareas)


def test_450_minutos_pendientes_no_liberan_manana_a_las_siete():
    linea = bono(cantidad=100)
    item = {'start': AHORA.replace(hour=11), 'end': AHORA, 'estado': 'plazo',
            'sin_tiempo': False, 'idempleado': '1', 'matricula': 'M1', 'es_montaje': False}
    avance = {(1, 10): {'minutos': 70, 'min_produccion': 10, 'min_montaje': 60,
                        'piezas': 10, 'operarios': 1, 'montando': 1}}
    api._proyectar(item, linea, AHORA, {(1, 10): (1, 5)}, MEDIAS, avance)
    assert item['min_restantes'] == 450
    assert item['end'] == datetime(2026, 9, 8, 11, 30)
    ocupado = api._ocupacion_actual([item], HASTA, AHORA)
    assert plan([bono(2)], ocupado)[0]['start'] == datetime(2026, 9, 8, 11, 30)


def test_bono_bloqueado_no_adelanta_al_disponible():
    tareas = plan([bono(1, semaforo='bloqueada', secuencia=1), bono(2, secuencia=20)])
    assert [t['bono']['idorden'] for t in tareas] == [2, 1]


def test_asignacion_bloqueada_hace_condicional_el_bono_compartido():
    tareas = plan([bono(1, 1), bono(1, 2, semaforo='bloqueada'), bono(2, 3)])
    assert tareas[-1]['bono']['idorden'] == 1
    assert tareas[-1]['semaforo'] == 'bloqueada'


def test_sin_secuencia_va_detras_de_cualquier_secuencia_explicita():
    tareas = plan([bono(1, secuencia=0), bono(2, secuencia=20000)])
    assert [t['bono']['idorden'] for t in tareas] == [2, 1]


def test_un_recurso_ocupado_no_oculta_trabajo_de_otro_libre():
    tareas = plan([bono(1), bono(2, 2, 'M2')], {('maquina', 'M1'): [(AHORA, HASTA)]})
    assert [t['bono']['idorden'] for t in tareas] == [2]


def test_no_se_cobra_preparacion_si_no_quedan_piezas():
    b = bono()
    b['fabricadas'] = b['piezas_a_fabricar']
    assert plan([b]) == []


def test_abierta_sin_liberacion_estimable_no_regala_capacidad():
    """Sin `libre_desde` no se sabe cuándo se libera: se reserva todo."""
    item = {'idempleado': '1', 'matricula': 'M1'}
    ocupado = api._ocupacion_actual([item], HASTA, AHORA)
    assert plan([bono()], ocupado) == []


def test_bono_con_las_piezas_hechas_no_bloquea_la_cola_de_su_operario():
    """El fichaje sin cerrar es trabajo terminado, no ocupación desconocida."""
    linea = bono(cantidad=100)
    item = {'start': AHORA.replace(hour=8), 'end': AHORA, 'estado': 'plazo',
            'sin_tiempo': False, 'idempleado': '1', 'matricula': 'M1',
            'es_montaje': False}
    avance = {(1, 10): {'minutos': 240, 'min_produccion': 240, 'min_montaje': 0,
                        'piezas': 100, 'operarios': 1, 'montando': 1}}
    api._proyectar(item, linea, AHORA, {(1, 10): (5, 1)}, MEDIAS, avance)
    assert item['estado'] == 'pendiente-cierre'
    assert item['libre_desde'] == AHORA
    ocupado = api._ocupacion_actual([item], HASTA, AHORA)
    assert plan([bono(2, 1, 'M1')], ocupado)[0]['start'] == AHORA


def test_una_preparacion_pasada_de_tiempo_sigue_reservando_su_produccion(monkeypatch):
    """Que el montaje se pase de su media no borra el bono que viene detrás."""
    monkeypatch.setattr(api, '_minutos_montaje', lambda l: 60)
    linea = dict(bono(), idoperacion=1)
    item = {'start': AHORA.replace(hour=10, minute=30), 'end': AHORA,
            'estado': 'plazo', 'sin_tiempo': False, 'idempleado': '1',
            'matricula': 'M1', 'es_montaje': True}
    avance = {(1, 10): {'minutos': 90, 'min_produccion': 0, 'min_montaje': 90,
                        'piezas': 0, 'operarios': 1, 'montando': 1}}
    api._proyectar(item, linea, AHORA, {(1, 10): (60, 1)}, MEDIAS, avance)
    assert item['min_restantes'] == 0
    # 100 piezas x 1 min desde ahora, no la ventana entera.
    assert item['libre_desde'] == AHORA.replace(hour=13, minute=40)
    ocupado = api._ocupacion_actual([item], HASTA, AHORA)
    assert plan([bono(2)], ocupado)[0]['start'] == item['libre_desde']


def test_la_preparacion_reserva_tambien_la_produccion_que_viene_despues(monkeypatch):
    monkeypatch.setattr(api, '_minutos_montaje', lambda l: 60)
    linea = dict(bono(), idoperacion=1)
    item = {'start': AHORA.replace(hour=11, minute=30), 'end': AHORA,
            'estado': 'plazo', 'sin_tiempo': False, 'idempleado': '1',
            'matricula': 'M1', 'es_montaje': True}
    avance = {(1, 10): {'minutos': 30, 'min_produccion': 0, 'min_montaje': 30,
                        'piezas': 0, 'operarios': 1, 'montando': 1}}
    api._proyectar(item, linea, AHORA, {(1, 10): (60, 1)}, MEDIAS, avance)
    assert item['fin_estimado'] == AHORA.replace(minute=30)
    assert item['libre_desde'] == AHORA.replace(hour=14, minute=10)
    ocupado = api._ocupacion_actual([item], HASTA, AHORA)
    assert plan([bono(2)], ocupado)[0]['start'] == item['libre_desde']


def _montaje():
    """La barra de un montaje abierto, tal como la arma `get_items`."""
    return {'id': '1-10-1', 'recurso_id': '1', 'idempleado': '1', 'matricula': 'M1',
            'start': AHORA.replace(hour=11, minute=30), 'end': AHORA,
            'estado': 'plazo', 'sin_tiempo': False, 'es_montaje': True,
            'idorden': 1, 'idbono': 10, 'art': 'Articulo', 'art_id': 'A',
            'area': 'CHAPA', 'operacion': 'Maquina M1', 'operarios': 'Operario 1',
            'piezas': 100}


def test_la_reserva_de_la_preparacion_se_pinta_como_barra(monkeypatch):
    """Reservar sin dibujar deja al operario ocupado y la fila vacia.

    Medido en 6589/20: Elias reservado seis dias por las 1.200 piezas que
    quedaban detras del montaje, y la pantalla enseñandolo libre a las 13:16
    porque lo unico fichado era la preparacion. La cola tampoco lo recogia
    (filtra IdEstado = 0 y el bono ya estaba en 1)."""
    monkeypatch.setattr(api, '_minutos_montaje', lambda l: 60)
    item = _montaje()
    avance = {(1, 10): {'minutos': 30, 'min_produccion': 0, 'min_montaje': 30,
                        'piezas': 0, 'operarios': 1, 'montando': 1}}
    api._proyectar(item, dict(bono(), idoperacion=1), AHORA,
                   {(1, 10): (60, 1)}, MEDIAS, avance)
    barra, = api._continuar([item], AHORA)
    assert barra['start'] == item['end']
    assert barra['end'] == item['libre_desde']
    assert barra['tipo'] == 'programado'
    assert barra['estado'] == 'continuacion'
    assert barra['min_restantes'] == 100        # 100 piezas x 1 min
    assert '_pendiente' not in item             # la clave interna no se filtra


def test_un_bono_con_las_piezas_hechas_no_deja_barra_de_continuacion(monkeypatch):
    """Ahi no queda fabricacion: queda cerrar el fichaje."""
    monkeypatch.setattr(api, '_minutos_montaje', lambda l: 60)
    item = _montaje()
    avance = {(1, 10): {'minutos': 130, 'min_produccion': 100, 'min_montaje': 30,
                        'piezas': 100, 'operarios': 1, 'montando': 1}}
    api._proyectar(item, dict(bono(), idoperacion=1), AHORA,
                   {(1, 10): (60, 1)}, MEDIAS, avance)
    assert api._continuar([item], AHORA) == []


def test_cambiar_la_ventana_no_reordena_la_prevision():
    cola = [bono(1, 1, 'M1'), bono(2, 1, 'M2')]
    ocupado = {('maquina', 'M1'): [(AHORA, datetime(2026, 9, 8, 12))]}
    corto = plan(cola, ocupado, hasta=AHORA.replace(hour=15))
    largo = plan(cola, ocupado)
    assert corto == [t for t in largo if t['start'] < AHORA.replace(hour=15)]


def test_un_companero_ocupado_no_vacia_la_cola_del_otro():
    """El caso de ETT2: sus cuatro bonos los comparte con José Luís, que
    estaba fichado él solo en otra máquina hasta el día siguiente. Esperando a
    que coincidieran los dos, ETT2 aparecía sin nada que hacer con su máquina
    parada todo el día."""
    ocupado = {('empleado', '1'): [(AHORA, datetime(2026, 9, 8, 10, 23))]}
    tareas = plan([bono(1, 1, 'M1'), bono(1, 2, 'M1')], ocupado)

    assert len(tareas) == 1
    assert tareas[0]['huecos']['2'][0] == AHORA        # el que está libre, ya
    assert tareas[0]['huecos']['1'][0] == datetime(2026, 9, 8, 10, 23)
    # La máquina se reserva una vez, con el primero que puede cogerla.
    assert tareas[0]['start'] == AHORA


def test_el_hueco_de_un_companero_no_se_pinta_si_cae_fuera_de_la_ventana(monkeypatch):
    cola = [bono(1, 1, 'M1'), bono(1, 2, 'M1')]
    monkeypatch.setattr(api, '_leer_cola', lambda: cola)
    ocupado = {('empleado', '1'): [(AHORA, datetime(2026, 9, 14, 8))]}  # más allá de HASTA
    items = api._encolar('empleado', ocupado, HASTA, AHORA, {(1, 10): (1, 1)}, MEDIAS)

    assert [i['recurso_id'] for i in items] == ['2']


def test_la_cola_no_presenta_la_media_de_la_maquina_como_fiable(monkeypatch):
    """Una misma maquina hace piezas muy distintas: su media sirve para
    dimensionar la barra, no como ritmo del que fiarse. Las barras abiertas ya
    lo avisaban y la cola las pintaba en verde, como una estimacion buena."""
    monkeypatch.setattr(api, '_leer_cola', lambda: [bono()])
    solo_maquina = {'articulo': {}, 'trabajo': {},
                    'maquina': {'M1': {'n': 10, 'minutos': 100.0, 'piezas': 100.0}}}
    items = api._encolar('empleado', {}, HASTA, AHORA, {}, solo_maquina)

    assert len(items) == 1
    assert items[0]['origen_estimado'] == 'media_maquina'
    assert items[0]['estado'] == 'sin-estimar'
    # El aviso no encoge la barra: la cola se sigue encadenando con su tamaño.
    assert items[0]['min_restantes'] > 0
    assert items[0]['sin_tiempo'] is False


def test_un_bono_que_arranca_manana_no_inutiliza_su_maquina_hoy():
    """El caso de Jose Ramon. ETT4 tiene 1,8 jornadas de cola, asi que su bono
    de la maquina 004 cae manana por la manana; con la ocupacion como un unico
    "libre a partir de", esa reserva dejaba la 004 inservible HOY, y a Jose
    Ramon —libre a las 11:47 y con sus dos bonos en esa maquina— se le iba
    todo a manana por un trabajo que ni siquiera empieza hoy."""
    ocupado = {('empleado', '1'): [(AHORA, datetime(2026, 9, 8, 9))]}
    # El 1 esta ocupado hasta manana; el 2 esta libre. Los dos van a M1.
    tareas = plan([bono(1, 1, 'M1'), bono(2, 2, 'M1')], ocupado)

    porbono = {t['bono']['idorden']: t for t in tareas}
    assert porbono[1]['start'] == datetime(2026, 9, 8, 9)     # espera a su operario
    # Y el 2 entra HOY, en el hueco que el otro deja delante.
    assert porbono[2]['start'] == AHORA
    assert porbono[2]['end'] < porbono[1]['start']


def test_el_hueco_solo_se_usa_si_el_trabajo_cabe_entero():
    """Colar una tarea en un hueco que no le llega la solaparia con lo que ya
    esta reservado detras."""
    # M1 libre solo de 12:00 a 12:30; el bono necesita 101 min. La ventana se
    # alarga para poder ver DONDE cae: con la corta se saldria y no se pintaria,
    # que es cierto pero no distingue "no cabe" de "no se intento".
    ocupado = {('maquina', 'M1'): [(AHORA.replace(hour=12, minute=30), HASTA)]}
    tareas = plan([bono(1, 1, 'M1')], ocupado, hasta=datetime(2026, 9, 18, 15))

    assert tareas[0]['start'] >= HASTA          # no se cuela en los 30 min


def test_una_cuadrilla_reparte_el_tiempo_y_empieza_a_la_vez():
    """6595/70: 60 piezas a 5,18 min/pieza son 331 minutos-HOMBRE, pero lo
    hacen cuatro operarios juntos, asi que de reloj son 83. Antes se pintaban
    331 a cada uno de los cuatro."""
    cola = [bono(1, e, 'M1', cantidad=100) for e in (1, 2, 3, 4)]
    tareas = plan(cola)

    assert len(tareas) == 1
    t = tareas[0]
    assert t['a_la_vez'] == 4
    assert t['min_hombre'] == 101              # 100 piezas x 1 min + 1 de setup
    assert t['duracion'] == 101 / 4            # el reloj, que es lo que mide la barra
    # Y arrancan los cuatro en el mismo instante: trabajan juntos.
    assert len({h for h in t['huecos'].values()}) == 1


def test_dos_asignados_no_son_cuadrilla():
    """Con dos, el 72% de los bonos acaba haciendolo una sola persona: repartir
    el tiempo entre dos partiria por la mitad barras que nadie hace en pareja.
    Ademas cada uno conserva su hueco, que es lo que arreglo el caso de ETT2."""
    tareas = plan([bono(1, 1, 'M1'), bono(1, 2, 'M1')])

    t = tareas[0]
    assert t['a_la_vez'] == 1
    assert t['duracion'] == t['min_hombre'] == 101


def test_la_cuadrilla_espera_a_que_esten_libres_todos():
    """Al reves que un bono de dos: aqui no vale que empiece el primero que
    quede libre, porque hace falta la cuadrilla entera."""
    ocupado = {('empleado', '3'): [(AHORA, datetime(2026, 9, 8, 9))]}
    tareas = plan([bono(1, e, 'M1', cantidad=100) for e in (1, 2, 3)], ocupado)

    assert tareas[0]['start'] == datetime(2026, 9, 8, 9)   # el ultimo manda


def test_la_fabricacion_que_sigue_al_montaje_se_reparte_entre_la_cuadrilla():
    """6469/40: tres operarios montando la misma maquina a la vez, y a cada uno
    se le pintaban los 453 minutos del bono ENTERO. El tiempo estimado son
    minutos-hombre; si tres montan juntos, tres fabrican juntos."""
    linea = dict(bono(cantidad=300), idoperacion=1)
    item = {'start': AHORA.replace(hour=11, minute=30), 'end': AHORA,
            'estado': 'plazo', 'sin_tiempo': False, 'idempleado': '1',
            'matricula': 'M1', 'es_montaje': True}
    avance = {(1, 10): {'minutos': 30, 'min_produccion': 0, 'min_montaje': 30,
                        'piezas': 0, 'operarios': 1, 'montando': 3}}
    api._proyectar(item, linea, AHORA, {(1, 10): (60, 1)}, MEDIAS, avance)

    # 300 piezas x 1 min = 300 minutos-hombre; entre tres son 100 de reloj.
    assert item['_pendiente']['minutos'] == 100
    # Y la reserva del recurso va con el reloj, no con los minutos-hombre: 1
    # minuto de montaje que falta —la media por defecto son 31 y lleva 30— mas
    # los 100 de fabricacion.
    assert item['libre_desde'] == AHORA.replace(hour=13, minute=41)


def test_un_solo_montador_no_cambia_nada():
    linea = dict(bono(cantidad=300), idoperacion=1)
    item = {'start': AHORA.replace(hour=11, minute=30), 'end': AHORA,
            'estado': 'plazo', 'sin_tiempo': False, 'idempleado': '1',
            'matricula': 'M1', 'es_montaje': True}
    avance = {(1, 10): {'minutos': 30, 'min_produccion': 0, 'min_montaje': 30,
                        'piezas': 0, 'operarios': 1, 'montando': 1}}
    api._proyectar(item, linea, AHORA, {(1, 10): (60, 1)}, MEDIAS, avance)

    assert item['_pendiente']['minutos'] == 300
