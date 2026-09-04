import os

import sqlalchemy
from dotenv import load_dotenv

load_dotenv(override=True)

_erp_engine = None


def get_erp_engine():
    """Engine hacia el ERP de producción (SQL Server GOMEZYCRESPO).

    Es la única fuente de datos de la app y se usa SIEMPRE en solo lectura:
    aquí no se escribe nunca. Se lee del ERP en vivo y no de la réplica
    analítica porque `Ordenes_Bonos_Lineas` (el detalle línea a línea de cada
    bono, con su hora de inicio y fin) no está replicado en PostgreSQL.

    El engine se cachea en una global de módulo y se crea de forma perezosa,
    así la app importa y arranca sin necesidad de que el ERP esté accesible.
    """
    global _erp_engine
    if _erp_engine is None:
        host = os.getenv("SQLSERVER_HOST")
        db   = os.getenv("SQLSERVER_DB")
        user = os.getenv("SQLSERVER_USER")
        pwd  = os.getenv("SQLSERVER_PASS")
        url = (
            f"mssql+pyodbc://{user}:{pwd}@{host}/{db}"
            "?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes"
        )
        _erp_engine = sqlalchemy.create_engine(
            url, pool_pre_ping=True, pool_size=2, max_overflow=2
        )
    return _erp_engine
