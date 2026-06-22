"""Smoke test: la app debe poder importarse y registrar sus rutas sin
necesitar una conexion real a la base de datos (get_engine() es lazy)."""
from app.main import app


def test_la_app_importa_y_registra_las_rutas_esperadas():
    rutas = {r.path for r in app.routes}
    esperadas = {
        "/",
        "/historico-produccion",
        "/api/grupos",
        "/api/items",
        "/api/refrescar",
        "/api/refrescar/{flow_run_id}",
    }
    assert esperadas.issubset(rutas)
