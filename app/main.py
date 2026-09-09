import datetime
import decimal
import json

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.routers import api, fiabilidad, pages, plan


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

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(pages.router)
app.include_router(api.router)
app.include_router(fiabilidad.router)
app.include_router(plan.router)
