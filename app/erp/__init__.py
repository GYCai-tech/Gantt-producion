"""Todo el acceso al ERP de la app, en un sitio.

El ERP (SQL Server GOMEZYCRESPO) es la única fuente de datos y se usa SIEMPRE
en solo lectura. Este paquete es la frontera: por debajo hay SQL y conexiones;
por encima, diccionarios de Python. Nada de `app/erp/` sabe qué es una
respuesta HTTP.

    cliente.py    la conexión y `ErpNoDisponible`
    consultas.py  el SQL, como texto, y las constantes que lo parametrizan
    lecturas.py   una función por pregunta que la app le hace al ERP
    cache.py      lo que se cachea, con su TTL y su degradación

QUÉ HACER CON UN FALLO DEL ERP. La decisión no es de este paquete, pero el
reparto que espera la app es este:

    ErpNoDisponible desde `consultar`        →  503
    ErpNoDisponible desde `leer_avance`      →  503
    `cargar_teoricos` / `cargar_estimaciones` / `cargar_semaforo`
                                             →  reutilizan su caché caducada
    `leer_paradas` / `leer_ausencias`        →  devuelven {}
"""
from app.erp.cache import (ESTIMA_TTL_S, SEMAFORO_TTL_S, cargar_estimaciones,
                           cargar_semaforo, cargar_teoricos)
from app.erp.cliente import ErpNoDisponible, conexion, consultar, ejecutar
from app.erp.lecturas import (cuando_falta, leer_abiertas, leer_areas_empleado,
                              leer_ausencias, leer_avance, leer_censo_empleados,
                              leer_censo_maquinas, leer_cola, leer_lineas,
                              leer_paradas, nombre_completo, normalizar_lineas)

__all__ = [
    "ErpNoDisponible", "conexion", "consultar", "ejecutar",
    "leer_lineas", "leer_abiertas", "leer_paradas", "leer_cola", "leer_avance",
    "leer_ausencias", "leer_censo_empleados", "leer_areas_empleado",
    "leer_censo_maquinas", "normalizar_lineas", "nombre_completo",
    "cuando_falta",
    "cargar_teoricos", "cargar_estimaciones", "cargar_semaforo",
    "ESTIMA_TTL_S", "SEMAFORO_TTL_S",
]
