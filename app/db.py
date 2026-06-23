import os
import sqlalchemy
from dotenv import load_dotenv

load_dotenv(override=True)

_engine = None
_sqlserver_engine = None


def get_engine():
    global _engine
    if _engine is None:
        host = os.getenv("PG_HOST")
        db   = os.getenv("PG_DB")
        user = os.getenv("PG_USER")
        pwd  = os.getenv("PG_PASS")
        port = os.getenv("PG_PORT", "5432")
        url  = f"postgresql+psycopg2://{user}:{pwd}@{host}:{port}/{db}"
        _engine = sqlalchemy.create_engine(url, pool_pre_ping=True, pool_size=5)
    return _engine


def get_sqlserver_engine():
    """ERP origen (GOMEZYCRESPO). Solo para consultas que necesitan estado
    en vivo y no pueden esperar al refresco del ETL (ver Consultor de Bonos)."""
    global _sqlserver_engine
    if _sqlserver_engine is None:
        host = os.getenv("SQLSERVER_HOST")
        db   = os.getenv("SQLSERVER_DB")
        user = os.getenv("SQLSERVER_USER")
        pwd  = os.getenv("SQLSERVER_PASS")
        url  = (
            f"mssql+pyodbc://{user}:{pwd}@{host}/{db}"
            "?driver=ODBC+Driver+18+for+SQL+Server&TrustServerCertificate=yes"
        )
        _sqlserver_engine = sqlalchemy.create_engine(url, pool_pre_ping=True, pool_size=2, max_overflow=1)
    return _sqlserver_engine
