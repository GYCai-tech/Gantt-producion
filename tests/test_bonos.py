"""La pestana de bonos con stock libre negativo, sin tocar el ERP."""
from decimal import Decimal

from app.routers import bonos


def fila(orden=6669, bono=30, articulo='12208006 ', descrip='BANDEJA HORIZONTAL ', stock=Decimal('-3'),
         estado=3):
    return {'idorden': orden, 'idbono': bono, 'idestado': estado, 'idarticulo': articulo,
            'descrip': descrip, 'stock': stock}


def erp(monkeypatch, filas):
    monkeypatch.setattr(bonos, '_erp', lambda q, p: filas)


def test_una_fila_por_bono_con_textos_limpios(monkeypatch):
    erp(monkeypatch, [fila()])
    b = bonos.get_bonos()['bonos'][0]

    assert b == {'idorden': 6669, 'idbono': 30, 'estado': 3, 'estado_label': 'Bloqueado',
                 'idarticulo': '12208006', 'descrip': 'BANDEJA HORIZONTAL', 'stock': -3.0}


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
    # Como en la consulta de Access: el stock es el del artículo de la orden.
    assert 's.IdArticulo = o.IdArticulo' in capturado['q']


def test_stock_nulo_cuenta_como_cero(monkeypatch):
    erp(monkeypatch, [fila(stock=None)])
    assert bonos.get_bonos()['bonos'][0]['stock'] == 0.0
