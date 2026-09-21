"""Capa HTTP del Gantt: validar lo que entra, traducir errores y poco más.

Este fichero llegó a tener 2.000 líneas porque era a la vez el acceso al ERP,
el motor de estimación, el calendario y la API. Ahora cada cosa vive donde
corresponde y aquí solo queda el borde:

    app/erp/        de dónde salen los datos (SQL, conexión, caché, normalización)
    app/calculos/   qué se hace con ellos (estimación, calendario, cola, fusión)
    app/services/   en qué orden se pide y se calcula
    app/routers/    esto: URLs, parámetros y códigos HTTP

**La traducción de errores no está aquí, está en `app/main.py`.** `app.erp`
lanza `ErpNoDisponible` sin saber lo que es un 503, los servicios lo dejan
subir y un manejador de la app lo convierte. Así ninguna ruta puede olvidarse
de traducirlo. El reparto anterior se conserva exacto:

  · lectura imprescindible que falla (líneas, cola, censo, avance) → 503
  · lectura accesoria que falla (paradas, ausencias) → se degrada a vacío
    dentro de `app.erp`, y aquí no llega nada
  · caché que no se puede refrescar → se reutiliza la anterior dentro de
    `app.erp`, y aquí no llega nada
"""
from datetime import date, datetime
from typing import Optional

from fastapi import APIRouter, Query

from app.erp import lecturas
from app.services import produccion

router = APIRouter(prefix="/api")


@router.get("/lineas")
def get_lineas(
    dia: Optional[date] = Query(None, description="Día a consultar (YYYY-MM-DD). Por defecto, hoy."),
):
    """La consulta en crudo, un día. No la usa el Gantt (que va por /items),
    pero es el sitio donde mirar qué está devolviendo el ERP."""
    dia = dia or date.today()
    lineas = lecturas.leer_lineas(dia, dia)
    return {"dia": dia, "ahora": datetime.now(), "total": len(lineas), "lineas": lineas}


@router.get("/grupos")
def get_grupos(vista: str = Query("empleado", pattern="^(maquina|empleado)$")):
    """Las filas del Gantt: el censo completo.

    El frontend carga los grupos UNA vez (y al cambiar de vista), no al navegar
    entre días. Por eso la lista NO depende de la ventana visible: si dependiera
    de fechas, al navegar a otro día habría barras sin fila a la que colgarse y
    desaparecerían sin aviso.
    """
    return produccion.censo(vista)


@router.get("/avisos")
def get_avisos(
    vista: str = Query("empleado", pattern="^(maquina|empleado)$"),
    dia: Optional[date] = Query(None, description="Día visible del Gantt. Por defecto, hoy."),
):
    """Avisos de FILA: los que no son de un bono sino del recurso entero.

    Va aparte de `/items` a propósito, porque no depende de la ventana visible.
    La fila de un operario que no puede empezar nada tiene que salir marcada
    también en un día en el que no se le haya colocado ninguna barra: Juan
    Carlos tiene un solo bono, bloqueado, y le cae el lunes porque los 18 de
    José Manuel ocupan antes la misma máquina de ensamblaje. Colgado de las
    barras, el aviso desaparecía justo en el día en que más falta hace.

    `dia` es el primer día visible del Gantt, y solo lo usan las ausencias.
    """
    return produccion.calcular_avisos(vista, dia)


@router.get("/items")
def get_items(
    vista: str = Query("empleado", pattern="^(maquina|empleado)$"),
    desde: Optional[datetime] = None,
    hasta: Optional[datetime] = None,
):
    """Las barras del Gantt.

    El cálculo vive en `app.services.produccion.calcular_items`, que es el
    mismo que usa `/api/plan`: las dos pantallas tienen que enseñar el mismo
    plan, y comparten el resultado sin que una ruta dependa de ejecutar la otra.
    """
    return produccion.calcular_items(vista, desde, hasta)


# ─────────────────────────────────────────────────────────────────────
#  REFRESCO
# ─────────────────────────────────────────────────────────────────────
#  Ya no hay ETL que lanzar, los datos son del ERP en vivo. Responden
#  COMPLETED al momento para que el botón "Actualizar" siga funcionando:
#  recargar y ya.
# ─────────────────────────────────────────────────────────────────────

@router.post("/refrescar")
def refrescar():
    return {"flow_run_id": "erp-en-vivo", "estado": "COMPLETED"}


@router.get("/refrescar/{flow_run_id}")
def refrescar_estado(flow_run_id: str):
    return {"flow_run_id": flow_run_id, "estado": "COMPLETED"}
