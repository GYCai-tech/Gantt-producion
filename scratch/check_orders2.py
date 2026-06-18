import sys
sys.path.insert(0, '.')
from dotenv import load_dotenv
load_dotenv()
from app.db import get_engine
from sqlalchemy import text
engine = get_engine()
with engine.connect() as conn:
    # Maquinas
    r = conn.execute(text("""
        SELECT idorden, idbono, matricula, situacion, estado_bono, estado_orden, fichaje_activo_desde
        FROM core.fact_asignaciones_maquina
        WHERE idorden IN ('5820', '5976')
        ORDER BY idorden, idbono
    """))
    print("=== MAQUINAS ===")
    for row in r.mappings():
        print(dict(row))

    # Cuantas veces aparece cada orden en los items devueltos por la API (via queries activas/programadas)
    print("\n=== EMPLEADOS - todos los bonos activos (estado_bono=1) ===")
    r2 = conn.execute(text("""
        SELECT idorden, idbono, idempleado, estado_bono, situacion, fichaje_activo_desde
        FROM analytics.v_asignaciones_empleado
        WHERE estado_bono = 1
        ORDER BY idorden, idbono
        LIMIT 5
    """))
    for row in r2.mappings():
        print(dict(row))
