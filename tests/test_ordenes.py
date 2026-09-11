"""La pestana de ordenes no asignadas, sin tocar el ERP."""
from app.routers import ordenes as od


def fila(orden=6700, bono=10, maquina='903', asignados=0, area='INYECION'):
    return {
        'idorden': orden, 'art_orden': 'PONEDERO 2 HUECOS', 'idart_orden': '11401042',
        'idbono': bono, 'descrip_bono': 'Embolsar', 'matricula': maquina,
        'maquina': 'Manual BOLSAS' if maquina else None, 'area': area,
        'art_salida': '11402062', 'descrip_salida': 'BOLSA PONEDERO 2H',
        'modelo': None, 'modelo_maquina': None, 'cantidad': 240, 'asignados': asignados,
    }


def test_solo_pide_los_bonos_bloqueados(monkeypatch):
    """IdEstado 3. Es justo el estado que la cola y el semaforo filtran, asi
    que el Gantt no puede enseñarlos: esta pestaña existe por eso."""
    capturado = {}
    monkeypatch.setattr(od, '_erp', lambda q, p: capturado.update(q=q, p=p) or [])
    od.get_no_asignadas()

    assert capturado['p'] == {'estado': 3}
    assert 'ob.IdEstado = :estado' in capturado['q']


def test_los_bonos_se_agrupan_por_orden(monkeypatch):
    monkeypatch.setattr(od, '_erp', lambda q, p: [
        fila(6700, 10), fila(6700, 50), fila(6699, 10),
    ])
    d = od.get_no_asignadas()

    assert d['total_ordenes'] == 2
    assert d['total_bonos'] == 3
    assert [o['idorden'] for o in d['ordenes']] == [6700, 6699]   # el ERP manda el orden
    assert [b['idbono'] for b in d['ordenes'][0]['bonos']] == [10, 50]


def test_un_bono_sin_maquina_no_se_pierde(monkeypatch):
    """Son las operaciones de fuera --lacar, zincar-- y con el INNER JOIN de la
    consulta original desaparecian 14. Precisamente son trabajo sin repartir."""
    monkeypatch.setattr(od, '_erp', lambda q, p: [fila(maquina=None, area='')])
    d = od.get_no_asignadas()

    assert d['total_bonos'] == 1
    assert d['ordenes'][0]['bonos'][0]['maquina'] == ''
    assert 'LEFT  JOIN Articulos amaq' in od._SQL


def test_se_cuenta_cuantos_no_tiene_nadie(monkeypatch):
    """16 de los 216 SI tienen a alguien: estan bloqueados por otro motivo."""
    monkeypatch.setattr(od, '_erp', lambda q, p: [
        fila(bono=10, asignados=0), fila(bono=20, asignados=2),
    ])
    d = od.get_no_asignadas()

    assert d['total_bonos'] == 2
    assert d['sin_asignar'] == 1


def test_las_areas_salen_sin_repetir_y_sin_vacios(monkeypatch):
    monkeypatch.setattr(od, '_erp', lambda q, p: [
        fila(bono=10, area='CHAPA'), fila(bono=20, area='CHAPA'), fila(bono=30, area=''),
    ])
    assert od.get_no_asignadas()['areas'] == ['CHAPA']
