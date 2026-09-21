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
    from app.erp import lecturas

    capturado = {}

    def erp(query, params):
        capturado.update(query=query, params=params)
        return []
    monkeypatch.setattr(lecturas, 'consultar', erp)
    lecturas.leer_censo_empleados()

    assert 'ed.IdDepartamento = :departamento' in capturado['query']
    assert capturado['params'] == {'departamento': 6}


def test_los_operarios_salen_en_orden_alfabetico_de_verdad(monkeypatch):
    """`sorted()` a secas compara por codigo Unicode, que NO es alfabetico: las
    mayusculas van antes que todas las minusculas y las tildes despues de la Z.

    Con los 25 operarios reales del ERP eso ponia "ETT1" delante de "Elias",
    "JOSE RAMON" delante de "Javier" y dejaba a "marcos" el ultimo de la lista
    solo por ir en minuscula."""
    from app.erp import lecturas
    from app.services import produccion

    censo = [
        {'idempleado': 1, 'nombre': 'marcos', 'apellidos': 'Bello Marquina'},
        {'idempleado': 2, 'nombre': 'ETT1', 'apellidos': 'ETT1'},
        {'idempleado': 3, 'nombre': 'Elías', 'apellidos': 'Calviño Ferro'},
        {'idempleado': 4, 'nombre': 'JOSE RAMON', 'apellidos': 'ALVAREZ DACOBA'},
        {'idempleado': 5, 'nombre': 'Javier', 'apellidos': 'Atanes Pérez'},
        {'idempleado': 6, 'nombre': 'Óscar', 'apellidos': 'González Garza'},
    ]
    monkeypatch.setattr(lecturas, 'leer_censo_empleados', lambda: censo)
    monkeypatch.setattr(lecturas, 'leer_areas_empleado', lambda: [])

    assert [g['nombre'] for g in produccion.censo(vista='empleado')] == [
        'Elías Calviño Ferro',
        'ETT1 ETT1',
        'Javier Atanes Pérez',
        'JOSE RAMON ALVAREZ DACOBA',
        'marcos Bello Marquina',
        'Óscar González Garza',
    ]


def test_la_clave_alfabetica_ignora_mayusculas_y_tildes():
    """Las dos cosas que rompian el orden, por separado."""
    from app.services.produccion import alfabetico

    assert alfabetico('Óscar') == alfabetico('oscar')
    assert alfabetico('ETT1') < alfabetico('Isaac')
    #  Una vocal con tilde ordena donde su vocal, no despues de la Z.
    assert alfabetico('Álvaro') < alfabetico('Beatriz')
