"""La rejilla de carga por persona y dia, sin tocar el ERP.

Lo que se fija aqui es el reparto: una barra que cruza la noche tiene que
contar en los dos dias, y solo los minutos que caen dentro de la jornada.
"""
from datetime import date, datetime, timedelta

import pytest

from app.routers import plan as pl


HOY = date(2026, 9, 9)          # miercoles
#  La hora importa: para HOY la carga se mide sobre lo que QUEDA de jornada, no
#  sobre las 8 horas. A las 07:00 quedan las 8 enteras, que es lo que esperan
#  los tests que no hablan del reloj.
AHORA = datetime(2026, 9, 9, 7, 0)


def item(recurso, ini, fin, tipo='programado', estado='disponible', orden=1):
    return {'recurso_id': recurso, 'tipo': tipo, 'estado': estado,
            'start': ini, 'end': fin, 'idorden': orden, 'idbono': 10,
            'art_id': 'A1', 'art': 'Articulo', 'operacion': 'Maquina M1'}


def montar(monkeypatch, items, grupos=None, ahora=AHORA, automaticas=()):
    monkeypatch.setattr(pl.produccion, 'calcular_items', lambda *a, **kw: items)
    monkeypatch.setattr(pl.produccion, 'maquinas_automaticas', lambda: set(automaticas))
    monkeypatch.setattr(pl.produccion, 'censo', lambda *a, **kw: grupos or
                        [{'id': '1', 'nombre': 'Operario 1', 'areas': ['CHAPA']}])
    monkeypatch.setattr(pl, 'date', type('D', (date,), {'today': classmethod(lambda c: HOY)}))
    monkeypatch.setattr(pl, 'datetime',
                        type('R', (datetime,), {'now': classmethod(lambda c, tz=None: ahora)}))
    return pl.get_plan


def test_una_barra_que_cruza_la_noche_cuenta_en_los_dos_dias(monkeypatch):
    """El caso de 6585/60: empieza hoy a las 10:09 y acaba manana a las 07:54.
    Contarla solo en su dia de inicio dejaba a manana pareciendo vacio."""
    get_plan = montar(monkeypatch, [item('1', datetime(2026, 9, 9, 13),
                                         datetime(2026, 9, 10, 9))])
    d = get_plan(dias=2)
    hoy, manana = d['personas'][0]['dias']

    assert hoy['min'] == 120        # 13:00 -> 15:00
    assert manana['min'] == 120     # 07:00 -> 09:00
    assert [x['personas'] for x in d['dias']] == [1, 1]


def test_la_noche_y_el_fin_de_semana_no_suman_carga(monkeypatch):
    # De las 14:00 del viernes a las 08:00 del lunes: 1 h el viernes y 1 el lunes.
    get_plan = montar(monkeypatch, [item('1', datetime(2026, 9, 11, 14),
                                         datetime(2026, 9, 14, 8))])
    d = get_plan(dias=4)            # mie, jue, vie, LUN (el finde se salta)
    assert [c['min'] for c in d['personas'][0]['dias']] == [0, 0, 60, 60]
    assert [x['etiqueta'] for x in d['dias']] == ['Mié 9', 'Jue 10', 'Vie 11', 'Lun 14']


def test_quien_no_tiene_nada_sigue_apareciendo(monkeypatch):
    """Un hueco es informacion: sin las filas vacias no se ve quien esta libre."""
    get_plan = montar(monkeypatch, [item('1', datetime(2026, 9, 9, 8), datetime(2026, 9, 9, 12))],
                      grupos=[{'id': '1', 'nombre': 'Con trabajo', 'areas': []},
                              {'id': '2', 'nombre': 'Sin nada', 'areas': []}])
    d = get_plan(dias=1)

    assert d['plantilla'] == 2
    assert [p['nombre'] for p in d['personas']] == ['Con trabajo', 'Sin nada']
    assert d['personas'][1]['dias_con_trabajo'] == 0
    assert d['dias'][0]['personas'] == 1


def test_el_trabajo_ya_hecho_no_ocupa_jornada(monkeypatch):
    """`trabajado` y `parcial` son pasado: no reservan a nadie."""
    get_plan = montar(monkeypatch, [
        item('1', datetime(2026, 9, 9, 8), datetime(2026, 9, 9, 10), tipo='trabajado'),
        item('1', datetime(2026, 9, 9, 10), datetime(2026, 9, 9, 11), tipo='real', estado='plazo'),
    ])
    d = get_plan(dias=1)
    assert d['personas'][0]['dias'][0]['min'] == 60
    assert len(d['personas'][0]['dias'][0]['bonos']) == 1


def test_lo_que_se_hace_a_la_vez_cuenta_una_sola_vez(monkeypatch):
    """El caso de ETT4: tenia abiertos a la vez el 6479/10 en la INYECTORA
    --que trabaja sola-- y el 6479/40 en Manual INYECCION, y sumando los dos
    salia al 200%. Una persona no esta el 200% ocupada: esta ocupada, y hace
    dos cosas."""
    get_plan = montar(monkeypatch, [
        item('1', datetime(2026, 9, 9, 7), datetime(2026, 9, 9, 15), orden=1),
        item('1', datetime(2026, 9, 9, 7), datetime(2026, 9, 9, 11), orden=2),
    ])
    celda = get_plan(dias=1)['personas'][0]['dias'][0]

    assert celda['min'] == 480          # la union, no los 720 de la suma
    assert celda['pct'] == 100
    assert celda['min_bonos'] == 720    # lo que suman por separado
    assert celda['simultaneo'] is True
    assert [b['idorden'] for b in celda['bonos']] == [1, 2]   # el mas largo primero


def test_dos_bonos_seguidos_se_suman_enteros(monkeypatch):
    """Sin solape no hay nada que descontar."""
    get_plan = montar(monkeypatch, [
        item('1', datetime(2026, 9, 9, 7), datetime(2026, 9, 9, 11), orden=1),
        item('1', datetime(2026, 9, 9, 11), datetime(2026, 9, 9, 15), orden=2),
    ])
    celda = get_plan(dias=1)['personas'][0]['dias'][0]

    assert celda['min'] == 480
    assert celda['simultaneo'] is False


def test_un_dia_no_pasa_del_cien_porque_lo_que_no_cabe_va_al_siguiente(monkeypatch):
    """Con la union, la carga de un dia esta acotada por el propio dia. No es
    una limitacion: la cola esta encadenada, asi que lo que no cabe hoy no
    desborda, se coloca manana. El desbordamiento se lee en la fila --varios
    dias seguidos al 100%--, no en una celda al 150%."""
    get_plan = montar(monkeypatch, [
        item('1', datetime(2026, 9, 9, 13), datetime(2026, 9, 9, 15), orden=1),
        item('1', datetime(2026, 9, 10, 7), datetime(2026, 9, 10, 15), orden=2),
    ], ahora=datetime(2026, 9, 9, 13))
    dias = get_plan(dias=2)['personas'][0]['dias']

    assert [c['pct'] for c in dias] == [100, 100]
    assert dias[0]['disponible'] == 120


def test_los_mas_cargados_van_primero(monkeypatch):
    get_plan = montar(monkeypatch, [
        item('1', datetime(2026, 9, 9, 7), datetime(2026, 9, 9, 9)),
        item('2', datetime(2026, 9, 9, 7), datetime(2026, 9, 9, 14)),
    ], grupos=[{'id': '1', 'nombre': 'Poco', 'areas': []},
               {'id': '2', 'nombre': 'Mucho', 'areas': []}])
    d = get_plan(dias=1)
    assert [p['nombre'] for p in d['personas']] == ['Mucho', 'Poco']


@pytest.mark.parametrize('dias', [0, 16])
def test_la_ventana_tiene_topes(dias):
    from fastapi.testclient import TestClient
    from app.main import app
    assert TestClient(app).get(f'/api/plan?dias={dias}').status_code == 422


def test_hoy_la_carga_se_mide_sobre_lo_que_queda_de_jornada(monkeypatch):
    """Segun avanza el dia, lo ya hecho deja de contar en el numerador --es
    pasado, no ocupa a nadie-- y con las 8 horas fijas de denominador la carga
    se desmoronaba sola: a las 13:00, un operario con la tarde entera ocupada
    aparecia al 25%."""
    # Son las 13:00: quedan 2 horas de jornada y las dos estan ocupadas.
    get_plan = montar(monkeypatch,
                      [item('1', datetime(2026, 9, 9, 13), datetime(2026, 9, 9, 15))],
                      ahora=datetime(2026, 9, 9, 13))
    celda = get_plan(dias=1)['personas'][0]['dias'][0]

    assert celda['disponible'] == 120
    assert celda['min'] == 120
    assert celda['pct'] == 100          # con las 8 horas fijas habria dado 25%


def test_lo_que_ya_paso_hoy_no_cuenta_como_carga(monkeypatch):
    """Un bono abierto desde las 08:00 solo ocupa lo que le queda por delante."""
    get_plan = montar(monkeypatch,
                      [item('1', datetime(2026, 9, 9, 8), datetime(2026, 9, 9, 12), tipo='real')],
                      ahora=datetime(2026, 9, 9, 11))
    celda = get_plan(dias=1)['personas'][0]['dias'][0]

    assert celda['min'] == 60           # de 11:00 a 12:00, no las 4 horas
    assert celda['disponible'] == 240   # de 11:00 a 15:00
    assert celda['pct'] == 25


def test_los_dias_futuros_siguen_midiendose_sobre_la_jornada_entera(monkeypatch):
    get_plan = montar(monkeypatch,
                      [item('1', datetime(2026, 9, 10, 7), datetime(2026, 9, 10, 11))],
                      ahora=datetime(2026, 9, 9, 13))
    dias = get_plan(dias=2)['personas'][0]['dias']

    assert dias[0]['disponible'] == 120      # hoy quedan 2 horas
    assert dias[1]['disponible'] == 480      # manana, la jornada entera
    assert dias[1]['pct'] == 50


def test_con_la_jornada_acabada_no_se_divide_entre_cero(monkeypatch):
    get_plan = montar(monkeypatch, [], ahora=datetime(2026, 9, 9, 16))
    celda = get_plan(dias=1)['personas'][0]['dias'][0]

    assert celda['disponible'] == 0
    assert celda['pct'] == 0


def test_la_carga_tambien_se_puede_ver_por_maquina(monkeypatch):
    """En la vista de maquinas el grupo trae un `area` suelto en vez de la
    lista que trae el operario."""
    pedido = {}
    monkeypatch.setattr(pl.produccion, 'calcular_items',
                        lambda vista=None, **kw: pedido.update(dict(kw, vista=vista)) or [])
    monkeypatch.setattr(pl.produccion, 'censo', lambda *a, **kw: [
        {'id': '044', 'nombre': 'Retractiladora GARPER', 'sub': 'Matrícula 044', 'area': 'EMBALAJE'}])
    monkeypatch.setattr(pl, 'date', type('D', (date,), {'today': classmethod(lambda c: HOY)}))
    monkeypatch.setattr(pl, 'datetime',
                        type('R', (datetime,), {'now': classmethod(lambda c, tz=None: AHORA)}))

    d = pl.get_plan(dias=1, vista='maquina')

    assert pedido['vista'] == 'maquina'
    assert d['vista'] == 'maquina'
    assert d['personas'][0]['areas'] == ['EMBALAJE']


def test_un_solape_de_un_minuto_no_se_marca(monkeypatch):
    """Dos barras encadenadas que se tocan dejan un minuto de diferencia entre
    la suma y la union solo por el redondeo. Marcarlas llenaria la rejilla de
    avisos que no dicen nada."""
    get_plan = montar(monkeypatch, [
        item('1', datetime(2026, 9, 9, 7), datetime(2026, 9, 9, 11), orden=1),
        item('1', datetime(2026, 9, 9, 10, 59), datetime(2026, 9, 9, 13), orden=2),
    ])
    celda = get_plan(dias=1)['personas'][0]['dias'][0]

    assert celda['min_bonos'] - celda['min'] == 1
    assert celda['simultaneo'] is False


# ─────────────────────────────────────────────────────────────────────
#  Lo que necesita la hoja del día
# ─────────────────────────────────────────────────────────────────────
#  La hoja se imprime la víspera y se cuelga para toda la planta. De cada bono
#  tiene que decir a qué hora va, y si es un trabajo de varios días, qué día de
#  cuántos es: una orden de tres días leída como tres trabajos sueltos no sirve.

def test_una_orden_de_dos_dias_se_lee_como_tal_en_cada_dia(monkeypatch):
    get_plan = montar(monkeypatch, [item('1', datetime(2026, 9, 9, 13),
                                         datetime(2026, 9, 10, 9))])
    hoy, manana = (c['bonos'][0] for c in get_plan(dias=2)['personas'][0]['dias'])

    assert (hoy['inicio'], hoy['fin']) == (datetime(2026, 9, 9, 13), datetime(2026, 9, 9, 15))
    assert (hoy['viene'], hoy['sigue'], hoy['dia_n'], hoy['dias_n']) == (False, True, 1, 2)
    assert (manana['inicio'], manana['fin']) == (datetime(2026, 9, 10, 7), datetime(2026, 9, 10, 9))
    assert (manana['viene'], manana['sigue'], manana['dia_n'], manana['dias_n']) == (True, False, 2, 2)


def test_el_fin_de_semana_no_cuenta_como_dia_de_la_orden(monkeypatch):
    # Viernes 14:00 -> lunes 08:00: son dos jornadas, no cuatro días.
    get_plan = montar(monkeypatch, [item('1', datetime(2026, 9, 11, 14),
                                         datetime(2026, 9, 14, 8))])
    lunes = get_plan(dias=4)['personas'][0]['dias'][3]['bonos'][0]
    assert (lunes['dia_n'], lunes['dias_n']) == (2, 2)


def test_acabar_justo_al_abrir_no_es_un_dia_mas(monkeypatch):
    """Una barra que termina a las 07:00 no ocupa nada de ese día: decir
    "día 1 de 2" haría esperar una continuación que no existe."""
    get_plan = montar(monkeypatch, [item('1', datetime(2026, 9, 9, 13),
                                         datetime(2026, 9, 10, 7))])
    b = get_plan(dias=2)['personas'][0]['dias'][0]['bonos'][0]
    assert (b['sigue'], b['dias_n']) == (False, 1)


def test_las_piezas_se_reparten_por_el_tiempo_de_cada_dia(monkeypatch):
    # 120 piezas pendientes en 4 horas: 2 hoy (13-15) y 2 mañana (07-09).
    it = item('1', datetime(2026, 9, 9, 13), datetime(2026, 9, 10, 9))
    it.update(piezas_pendientes=120, piezas=200)
    get_plan = montar(monkeypatch, [it])
    hoy, manana = (c['bonos'][0] for c in get_plan(dias=2)['personas'][0]['dias'])

    assert (hoy['piezas_dia'], manana['piezas_dia']) == (60, 60)
    assert hoy['piezas_pendientes'] == 120 and hoy['piezas_objetivo'] == 200


def test_sin_piezas_conocidas_no_se_inventa_el_reparto(monkeypatch):
    get_plan = montar(monkeypatch, [item('1', datetime(2026, 9, 9, 8), datetime(2026, 9, 9, 10))])
    b = get_plan(dias=1)['personas'][0]['dias'][0]['bonos'][0]
    assert b['piezas_dia'] is None and b['piezas_pendientes'] is None


# ─────────────────────────────────────────────────────────────────────
#  Tiempo efectivo del operario en la hoja del día
# ─────────────────────────────────────────────────────────────────────
#  Dos reglas de producción, SOLO para la hoja: la jornada son 7h45 porque el
#  descanso de 11:00 a 11:15 no es trabajo, y en las máquinas automáticas al
#  operario solo le cuenta el montaje. El plan y la rejilla no cambian.

def maquina(it, matricula, **extra):
    it.update(matricula=matricula, **extra)
    return it


def test_la_jornada_efectiva_son_siete_horas_y_tres_cuartos(monkeypatch):
    get_plan = montar(monkeypatch, [item('1', datetime(2026, 9, 9, 7), datetime(2026, 9, 9, 15))])
    celda = get_plan(dias=1)['personas'][0]['dias'][0]

    assert celda['min'] == 480                       # la rejilla, como siempre
    assert celda['min_operario'] == 465              # la hoja, sin el descanso
    assert celda['disponible_operario'] == 465
    assert celda['bonos'][0]['min_operario'] == 465


def test_un_trabajo_que_no_toca_el_descanso_cuenta_entero(monkeypatch):
    get_plan = montar(monkeypatch, [item('1', datetime(2026, 9, 9, 12), datetime(2026, 9, 9, 13))])
    assert get_plan(dias=1)['personas'][0]['dias'][0]['min_operario'] == 60


def test_en_una_automatica_solo_cuenta_el_montaje(monkeypatch):
    # 30 min de preparación al principio de una barra de todo el día.
    it = maquina(item('1', datetime(2026, 9, 9, 7), datetime(2026, 9, 9, 15)), 'M1',
                 min_preparacion=30)
    get_plan = montar(monkeypatch, [it], automaticas={'M1'})
    celda = get_plan(dias=1)['personas'][0]['dias'][0]
    b = celda['bonos'][0]

    assert b['automatica'] is True
    assert (b['op_inicio'], b['op_fin']) == (datetime(2026, 9, 9, 7), datetime(2026, 9, 9, 7, 30))
    assert b['min_operario'] == 30 and celda['min_operario'] == 30
    assert celda['min'] == 480                       # la máquina sigue ocupada


def test_la_misma_barra_en_una_maquina_no_declarada_cuenta_entera(monkeypatch):
    it = maquina(item('1', datetime(2026, 9, 9, 7), datetime(2026, 9, 9, 15)), 'M2',
                 min_preparacion=30)
    get_plan = montar(monkeypatch, [it], automaticas={'M1'})
    b = get_plan(dias=1)['personas'][0]['dias'][0]['bonos'][0]
    assert b['automatica'] is False and b['min_operario'] == 465


def test_una_automatica_ya_montada_no_le_cuesta_nada(monkeypatch):
    """Bono a medias con la máquina montada: la cola pone la preparación a 0."""
    it = maquina(item('1', datetime(2026, 9, 9, 7), datetime(2026, 9, 9, 15)), 'M1',
                 min_preparacion=0)
    get_plan = montar(monkeypatch, [it], automaticas={'M1'})
    celda = get_plan(dias=1)['personas'][0]['dias'][0]
    assert celda['min_operario'] == 0 and celda['bonos'][0]['op_inicio'] is None


def test_en_una_automatica_la_produccion_en_marcha_no_cuenta_y_el_montaje_si(monkeypatch):
    montaje = maquina(item('1', datetime(2026, 9, 9, 7), datetime(2026, 9, 9, 8), tipo='real'),
                      'M1', es_montaje=True)
    produce = maquina(item('1', datetime(2026, 9, 9, 8), datetime(2026, 9, 9, 15), tipo='real',
                           orden=2), 'M1', es_montaje=False)
    get_plan = montar(monkeypatch, [montaje, produce], automaticas={'M1'})
    celda = get_plan(dias=1)['personas'][0]['dias'][0]
    assert celda['min_operario'] == 60


def test_el_montaje_de_un_trabajo_de_dos_dias_cuenta_solo_el_primero(monkeypatch):
    it = maquina(item('1', datetime(2026, 9, 9, 14), datetime(2026, 9, 10, 15)), 'M1',
                 min_preparacion=90)             # 14:00-15:00 hoy y 07:00-07:30 mañana
    get_plan = montar(monkeypatch, [it], automaticas={'M1'})
    hoy, manana = get_plan(dias=2)['personas'][0]['dias']
    assert hoy['min_operario'] == 60
    assert manana['min_operario'] == 30
    assert manana['bonos'][0]['op_fin'] == datetime(2026, 9, 10, 7, 30)


# ─────────────────────────────────────────────────────────────────────
#  Quien falta no cuenta en la hoja del día
# ─────────────────────────────────────────────────────────────────────

def test_la_hoja_sabe_quien_falta_el_dia_entero(monkeypatch):
    get_plan = montar(monkeypatch, [], grupos=[{'id': '13', 'nombre': 'Ángel', 'areas': []},
                                               {'id': '7', 'nombre': 'Parcial', 'areas': []},
                                               {'id': '1', 'nombre': 'Presente', 'areas': []}])
    monkeypatch.setattr(pl.produccion, 'ausencias', lambda dia: {
        '13': {'motivo': 'De baja', 'parcial': False},
        '7':  {'motivo': 'Ausencia', 'parcial': True},     # trabaja el resto del día
    })
    falta = {p['nombre']: p['dias'][0]['ausencia'] for p in get_plan(dias=1, vista='empleado', ausencias=True)['personas']}
    assert falta == {'Ángel': 'De baja', 'Parcial': None, 'Presente': None}


def test_la_rejilla_de_carga_no_consulta_ausencias(monkeypatch):
    get_plan = montar(monkeypatch, [])
    def no_llamar(dia):
        raise AssertionError('la rejilla no debe pagar la consulta a PORTALHR')
    monkeypatch.setattr(pl.produccion, 'ausencias', no_llamar)
    assert get_plan(dias=1)['personas'][0]['dias'][0]['ausencia'] is None


def test_el_sobrante_de_un_montaje_del_dia_anterior_no_sale_como_hora(monkeypatch):
    """1 min de montaje que empieza a las 14:59:40: 20 s hoy y 40 s mañana.
    En la hoja salía "07:00–07:00"; menos de un minuto no es un montaje."""
    it = maquina(item('1', datetime(2026, 9, 9, 14, 59, 40), datetime(2026, 9, 10, 12)), 'M1',
                 min_preparacion=1)
    get_plan = montar(monkeypatch, [it], automaticas={'M1'})
    manana = get_plan(dias=2)['personas'][0]['dias'][1]
    assert manana['bonos'][0]['op_inicio'] is None and manana['min_operario'] == 0
