"""La única puerta de salida hacia el ERP.

Aquí NO se sabe nada de HTTP a propósito. Un fallo del ERP sale de este módulo
como `ErpNoDisponible`, y es la capa de routers la que decide si eso es un 503,
una caché caducada que se reutiliza o un diccionario vacío. Esa decisión es de
cada pantalla, no de la conexión.

El engine es el de `app.db.get_erp_engine()`: uno solo para toda la app, con su
pool. Aquí no se crea ninguno.

El ERP es de SOLO LECTURA. Por este módulo no se escribe nunca.
"""
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.db import get_erp_engine


class ErpNoDisponible(Exception):
    """El ERP no se pudo consultar. La capa HTTP decide qué hacer con esto.

    El mensaje es el que acaba viendo el usuario si quien llama lo traduce a un
    503, así que lleva el tipo de error de SQLAlchemy y no su texto completo:
    el texto trae cadenas de conexión y nombres de servidor.
    """


@contextmanager
def conexion():
    """Una conexión al ERP con los errores ya traducidos a `ErpNoDisponible`.

    Sirve para las lecturas que necesitan varias consultas sobre la MISMA
    conexión (las medias históricas y los montajes van juntos: si la segunda
    falla, se descarta también la primera y se reutiliza la caché entera).
    """
    try:
        with get_erp_engine().connect() as conn:
            yield conn
    except SQLAlchemyError as e:
        raise ErpNoDisponible(
            f"No se pudo consultar el ERP: {e.__class__.__name__}") from e


def ejecutar(sentencia, params: dict | None = None) -> list[dict]:
    """Como `consultar`, pero con una sentencia de SQLAlchemy ya construida.

    La consulta del avance necesita `bindparam(..., expanding=True)` para meter
    una lista de órdenes en un `IN`, y eso no se puede expresar con texto
    suelto.
    """
    with conexion() as conn:
        return [dict(r) for r in conn.execute(sentencia, params or {}).mappings()]


def consultar(query: str, params: dict | None = None) -> list[dict]:
    """Lanza `query` contra el ERP y devuelve las filas como diccionarios.

    Lanza `ErpNoDisponible` si el ERP no responde. Quien llama decide: los
    routers lo convierten en 503 y las lecturas que pueden degradar lo capturan
    y siguen con lo que tenían.
    """
    return ejecutar(text(query), params)
