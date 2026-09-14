"""La pestana de bonos duplicados, sin tocar el ERP."""
from datetime import datetime

from app.routers import duplicados as dup


def fila(orden=6700, bono=10, articulo='11402062', estado=0, fichajes=0,
         objetivo=100, hechas=0, fecha=datetime(2026, 9, 1, 8, 0)):
    return {
        'idorden': orden, 'idbono': bono, 'idarticulo': articulo,
        'descrip_articulo': 'BOLSA PONEDERO 2H', 'idestado': estado,
        'descrip_bono': 'Embolsar', 'matricula': '903', 'maquina': 'Manual BOLSAS',
        'area': 'INYECION', 'fecha_orden': fecha, 'lote': 'LOT015000-01',
        'objetivo': objetivo, 'hechas': hechas, 'asignados': 2, 'fichajes': fichajes,
    }


def erp(monkeypatch, filas):
    monkeypatch.setattr(dup, '_erp', lambda q, p: filas)


def test_agrupa_por_articulo(monkeypatch):
    erp(monkeypatch, [fila(6700, 10), fila(6701, 20), fila(6702, 30, articulo='OTRO')])
    d = dup.get_duplicados()

    assert d['total_grupos'] == 2
    assert d['total_bonos'] == 3
    assert [len(g['bonos']) for g in d['grupos']] == [2, 1]


def test_cuenta_ordenes_distintas_no_bonos(monkeypatch):
    """Dos bonos de la MISMA orden no son dos ordenes lanzadas por duplicado."""
    erp(monkeypatch, [fila(6700, 10), fila(6700, 20)])
    d = dup.get_duplicados()

    assert d['grupos'][0]['ordenes'] == 1
    assert d['total_ordenes'] == 1


def test_sin_tocar_solo_si_nadie_ficho_nada(monkeypatch):
    """Es la diferencia entre anular un duplicado gratis o tirar trabajo."""
    erp(monkeypatch, [fila(6700, 10), fila(6701, 10)])
    assert dup.get_duplicados()['grupos'][0]['sin_tocar'] is True

    erp(monkeypatch, [fila(6700, 10), fila(6701, 10, fichajes=3)])
    assert dup.get_duplicados()['grupos'][0]['sin_tocar'] is False


def test_solo_pide_los_estados_vivos(monkeypatch):
    """Anulado (-1) y finalizado (2) no compiten por nada."""
    capturado = {}
    monkeypatch.setattr(dup, '_erp', lambda q, p: capturado.update(q=q, p=p) or [])
    dup.get_duplicados()

    assert sorted(capturado['p'].values()) == [0, 1, 3]
    assert 'IdEstado IN (:espera, :activado, :bloqueado)' in capturado['q']


def test_las_piezas_del_grupo_suman_las_de_sus_bonos(monkeypatch):
    erp(monkeypatch, [fila(6700, 10, objetivo=100), fila(6701, 10, objetivo=50)])
    d = dup.get_duplicados()

    assert d['grupos'][0]['piezas'] == 150
    assert d['piezas'] == 150
