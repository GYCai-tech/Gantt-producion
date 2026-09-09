import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")

_STATIC_DIR = "static"


def _static_v() -> str:
    """Versión de los estáticos = mtime del fichero más reciente de /static.

    Va como `?v=` en los <script>/<link> para romper la caché del navegador.
    Sin esto, al desplegar un cambio de JS el navegador se queda con la copia
    vieja (StaticFiles manda ETag pero no Cache-Control, así que el navegador
    la cachea por heurística y no revalida): la plantilla nueva se ve, pero el
    JS viejo no tiene las funciones que la plantilla llama y los botones nuevos
    quedan muertos sin ningún error visible.

    Se calcula en cada petición a propósito: en desarrollo basta con recargar
    para ver el cambio, y el coste es recorrer un puñado de ficheros."""
    ultimo = 0.0
    for raiz, _, ficheros in os.walk(_STATIC_DIR):
        for f in ficheros:
            try:
                ultimo = max(ultimo, os.path.getmtime(os.path.join(raiz, f)))
            except OSError:
                pass
    return str(int(ultimo))


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    # Firma request-first: la forma antigua (name primero) ya no la acepta
    # Starlette y se traga el dict de contexto como si fuera el nombre.
    return templates.TemplateResponse(
        request, "index.html", {"current_page": "planificador", "static_v": _static_v()}
    )


@router.get("/fiabilidad", response_class=HTMLResponse)
def fiabilidad(request: Request):
    return templates.TemplateResponse(
        request, "fiabilidad.html", {"current_page": "fiabilidad", "static_v": _static_v()}
    )
