"""Smoke test: la app debe importarse y publicar sus rutas sin necesidad de
una conexión real al ERP (get_erp_engine() es perezoso).

Se leen del esquema OpenAPI y no de `app.routes` a propósito: según la versión
de FastAPI, los routers incluidos aparecen ahí envueltos y sin `.path`."""
from app.main import app


def _rutas():
    return set(app.openapi()["paths"])


def test_la_app_publica_las_rutas_esperadas():
    assert {"/", "/api/lineas"}.issubset(_rutas())


def test_no_quedan_rutas_de_la_version_anterior():
    viejas = {
        "/historico-produccion", "/consultor-bonos",
        "/api/grupos", "/api/items", "/api/bonos", "/api/refrescar",
    }
    assert not (_rutas() & viejas)
