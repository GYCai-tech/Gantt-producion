"""La pestana de urgencia de bonos, sin tocar el ERP.

Mira DOS almacenes, Principal y Produccion, y un bono entra si su articulo esta
en negativo en alguno de los dos. Medido sobre los 170 articulos con bonos
vivos: 51 en negativo en Principal, 17 en Produccion y solo 2 en los dos.
"""
from decimal import Decimal

from app.routers import bonos


def fila(orden=6687, bono=10, articulo='60007034 ', descrip='VARILLA CORTADA ',
         principal=Decimal('-20'), produccion=Decimal('0'), estado=0,
         descrip_orden='FRENTE JAULON ', matricula='107 ',
         maquina='PLEGADORA ADIRA ', sobre_minimo=Decimal('-25')):
    return {'idorden': orden, 'idbono': bono, 'idestado': estado, 'idarticulo': articulo,
            'descrip': descrip, 'descrip_orden': descrip_orden,
            'matricula': matricula, 'descrip_maquina': maquina,
            'libre_principal': principal, 'libre_produccion': produccion,
            'sobre_minimo': sobre_minimo}


def erp(monkeypatch, filas):
    monkeypatch.setattr(bonos, '_erp', lambda q, p: filas)


def test_una_fila_por_bono_con_textos_limpios(monkeypatch):
    erp(monkeypatch, [fila()])
    b = bonos.get_bonos()['bonos'][0]

    assert b == {'idorden': 6687, 'idbono': 10, 'estado': 0, 'estado_label': 'En espera',
                 'idarticulo': '60007034', 'descrip': 'VARILLA CORTADA',
                 'descrip_orden': 'FRENTE JAULON', 'matricula': '107',
                 'maquina': 'PLEGADORA ADIRA', 'libre_principal': -20.0,
                 'libre_produccion': 0.0, 'sobre_minimo': -25.0}


def test_totales_cuentan_distintos(monkeypatch):
    """Tres bonos de la misma orden son una orden; dos con el mismo artículo, un artículo."""
    erp(monkeypatch, [fila(6669, 20, '60501029'), fila(6669, 30), fila(6669, 40, '12208003'),
                      fila(6717, 30, '12208003')])
    d = bonos.get_bonos()

    assert d['total_bonos'] == 4
    assert d['total_ordenes'] == 2
    assert d['total_articulos'] == 3


def test_cuenta_los_bonos_de_cada_estado_vivo(monkeypatch):
    """El filtro por estado de la pestaña enseña estas cuentas, también las que dan cero."""
    erp(monkeypatch, [fila(6669, 20, estado=0), fila(6669, 30, estado=3), fila(6669, 40, estado=3)])
    d = bonos.get_bonos()

    assert d['por_estado'] == {0: 1, 1: 0, 3: 2}
    assert [b['estado_label'] for b in d['bonos']] == ['En espera', 'Bloqueado', 'Bloqueado']


def test_la_consulta_mira_los_dos_almacenes(monkeypatch):
    """Un agujero en Produccion cuenta aunque en Principal sobre material: es un
    OR, no la suma. La suma taparia el articulo cuyo positivo cancela al otro."""
    capturado = {}
    monkeypatch.setattr(bonos, '_erp', lambda q, p: capturado.update(q=q, p=p) or [])
    bonos.get_bonos()

    assert capturado['p'] == {'espera': 0, 'activado': 1, 'bloqueado': 3,
                              'principal': 0, 'produccion': 2}
    assert 'ob.IdEstado IN (:espera, :activado, :bloqueado)' in capturado['q']
    assert '(st.libre_principal < 0 OR st.libre_produccion < 0)' in capturado['q']
    # Como en la consulta de Access: el stock, y su descripción, son los del
    # artículo de la orden, no los del bono.
    assert 'st.IdArticulo = o.IdArticulo' in capturado['q']
    assert 'ao.IdArticulo = o.IdArticulo' in capturado['q']


def test_la_maquina_se_une_con_LEFT(monkeypatch):
    """17 de los 452 bonos vivos no declaran matricula. Con un JOIN normal
    desaparecian de la pantalla justo por no saber en que maquina van."""
    capturado = {}
    monkeypatch.setattr(bonos, '_erp', lambda q, p: capturado.update(q=q) or [])
    bonos.get_bonos()

    assert 'LEFT JOIN Articulos a_maq' in capturado['q']


def test_un_bono_sin_maquina_no_desaparece(monkeypatch):
    erp(monkeypatch, [fila(matricula=None, maquina=None)])
    b = bonos.get_bonos()['bonos'][0]

    assert b['matricula'] == ''
    assert b['maquina'] == ''


def test_el_minimo_sale_del_stock_del_principal(monkeypatch):
    """`StockMinimo` solo existe en Articulos_Stock: es el mínimo de ese almacén."""
    capturado = {}
    monkeypatch.setattr(bonos, '_erp', lambda q, p: capturado.update(q=q) or [])
    bonos.get_bonos()

    assert 'st.libre_principal - st.minimo_principal AS sobre_minimo' in capturado['q']


def test_stocks_nulos_cuentan_como_cero(monkeypatch):
    erp(monkeypatch, [fila(principal=None, produccion=None, sobre_minimo=None)])
    b = bonos.get_bonos()['bonos'][0]

    assert b['libre_principal'] == 0.0
    assert b['libre_produccion'] == 0.0
    assert b['sobre_minimo'] == 0.0
