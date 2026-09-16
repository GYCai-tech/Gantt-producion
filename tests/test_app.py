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
        "/ordenes-no-asignadas", "/api/ordenes-no-asignadas",
    }.issubset(_rutas())


def test_el_consultor_de_bonos_esta_de_vuelta():
    """Estuvo prohibido a proposito: el reinicio a la v2 dejo main como minimo
    y este test vigilaba que las paginas eliminadas no reaparecieran. El
    Consultor se repone por peticion expresa, asi que la ruta pasa de vetada a
    exigida -- pero portada al ERP, sin la dependencia de PostgreSQL que tenia
    en la v1."""
    assert {"/consultor-bonos", "/api/bonos", "/api/matriculas"}.issubset(_rutas())


def test_no_quedan_rutas_de_las_paginas_eliminadas():
    """Las que siguen sin volver. `/api/bonos` salio de esta lista; el resto no."""
    assert not (_rutas() & {"/historico-produccion", "/fiabilidad", "/api/fiabilidad"})


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
