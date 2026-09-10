"""Smoke test: la app debe importarse y publicar sus rutas sin necesidad de
una conexión real al ERP (get_erp_engine() es perezoso).

Se leen del esquema OpenAPI y no de `app.routes` a propósito: según la versión
de FastAPI, los routers incluidos aparecen ahí envueltos y sin `.path`."""
from app.main import app


def _rutas():
    return set(app.openapi()["paths"])


def test_la_app_publica_las_rutas_que_consume_el_frontend():
    # /grupos, /items y /refrescar son las que llama static/js/app.js;
    # /lineas expone la consulta en crudo.
    assert {
        "/", "/api/lineas", "/api/grupos", "/api/items",
        "/api/refrescar", "/api/refrescar/{flow_run_id}",
        "/planificacion", "/api/plan",
    }.issubset(_rutas())


def test_no_quedan_rutas_de_las_paginas_eliminadas():
    assert not (_rutas() & {"/historico-produccion", "/consultor-bonos", "/api/bonos",
                            "/fiabilidad", "/api/fiabilidad"})


def test_el_censo_de_operarios_se_limita_al_departamento_de_planta(monkeypatch):
    """Cuatro de los 29 que salian tienen fichajes en el historico pero no son
    gente de planta -- Gilberto, que mantiene el escandallo, es uno-- y
    ocupaban fila en el Gantt y en la rejilla de carga sin trabajo que
    planificar."""
    from app.routers import api
    capturado = {}

    def erp(query, params):
        capturado.setdefault('consultas', []).append((query, params))
        return []
    monkeypatch.setattr(api, '_erp', erp)
    api.get_grupos(vista='empleado')

    censo, params = capturado['consultas'][0]
    assert 'ed.IdDepartamento = :departamento' in censo
    assert params == {'departamento': 6}
