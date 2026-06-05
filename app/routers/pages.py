from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})


@router.get("/historico-produccion", response_class=HTMLResponse)
def historico_produccion(request: Request):
    return templates.TemplateResponse("historico_prod.html", {"request": request})
