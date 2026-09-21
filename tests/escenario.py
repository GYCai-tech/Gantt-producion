"""Un escenario de taller fijo, compartido por los tests de caracterización.

Existe para el refactor: congela una entrada y una hora, de modo que
`/api/items`, `/api/grupos` y `/api/plan` tengan una salida que se pueda
comparar byte a byte antes y después de mover el código de sitio.

Los puntos de parcheo viven AQUÍ y no en cada test a propósito. Cuando una
lectura se mude a `app/erp/`, se cambia `fingir_erp()` en un solo sitio y los
golden siguen valiendo: lo que se compara es la salida, no por dónde pasa.
"""
from datetime import date, datetime

from app.erp import cache, lecturas
from app.services import produccion

#  Un lunes a media mañana: hay jornada por delante y cola que encadenar.
AHORA = datetime(2026, 9, 7, 12)

MEDIAS = {
    "articulo": {"A": {"n": 5, "minutos": 500.0, "piezas": 250.0}},
    "trabajo":  {1: {"n": 5, "minutos": 500.0, "piezas": 250.0}},
    "maquina":  {"M1": {"n": 9, "minutos": 900.0, "piezas": 300.0}},
}

TEORICOS = {(1, 10): (1, 2.0), (2, 10): (1, 1.5)}


class Reloj(datetime):
    @classmethod
    def now(cls, tz=None):
        return AHORA


class Fecha(date):
    @classmethod
    def today(cls):
        return AHORA.date()


def _linea(orden, bono_id, linea, empleado, maquina, *, abierta, inicio, fin,
           operacion=0, piezas=100):
    return {
        "idorden": orden, "idbono": bono_id, "idlinea": linea,
        "idoperacion": operacion, "idempleado": empleado,
        "empleado": f"Operario {empleado}", "matricula": maquina,
        "descrip_maquina": f"Maquina {maquina}", "area": "CHAPA",
        "descrip_salida": "Articulo", "idarticulo_salida": "A",
        "idtrabajo": 1, "piezas_a_fabricar": piezas,
        "fecha": inicio, "inicio": inicio, "fin": fin, "abierta": abierta,
    }


def _bono(orden, empleado, maquina, *, cantidad=100, semaforo="disponible",
          secuencia=1, arrancado=False, hechas=0, montado=False):
    return {
        "idorden": orden, "idbono": 10, "idempleado": empleado,
        "empleado": f"Operario {empleado}", "matricula": maquina,
        "descrip_maquina": f"Maquina {maquina}", "idtrabajo": 1,
        "descrip_salida": "Articulo", "idarticulo_salida": "A", "area": "CHAPA",
        "piezas_a_fabricar": cantidad, "fabricadas": hechas,
        "ordenar": secuencia, "semaforo": semaforo,
        "arrancado": arrancado, "montado": montado,
        "ultimo_fichaje": datetime(2026, 9, 7, 9) if arrancado else None,
    }


#  Una línea cerrada (trabajo hecho), una abierta (en curso) y dos bonos en
#  cola, uno de ellos bloqueado para que el semáforo tenga algo que ordenar.
LINEAS_CERRADAS = [
    _linea(1, 10, 1, 1, "M1", abierta=False,
           inicio=datetime(2026, 9, 7, 7), fin=datetime(2026, 9, 7, 9)),
]
LINEAS_ABIERTAS = [
    _linea(2, 10, 1, 2, "M2", abierta=True,
           inicio=datetime(2026, 9, 7, 11), fin=None),
]
COLA = [
    _bono(3, 1, "M1", secuencia=1),
    _bono(4, 2, "M2", secuencia=2, semaforo="bloqueada"),
    _bono(5, 3, "M1", secuencia=3, arrancado=True, hechas=40),
]
AVANCE = {
    (2, 10): {"minutos": 60.0, "min_produccion": 60.0, "min_montaje": 0.0,
              "piezas": 30.0, "operarios": 1, "montando": 1},
}

#  `get_grupos` lanza tres consultas distintas según la vista. El escenario
#  responde a cada una por lo que pide, no por el orden en que llega.
CENSO = [
    {"idempleado": 1, "nombre": "Ana",   "apellidos": "Boo"},
    {"idempleado": 2, "nombre": "Bruno", "apellidos": "Cela"},
    #  Con tilde y en minúscula a propósito: vigila `alfabetico()`.
    {"idempleado": 3, "nombre": "chus",  "apellidos": "Ávila"},
]
AREAS_EMPLEADO = [
    {"idempleado": 1, "area": "CHAPA"},
    {"idempleado": 2, "area": "ESTRUCTURAS"},
    {"idempleado": 3, "area": "CHAPA"},
]
MAQUINAS = [
    {"id": "M1", "nombre": "Maquina M1", "area": "CHAPA"},
    {"id": "M2", "nombre": "Maquina M2", "area": "ESTRUCTURAS"},
]


#  Sin histórico de montajes: el escenario fija el setup por escandallo.
MONTAJES = {"trabajo": {}, "maquina": {}}


def fingir_erp(monkeypatch):
    """Deja la app leyendo del escenario y no del ERP.

    ÚNICO sitio que conoce los nombres de las funciones de lectura.

    Se parchea a nivel de FUNCIÓN (`lecturas.leer_cola`) y no de `consultar`:
    tres de las consultas del ERP contienen `ed.Apellidos`, así que despachar
    por el texto del SQL devolvería el censo cuando se pide la cola.
    """
    monkeypatch.setattr(produccion, "datetime", Reloj)
    monkeypatch.setattr(produccion, "date", Fecha)
    monkeypatch.setattr(lecturas, "leer_lineas", lambda *a: [dict(l) for l in LINEAS_CERRADAS])
    monkeypatch.setattr(lecturas, "leer_abiertas", lambda *a: [dict(l) for l in LINEAS_ABIERTAS])
    monkeypatch.setattr(lecturas, "leer_cola", lambda: [dict(b) for b in COLA])
    monkeypatch.setattr(lecturas, "leer_paradas", lambda: {})
    monkeypatch.setattr(lecturas, "leer_censo_maquinas", lambda: list(MAQUINAS))
    monkeypatch.setattr(lecturas, "leer_censo_empleados", lambda: list(CENSO))
    monkeypatch.setattr(lecturas, "leer_areas_empleado", lambda: list(AREAS_EMPLEADO))
    monkeypatch.setattr(lecturas, "leer_avance", lambda *a: {k: dict(v) for k, v in AVANCE.items()})
    monkeypatch.setattr(cache, "cargar_estimaciones",
                        lambda: (dict(TEORICOS), MEDIAS, MONTAJES))
