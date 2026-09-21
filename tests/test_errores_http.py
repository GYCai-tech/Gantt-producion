"""Un ERP caído tiene que llegar al navegador como 503, por todas las rutas.

Antes del refactor cada lectura construía su propia `HTTPException`. Ahora
`app.erp` lanza `ErpNoDisponible` sin saber lo que es el HTTP y la traducción
vive en un único manejador de `app/main.py`. Eso quita repetición, pero mueve
el riesgo: si el manejador no estuviera registrado, TODAS las rutas pasarían de
503 a 500 a la vez y en silencio.

503 y 500 no son lo mismo para quien mira la pantalla: 503 es "el origen de
datos no responde" —que es la verdad, y lo que el Gantt enseña en su aviso—,
y 500 es "esta aplicación está rota". Por eso se vigila aquí.
"""
import pytest
from fastapi.testclient import TestClient

from app.erp import cache, lecturas
from app.erp.cliente import ErpNoDisponible
from app.main import app
from app.routers import bonos, consultor, duplicados, ordenes


def _revienta(*a, **k):
    raise ErpNoDisponible("No se pudo consultar el ERP: OperationalError")


@pytest.mark.parametrize("ruta", [
    "/api/lineas",
    "/api/grupos?vista=empleado",
    "/api/grupos?vista=maquina",
    "/api/items?vista=empleado",
    "/api/avisos?vista=empleado",
    "/api/plan?dias=1",
])
def test_un_fallo_del_erp_llega_al_frontend_como_503(monkeypatch, ruta):
    for modulo, nombre in (
        (lecturas, "leer_lineas"), (lecturas, "leer_abiertas"),
        (lecturas, "leer_cola"), (lecturas, "leer_avance"),
        (lecturas, "leer_censo_empleados"), (lecturas, "leer_censo_maquinas"),
        (lecturas, "leer_areas_empleado"),
        (cache, "cargar_estimaciones"),
    ):
        monkeypatch.setattr(modulo, nombre, _revienta)

    r = TestClient(app).get(ruta)
    assert r.status_code == 503, f"{ruta} devolvió {r.status_code}"
    # El frontend enseña `detail` tal cual en su aviso rojo.
    assert "detail" in r.json()


@pytest.mark.parametrize("modulo,ruta", [
    (bonos,      "/api/bonos-stock"),
    (ordenes,    "/api/ordenes-no-asignadas"),
    (duplicados, "/api/bonos-duplicados"),
    (consultor,  "/api/bonos"),
])
def test_las_demas_pantallas_tambien_dan_503(monkeypatch, modulo, ruta):
    """Estas cuatro llamaban a `_erp` importado de `api.py`, que lanzaba la
    `HTTPException` por ellas. Ahora reciben `ErpNoDisponible`: sin el
    manejador global, cada una habría empezado a devolver un 500."""
    monkeypatch.setattr(modulo, "_erp", _revienta)
    r = TestClient(app).get(ruta)
    assert r.status_code == 503, f"{ruta} devolvió {r.status_code}"


def test_las_paginas_html_siguen_sirviendose_con_el_erp_caido(monkeypatch):
    """Las plantillas no tocan el ERP: tienen que responder 200 para que el
    JavaScript pueda cargar y enseñar el aviso. Si la página también fallara,
    el usuario vería un error del navegador en vez del mensaje de la app."""
    monkeypatch.setattr(lecturas, "leer_lineas", _revienta)
    cliente_http = TestClient(app)
    for pagina in ("/", "/planificacion", "/bonos", "/consultor-bonos"):
        assert cliente_http.get(pagina).status_code == 200
