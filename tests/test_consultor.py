"""El Consultor de Bonos, sin tocar el ERP.

Portado de la v1 (`legacy/planificador-v1`) con dos cambios: el bono "sin fichar"
sale del ERP en vivo y no de PostgreSQL, y no hay columna Cliente porque
`Ordenes.IdCliente` esta relleno en 48 de 822 bonos (5,8%).
"""
from app.routers import consultor


def fila(orden=6726, bono=10, estado=1, matricula='107 ', maquina='Manual Bolsas-2 ',
         articulo='CABLE CONTROL ', area='INYECION ', usuario='marcos '):
    return {'idorden': orden, 'idbono': bono, 'estado_bono': estado,
            'matricula': matricula, 'descrip_matricula': maquina,
            'idarticulo_orden': '30806013 ', 'descrip_articulo': articulo,
            'area': area, 'usuario': usuario}


def erp(monkeypatch, bonos, abiertas=()):
    """Sustituye `_erp`, que el endpoint llama dos veces: bonos y fichajes."""
    capturado = {'consultas': [], 'params': []}

    def fake(query, params):
        capturado['consultas'].append(query)
        capturado['params'].append(params)
        return list(abiertas) if 'Hfinal IS NULL' in query else list(bonos)

    monkeypatch.setattr(consultor, '_erp', fake)
    return capturado


def test_una_tarjeta_por_bono_con_textos_limpios(monkeypatch):
    erp(monkeypatch, [fila()])
    b = consultor.get_consultor_bonos(estado_orden=1, estado_bono=None, matricula=None)['bonos'][0]

    assert b['idorden'] == 6726
    assert b['matricula'] == '107'
    assert b['descrip_matricula'] == 'Manual Bolsas-2'
    assert b['area'] == 'INYECION'
    assert b['usuario'] == 'marcos'
    # La v1 traia el cliente; se cae por estar relleno en el 5,8% de los bonos.
    assert 'idcliente' not in b


def test_marca_el_bono_que_tiene_fichaje_abierto(monkeypatch):
    """Un bono ACTIVO sin fichaje es trabajo en pausa, y la tarjeta lo destaca."""
    erp(monkeypatch,
        bonos=[fila(6726, 10), fila(6727, 20)],
        abiertas=[{'idorden': 6726, 'idbono': 10}])
    bonos = consultor.get_consultor_bonos(estado_orden=1, estado_bono=1, matricula=None)['bonos']

    assert [b['tiene_fichaje_activo'] for b in bonos] == [True, False]


def test_sin_bonos_no_se_pregunta_por_los_fichajes(monkeypatch):
    """La pantalla lanza cinco peticiones a la vez y cuatro suelen venir vacias:
    no hay que gastar una consulta mas para marcar una lista vacia."""
    cap = erp(monkeypatch, bonos=[])
    consultor.get_consultor_bonos(estado_orden=1, estado_bono=2, matricula=None)

    assert len(cap['consultas']) == 1


def test_los_filtros_se_montan_solo_cuando_se_piden(monkeypatch):
    cap = erp(monkeypatch, bonos=[])
    consultor.get_consultor_bonos(estado_orden=1, estado_bono=None, matricula=None)
    assert 'ob.IdEstado = :estado_bono' not in cap['consultas'][0]
    assert 'ob.Matricula = :matricula' not in cap['consultas'][0]
    assert cap['params'][0] == {'estado_orden': 1}

    cap = erp(monkeypatch, bonos=[])
    consultor.get_consultor_bonos(estado_orden=1, estado_bono=3, matricula='107')
    assert 'ob.IdEstado = :estado_bono' in cap['consultas'][0]
    assert 'ob.Matricula = :matricula' in cap['consultas'][0]
    assert cap['params'][0] == {'estado_orden': 1, 'estado_bono': 3, 'matricula': '107'}


def test_llamada_directa_sin_argumentos_no_cuela_el_objeto_Query(monkeypatch):
    """Llamada como funcion (tests, scripts) FastAPI no resuelve los defaults y
    llegan como objetos `Query`, que son truthy. Sin normalizarlos, `matricula`
    se colaba en los parametros y el driver contestaba "Invalid parameter type"."""
    cap = erp(monkeypatch, bonos=[])
    consultor.get_consultor_bonos()

    assert cap['params'][0] == {'estado_orden': 1}
    assert 'ob.Matricula = :matricula' not in cap['consultas'][0]


def test_la_maquina_se_une_con_LEFT(monkeypatch):
    """17 de los 822 bonos no declaran matricula. El JOIN del original los
    borraba de la pantalla justo por no saber en que maquina van."""
    cap = erp(monkeypatch, bonos=[])
    consultor.get_consultor_bonos(estado_orden=1, estado_bono=None, matricula=None)

    assert 'LEFT JOIN Articulos a_matricula' in cap['consultas'][0]


def test_un_bono_sin_maquina_no_desaparece(monkeypatch):
    erp(monkeypatch, [fila(matricula=None, maquina=None)])
    b = consultor.get_consultor_bonos(estado_orden=1, estado_bono=None, matricula=None)['bonos'][0]

    assert b['matricula'] == ''
    assert b['descrip_matricula'] == ''


def test_el_desplegable_no_ofrece_maquinas_sin_bonos(monkeypatch):
    """Sale del mismo universo que la pantalla: ofrecer una maquina que al
    elegirla no devuelve nada es peor que no ofrecerla."""
    cap = erp(monkeypatch, bonos=[])
    monkeypatch.setattr(consultor, '_erp',
                        lambda q, p: cap['consultas'].append(q) or cap['params'].append(p) or [])
    consultor.get_consultor_matriculas()

    assert 'o.IdEstado IN (:activa, :bloqueada)' in cap['consultas'][0]
    assert cap['params'][0] == {'activa': 1, 'bloqueada': 3}
