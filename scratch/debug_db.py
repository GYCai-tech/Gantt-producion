import os
import psycopg2
from dotenv import load_dotenv

load_dotenv(override=True)

try:
    conn = psycopg2.connect(
        host=os.getenv("PG_HOST"),
        database=os.getenv("PG_DB"),
        user=os.getenv("PG_USER"),
        password=os.getenv("PG_PASS"),
        port=os.getenv("PG_PORT", "5432")
    )
    cur = conn.cursor()
    
    print("Listing schemas:")
    cur.execute("SELECT schema_name FROM information_schema.schemata")
    for r in cur.fetchall():
        print(f" - {r[0]}")
    
    print("\nSearching for fact_ordenes_produccion:")
    cur.execute("""
        SELECT table_schema, table_name 
        FROM information_schema.tables 
        WHERE table_name = 'fact_ordenes_produccion'
    """)
    for r in cur.fetchall():
        print(f" - {r[0]}.{r[1]}")

    print("\nSearching for v_bonos_activos:")
    cur.execute("""
        SELECT table_schema, table_name 
        FROM information_schema.tables 
        WHERE table_name = 'v_bonos_activos'
    """)
    for r in cur.fetchall():
        print(f" - {r[0]}.{r[1]}")
        
    cur.close()
    conn.close()
except Exception as e:
    print(f"Error: {e}")
