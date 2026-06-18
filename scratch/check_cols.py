import sys, os
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()
from app.db import get_engine
from sqlalchemy import text
engine = get_engine()
with engine.connect() as conn:
    r = conn.execute(text("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_schema='core' AND table_name='fact_asignaciones_maquina'
        ORDER BY ordinal_position
    """))
    cols = [row[0] for row in r]
    print("fact_asignaciones_maquina:", cols)
    
    r2 = conn.execute(text("""
        SELECT column_name 
        FROM information_schema.columns 
        WHERE table_schema='analytics' AND table_name='v_asignaciones_empleado'
        ORDER BY ordinal_position
    """))
    cols2 = [row[0] for row in r2]
    print("v_asignaciones_empleado:", cols2)
