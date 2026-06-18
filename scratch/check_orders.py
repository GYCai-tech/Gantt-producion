import sys
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()
from app.db import get_engine
from sqlalchemy import text
engine = get_engine()
with engine.connect() as conn:
    r = conn.execute(text("""
        SELECT idorden, idbono, idempleado, situacion, estado_bono, estado_orden,
               fichaje_activo_desde, fase, min_estimados
        FROM analytics.v_asignaciones_empleado
        WHERE idorden IN ('5820', '5976')
        ORDER BY idorden, idbono, idempleado
    """))
    for row in r.mappings():
        print(dict(row))
