"""Ejecuta la agregación con SQLite adaptando solo dos funciones de T-SQL.

Verifica las reglas de consumo; la compatibilidad con SQL Server/ODBC queda
pendiente de la validación en el entorno del ERP.
"""
from contextlib import contextmanager
from datetime import datetime

from sqlalchemy import bindparam, create_engine, event, text
from sqlalchemy.exc import SQLAlchemyError
import pytest
from fastapi import HTTPException

from app.routers import api

AHORA = datetime(2026, 9, 7, 12)


@pytest.fixture
def erp(monkeypatch):
    engine = create_engine('sqlite://')
    @event.listens_for(engine, 'connect')
    def funciones(conn, _):
        def datediff(unidad, inicio, fin):
            a = datetime.fromisoformat(inicio).replace(second=0, microsecond=0)
            b = datetime.fromisoformat(fin).replace(second=0, microsecond=0)
            return int((b - a).total_seconds() // 60)
        conn.create_function('DATEDIFF', 3, datediff)
    with engine.begin() as c:
        c.execute(text('''CREATE TABLE Ordenes_Bonos_Lineas (
            IdOrden INT, IdBono INT, IdEmpleado INT, IdOperacion INT,
            Hinicial TEXT, Hfinal TEXT, TotalPiezas REAL
        )'''))
    class Conexion:
        def __init__(self, conn):
            self.conn = conn
        def execute(self, consulta, params):
            sql = consulta.text.replace('DATEDIFF(minute,', "DATEDIFF('minute',")
            sql = sql.replace('ISNULL(', 'IFNULL(')
            params = {k: v.isoformat(' ') if isinstance(v, datetime) else v for k, v in params.items()}
            return self.conn.execute(text(sql).bindparams(bindparam('ordenes', expanding=True)), params)
    class ERP:
        @contextmanager
        def connect(self):
            with engine.connect() as c:
                yield Conexion(c)
    monkeypatch.setattr(api, 'get_erp_engine', ERP)
    yield engine
    engine.dispose()


def insertar(engine, filas):
    with engine.begin() as c:
        c.execute(text('''INSERT INTO Ordenes_Bonos_Lineas
            VALUES (1, 10, :empleado, :operacion, :inicio, :fin, :piezas)'''), filas)


def test_consumo_y_operarios_separan_preparacion_de_fabricacion(erp):
    insertar(erp, [
        dict(empleado=1, operacion=1, inicio='2026-09-07 10:00:00', fin='2026-09-07 11:00:00', piezas=0),
        dict(empleado=1, operacion=0, inicio='2026-09-07 11:00:00', fin='2026-09-07 11:10:00', piezas=10),
        dict(empleado=1, operacion=0, inicio='2026-09-07 11:50:00', fin=None, piezas=0),
        dict(empleado=2, operacion=0, inicio='2026-09-07 11:55:00', fin=None, piezas=0),
        dict(empleado=3, operacion=1, inicio='2026-09-07 11:55:00', fin=None, piezas=0),
        dict(empleado=4, operacion=2, inicio='2026-09-07 10:00:00', fin='2026-09-07 10:05:00', piezas=0),
    ])
    avance = api._avance_por_bono([{'idorden': 1}], AHORA)[(1, 10)]
    # `montando` es 1: el empleado 3 tiene una preparacion abierta. Se cuenta
    # aparte porque mientras solo hay montaje fichado `operarios` da 0 y no
    # habria por quien dividir la fabricacion que viene detras.
    assert avance == {'minutos': 95, 'min_produccion': 25, 'min_montaje': 70,
                      'piezas': 10, 'operarios': 2, 'montando': 1}


def test_fichajes_fantasma_futuros_y_duraciones_negativas_no_inflan_consumo(erp):
    insertar(erp, [
        dict(empleado=1, operacion=0, inicio='2026-01-01 10:00:00', fin=None, piezas=0),
        dict(empleado=2, operacion=0, inicio='2026-09-08 10:00:00', fin=None, piezas=0),
        dict(empleado=3, operacion=0, inicio='2026-09-07 11:00:00', fin='2026-09-07 10:00:00', piezas=0),
    ])
    avance = api._avance_por_bono([{'idorden': 1}], AHORA)[(1, 10)]
    assert avance['min_produccion'] == 0
    assert avance['minutos'] == 0
    assert avance['operarios'] == 1


def test_error_del_erp_no_se_convierte_en_cero_produccion(monkeypatch):
    class ERP:
        def connect(self):
            raise SQLAlchemyError('fallo sintetico')
    monkeypatch.setattr(api, 'get_erp_engine', ERP)
    with pytest.raises(HTTPException) as exc:
        api._avance_por_bono([{'idorden': 1}], AHORA)
    assert exc.value.status_code == 503
