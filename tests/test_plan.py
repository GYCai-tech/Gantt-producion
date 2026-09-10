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


def montar(monkeypatch, items, grupos=None, ahora=AHORA):
    monkeypatch.setattr(pl, 'get_items', lambda **kw: items)
    monkeypatch.setattr(pl, 'get_grupos', lambda **kw: grupos or
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
    monkeypatch.setattr(pl, 'get_items', lambda **kw: pedido.update(kw) or [])
    monkeypatch.setattr(pl, 'get_grupos', lambda **kw: [
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
