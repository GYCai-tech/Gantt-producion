"""La pestana de bonos con stock libre negativo, sin tocar el ERP."""
from decimal import Decimal

from app.routers import bonos


def fila(orden=6687, bono=10, articulo='60007034 ', descrip='VARILLA CORTADA ', stock=Decimal('-20'),
         estado=0, descrip_orden='FRENTE JAULON ', sobre_minimo=Decimal('-25')):
    return {'idorden': orden, 'idbono': bono, 'idestado': estado, 'idarticulo': articulo,
            'descrip': descrip, 'idalmacen': 0, 'stock': stock, 'descrip_orden': descrip_orden,
            'stock_sobre_minimo': sobre_minimo}


def erp(monkeypatch, filas):
    monkeypatch.setattr(bonos, '_erp', lambda q, p: filas)


def test_una_fila_por_bono_con_textos_limpios(monkeypatch):
    erp(monkeypatch, [fila()])
    b = bonos.get_bonos()['bonos'][0]

    assert b == {'idorden': 6687, 'idbono': 10, 'estado': 0, 'estado_label': 'En espera',
                 'idarticulo': '60007034', 'descrip': 'VARILLA CORTADA', 'idalmacen': 0,
                 'stock': -20.0, 'descrip_orden': 'FRENTE JAULON', 'stock_sobre_minimo': -25.0}


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


def test_consulta_estados_vivos_almacen_principal_y_stock_negativo(monkeypatch):
    capturado = {}
    monkeypatch.setattr(bonos, '_erp', lambda q, p: capturado.update(q=q, p=p) or [])
    bonos.get_bonos()

    assert capturado['p'] == {'espera': 0, 'activado': 1, 'bloqueado': 3, 'almacen': 0}
    assert 'ob.IdEstado IN (:espera, :activado, :bloqueado)' in capturado['q']
    assert 'ob.IdEstado    AS idestado' in capturado['q']
    assert 's.IdAlmacen = :almacen' in capturado['q']
    assert 'ISNULL(s.Stock, 0) - ISNULL(s.StockReservado, 0) < 0' in capturado['q']
    # Como en la consulta de Access: el stock, y su descripción, son los del
    # artículo de la orden, no los del bono.
    assert 's.IdArticulo = o.IdArticulo' in capturado['q']
    assert 'ao.IdArticulo = s.IdArticulo' in capturado['q']


def test_el_minimo_sale_del_stock_del_almacen(monkeypatch):
    """`StockMinimo` solo existe en Articulos_Stock: es el mínimo de ese almacén."""
    capturado = {}
    monkeypatch.setattr(bonos, '_erp', lambda q, p: capturado.update(q=q) or [])
    bonos.get_bonos()

    assert ('ISNULL(s.Stock, 0) - ISNULL(s.StockReservado, 0) - ISNULL(s.StockMinimo, 0)'
            ' AS stock_sobre_minimo') in capturado['q']


def test_stocks_nulos_cuentan_como_cero(monkeypatch):
    erp(monkeypatch, [fila(stock=None, sobre_minimo=None)])
    b = bonos.get_bonos()['bonos'][0]
    assert b['stock'] == 0.0
    assert b['stock_sobre_minimo'] == 0.0
