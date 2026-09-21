"""Caracterización: la salida de las tres rutas no puede cambiar al mover código.

Es la red del refactor. Con la entrada y la hora fijas de `tests/escenario.py`,
`/api/items`, `/api/grupos` y `/api/plan` tienen que devolver EXACTAMENTE el
mismo JSON antes y después de extraer el acceso al ERP y los cálculos.

El golden vive en `tests/golden/*.json`, fuera del código que se mueve: si un
día hay que regenerarlo, `REGENERAR=1 pytest tests/test_contratos_golden.py`
lo reescribe -- y el diff en git es la prueba de qué cambió y por qué. Un
golden que se regenera sin mirar el diff no protege de nada.
"""
import datetime
import decimal
import json
import os
import pathlib

import pytest

from app.routers import plan as plan_router
from app.services import produccion
from tests.escenario import fingir_erp

GOLDEN = pathlib.Path(__file__).parent / "golden"


def _plano(obj):
    """Mismo JSON que sirve la app: `_Encoder` de `app/main.py` en pequeño."""
    if isinstance(obj, decimal.Decimal):
        return float(obj)
    if isinstance(obj, (datetime.date, datetime.datetime)):
        return obj.isoformat()
    raise TypeError(type(obj))


def _comparar(nombre, valor):
    fichero = GOLDEN / f"{nombre}.json"
    texto = json.dumps(valor, default=_plano, ensure_ascii=False,
                       indent=2, sort_keys=True)
    if os.environ.get("REGENERAR"):
        GOLDEN.mkdir(exist_ok=True)
        fichero.write_text(texto + "\n", encoding="utf-8")
        pytest.skip(f"golden regenerado: {fichero.name}")
    assert fichero.exists(), (
        f"falta {fichero}. Generalo con REGENERAR=1 pytest {__file__}")
    esperado = fichero.read_text(encoding="utf-8").strip()
    assert texto == esperado, (
        f"{nombre} ha cambiado. Si el cambio es intencionado, revisa el diff "
        f"y regenera con REGENERAR=1.")


@pytest.mark.parametrize("vista", ["empleado", "maquina"])
def test_items_no_cambia(monkeypatch, vista):
    fingir_erp(monkeypatch)
    items = produccion.calcular_items(vista,
                                      desde=datetime.datetime(2026, 9, 7),
                                      hasta=datetime.datetime(2026, 9, 9))
    _comparar(f"items_{vista}", items)


@pytest.mark.parametrize("vista", ["empleado", "maquina"])
def test_grupos_no_cambia(monkeypatch, vista):
    fingir_erp(monkeypatch)
    _comparar(f"grupos_{vista}", produccion.censo(vista))


@pytest.mark.parametrize("vista", ["empleado", "maquina"])
def test_plan_no_cambia(monkeypatch, vista):
    fingir_erp(monkeypatch)
    # `plan.py` congela su propio reloj: importa `date`/`datetime` por su
    # cuenta, así que parchear los de `api` no le llega.
    monkeypatch.setattr(plan_router, "datetime", produccion.datetime)
    monkeypatch.setattr(plan_router, "date", produccion.date)
    _comparar(f"plan_{vista}", plan_router.get_plan(dias=3, vista=vista))
