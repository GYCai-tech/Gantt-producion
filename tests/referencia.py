"""Acceso a la implementación ANTERIOR al refactor, para comparar contra ella.

Durante la extracción de `app/calculos/` se escribieron tests *diferenciales*:
corren el mismo escenario por el módulo nuevo y por el `api.py` de 2.000 líneas
del que salió, y comparan la salida entera. Con eso se comprobó que mover el
código no cambiaba ni un valor — **76 de 76 en verde**.

Esa referencia no se versiona: sería dejar una copia completa de la
implementación vieja dentro de los tests, que envejece sola y no la mantiene
nadie. Se recupera de git cuando hace falta:

    git show e918c7d:app/routers/api.py > tests/_api_v1.py
    pytest tests/test_calculos_*.py

Sin ese fichero, los tests diferenciales se saltan y los de valor concreto que
van a su lado siguen corriendo. La protección permanente del comportamiento no
es ésta, es `tests/test_contratos_golden.py`.
"""
import pytest

try:
    import tests._api_v1 as v1
except ImportError:                                     # pragma: no cover
    v1 = None

#  Para decorar un test diferencial: `@necesita_v1`.
necesita_v1 = pytest.mark.skipif(
    v1 is None,
    reason="falta tests/_api_v1.py — ver el docstring de tests/referencia.py",
)
