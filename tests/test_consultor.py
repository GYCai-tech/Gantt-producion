"""El Consultor de Bonos, sin tocar el ERP.

La consulta es la de la v1 LITERAL, restaurada por peticion expresa: la pantalla
tiene que enseñar exactamente lo que enseñaba antes. Lo unico que no se pudo
portar es de donde sale el "sin fichar": la v1 lo leia de PostgreSQL
(analytics.v_asignaciones_empleado) y aqui se deriva del ERP en vivo, porque la
v2 no tiene esa conexion.
"""
from app.routers import consultor


def fila(orden=6726, bono=10, estado=1, matricula='107 ', maquina='Manual Bolsas-2 ',
         articulo='CABLE CONTROL ', area='INYECION ', usuario='marcos ',
         cliente='0011000001 '):
    return {'idorden': orden, 'idbono': bono, 'estado_bono': estado,
            'matricula': matricula, 'descrip_matricula': maquina,
            'idcliente': cliente, 'idarticulo_orden': '30806013 ',
            'descrip_articulo': articulo, 'area': area, 'usuario': usuario}


def erp(monkeypatch, bonos, abiertas=()):
    """Sustituye `_erp`, que el endpoint llama dos veces: bonos y fichajes."""
    capturado = {'consultas': [], 'params': []}

    def fake(query, params):
        capturado['consultas'].append(query)
        capturado['params'].append(params)
        return list(abiertas) if 'Hfinal IS NULL' in query else list(bonos)

    monkeypatch.setattr(consultor, '_erp', fake)
    return capturado


def test_una_fila_por_bono_con_textos_limpios(monkeypatch):
    erp(monkeypatch, [fila()])
    b = consultor.get_consultor_bonos(estado_orden=1, estado_bono=None, matricula=None)['bonos'][0]

    assert b['idorden'] == 6726
    assert b['matricula'] == '107'
    assert b['descrip_matricula'] == 'Manual Bolsas-2'
    assert b['area'] == 'INYECION'
    assert b['usuario'] == 'marcos'
    # El cliente esta relleno en el 5,8% de los bonos, pero la v1 lo enseñaba.
    assert b['idcliente'] == '0011000001'


def test_la_consulta_es_la_de_la_v1_literal(monkeypatch):
    """Se restauro tal cual, con dos efectos que llegaron a cambiarse y se
    revirtieron a proposito: el JOIN a la matricula deja fuera los bonos que no
    la declaran (17 de 822), y sin GROUP BY un bono que repite articulo en
    Ordenes_Bonos_Salidas sale duplicado."""
    cap = erp(monkeypatch, bonos=[])
    consultor.get_consultor_bonos(estado_orden=1, estado_bono=None, matricula=None)
    q = cap['consultas'][0]

    assert 'JOIN Articulos a_matricula  ON ob.Matricula    = a_matricula.IdArticulo' in q
    assert 'LEFT JOIN Articulos a_matricula' not in q
    assert 'GROUP BY' not in q
    assert 'o.IdCliente' in q
    assert 'ORDER BY o.IdOrden DESC' in q


def test_marca_el_bono_que_tiene_fichaje_abierto(monkeypatch):
    """Un bono ACTIVO sin fichaje es trabajo en pausa, y la fila lo destaca."""
    erp(monkeypatch,
        bonos=[fila(6726, 10), fila(6727, 20)],
        abiertas=[{'idorden': 6726, 'idbono': 10}])
    bonos = consultor.get_consultor_bonos(estado_orden=1, estado_bono=1, matricula=None)['bonos']

    assert [b['tiene_fichaje_activo'] for b in bonos] == [True, False]


def test_sin_bonos_no_se_pregunta_por_los_fichajes(monkeypatch):
    """No hay que gastar una consulta mas para marcar una lista vacia."""
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


def test_el_desplegable_no_ofrece_maquinas_sin_bonos(monkeypatch):
    """Sale del mismo universo que la pantalla: ofrecer una maquina que al
    elegirla no devuelve nada es peor que no ofrecerla."""
    cap = erp(monkeypatch, bonos=[])
    monkeypatch.setattr(consultor, '_erp',
                        lambda q, p: cap['consultas'].append(q) or cap['params'].append(p) or [])
    consultor.get_consultor_matriculas()

    assert 'o.IdEstado IN (:activa, :bloqueada)' in cap['consultas'][0]
    assert cap['params'][0] == {'activa': 1, 'bloqueada': 3}
