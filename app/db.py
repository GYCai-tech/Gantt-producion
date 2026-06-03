import os
import sqlalchemy
from dotenv import load_dotenv

load_dotenv(override=True)

_engine = None


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
