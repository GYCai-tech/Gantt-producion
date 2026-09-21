import datetime
import decimal
import json

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.erp.cliente import ErpNoDisponible
from app.routers import api, bonos, consultor, duplicados, ordenes, pages, plan


class _Encoder(json.JSONEncoder):
    """El ERP devuelve Decimal (cantidades) y datetime (horas); ninguno de los
    dos es serializable por el json de la stdlib."""

    def default(self, obj):
        if isinstance(obj, decimal.Decimal):
            return float(obj)
        if isinstance(obj, (datetime.date, datetime.datetime)):
            return obj.isoformat()
        return super().default(obj)


class _JSONResponse(JSONResponse):
    def render(self, content) -> bytes:
        return json.dumps(content, cls=_Encoder, ensure_ascii=False).encode("utf-8")


app = FastAPI(
    title="GYC — Seguimiento de Producción",
    version="2.0",
    default_response_class=_JSONResponse,
)

#  El ERP caido es un 503, no un 500: el servicio existe y funciona, lo que
#  falta es el origen de datos. Se traduce AQUI, en el borde HTTP, y no en cada
#  ruta: `app.erp` lanza `ErpNoDisponible` sin saber que existe el HTTP, y asi
#  ninguna ruta puede olvidarse de traducirlo y colar un 500.
#
#  El detalle es el texto que ya traia la excepcion, identico al que servia la
#  version anterior: el frontend lo ensena tal cual.
@app.exception_handler(ErpNoDisponible)
def _erp_no_disponible(request: Request, exc: ErpNoDisponible):
    return _JSONResponse(status_code=503, content={"detail": str(exc)})


app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(pages.router)
app.include_router(api.router)
app.include_router(plan.router)
app.include_router(ordenes.router)
app.include_router(duplicados.router)
app.include_router(bonos.router)
app.include_router(consultor.router)
