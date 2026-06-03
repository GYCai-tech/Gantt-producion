from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from app.routers import pages, api
import decimal, datetime, json


class _Encoder(json.JSONEncoder):
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
    title="GYC — Planificador de Producción",
    version="1.0",
    default_response_class=_JSONResponse,
)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(pages.router)
app.include_router(api.router)
