import sys
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()
from app.db import get_engine
from sqlalchemy import text
engine = get_engine()
with engine.connect() as conn:
    r = conn.execute(text("""
        SELECT idorden, idbono, situacion, estado_bono, estado_orden,
               fichaje_activo_desde, fase
        FROM analytics.v_asignaciones_empleado
        WHERE idorden = '5591' AND idbono = 50
    """))
    for row in r.mappings():
        print(dict(row))
