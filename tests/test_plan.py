"""La rejilla de carga por persona y dia, sin tocar el ERP.

Lo que se fija aqui es el reparto: una barra que cruza la noche tiene que
contar en los dos dias, y solo los minutos que caen dentro de la jornada.
"""
from datetime import date, datetime, timedelta

import pytest

from app.routers import plan as pl


HOY = date(2026, 9, 9)          # miercoles


def item(recurso, ini, fin, tipo='programado', estado='disponible', orden=1):
    return {'recurso_id': recurso, 'tipo': tipo, 'estado': estado,
            'start': ini, 'end': fin, 'idorden': orden, 'idbono': 10,
            'art_id': 'A1', 'art': 'Articulo', 'operacion': 'Maquina M1'}


def montar(monkeypatch, items, grupos=None):
    monkeypatch.setattr(pl, 'get_items', lambda **kw: items)
    monkeypatch.setattr(pl, 'get_grupos', lambda **kw: grupos or
                        [{'id': '1', 'nombre': 'Operario 1', 'areas': ['CHAPA']}])
    monkeypatch.setattr(pl, 'date', type('D', (date,), {'today': classmethod(lambda c: HOY)}))
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


def test_dos_bonos_solapados_pasan_del_cien_por_cien(monkeypatch):
    """No se recorta: que un dia sume mas que la jornada es la senal de que
    ese reparto no cabe, y taparlo con un min(100) la esconde."""
    get_plan = montar(monkeypatch, [
        item('1', datetime(2026, 9, 9, 7), datetime(2026, 9, 9, 15), orden=1),
        item('1', datetime(2026, 9, 9, 7), datetime(2026, 9, 9, 11), orden=2),
    ])
    d = get_plan(dias=1)
    celda = d['personas'][0]['dias'][0]

    assert celda['min'] == 480 + 240
    assert celda['pct'] == 150
    assert [b['idorden'] for b in celda['bonos']] == [1, 2]   # el mas largo primero


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
