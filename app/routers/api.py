import os
import httpx
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import text
from datetime import datetime, timedelta
from typing import Optional
from app.db import get_engine

router = APIRouter(prefix="/api")

# ── Jornada laboral (para estimar fin mientras no haya fecha_fin_real) ──
#  Horario: 7:00–16:00 con descanso 11:00–11:15  →  525 min efectivos/día.
JORNADA_INICIO = 7
JORNADA_FIN    = 16
# Segmentos de trabajo dentro del día, en minutos desde medianoche.
_SEGMENTOS = [(7 * 60, 11 * 60), (11 * 60 + 15, 16 * 60)]   # [7:00–11:00] y [11:15–16:00]


def _next_workday_start(dt: datetime) -> datetime:
    nxt = (dt + timedelta(days=1)).replace(hour=JORNADA_INICIO, minute=0, second=0, microsecond=0)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def add_work_minutes(start: datetime, minutes: float) -> datetime:
    """Suma `minutes` de trabajo respetando jornada 7–16h, descanso 11:00–11:15 y días laborables."""
    if minutes <= 0:
        return start
    remaining = float(minutes)
    day = start
    for _ in range(400):
        if day.weekday() >= 5:
            day = _next_workday_start(day); continue
        midnight = day.replace(hour=0, minute=0, second=0, microsecond=0)
        cm = (day - midnight).total_seconds() / 60            # minutos desde medianoche
        for a, b in _SEGMENTOS:
            if cm >= b:
                continue
            seg_start = max(cm, a)
            avail = b - seg_start
            if remaining <= avail:
                return midnight + timedelta(minutes=seg_start + remaining)
            remaining -= avail
        day = _next_workday_start(day)                        # día agotado
    return day


def _snap_to_work(dt: datetime) -> datetime:
    """Devuelve `dt` si cae dentro de un tramo de jornada; si no, el inicio del siguiente tramo."""
    for _ in range(400):
        if dt.weekday() >= 5:
            dt = _next_workday_start(dt); continue
        midnight = dt.replace(hour=0, minute=0, second=0, microsecond=0)
        cm = (dt - midnight).total_seconds() / 60
        for a, b in _SEGMENTOS:
            if cm < a:
                return midnight + timedelta(minutes=a)
            if cm < b:
                return dt
        dt = _next_workday_start(dt)                          # día agotado
    return dt


def _estimar_fin(inicio: datetime, min_est: float, min_real: float, ahora: datetime) -> datetime:
    """Proyección de fin para un bono en curso sin fecha_fin_real."""
    min_rest = max(min_est - min_real, 0)
    base = max(inicio, ahora)
    if min_est > 0:
        fin = add_work_minutes(base, min_rest if min_rest > 0 else 30)
    else:
        fin = base.replace(hour=JORNADA_FIN, minute=0, second=0, microsecond=0)
        if fin <= base:
            fin = base + timedelta(hours=1)
    return max(fin, ahora + timedelta(minutes=30))


# Resolución del recurso (máquina u operario) que trabajó un bono: último fichaje.
def _recurso_lateral(vista: str) -> str:
    col = "matricula_maquina" if vista == "maquina" else "idempleado"
    return f"""
        LEFT JOIN LATERAL (
            SELECT {col} AS rid
            FROM core.fact_fichajes
            WHERE idorden = eba.idorden AND idbono = eba.idbono AND {col} IS NOT NULL
            ORDER BY hinicial DESC
            LIMIT 1
        ) te ON true
    """


_SEM_KEY  = {"RETRASADA": "retrasada", "EN RIESGO": "riesgo", "EN PLAZO": "plazo", "SIN ESTIMAR": "sin-estimar"}
_PRIO_KEY = {"VENCIDA": "vencida", "URGENTE": "urgente", "NORMAL": "normal", "SIN FECHA": "sin-fecha"}


# ─────────────────────────────────────────────────────────────────────
#  GRUPOS  (filas del Gantt según la vista activa)
# ─────────────────────────────────────────────────────────────────────

@router.get("/grupos")
def get_grupos(vista: str = Query("empleado", pattern="^(maquina|empleado)$")):
    engine = get_engine()
    with engine.connect() as conn:
        if vista == "maquina":
            rows = conn.execute(text(f"""
                WITH bono_rec AS (
                    SELECT DISTINCT te.rid
                    FROM analytics.v_estado_bonos_activos eba
                    {_recurso_lateral('maquina')}
                    WHERE te.rid IS NOT NULL
                )
                SELECT DISTINCT ON (dm.matricula)
                    dm.matricula                  AS id,
                    dm.descrip                    AS nombre,
                    COALESCE(dm.area, 'Sin área') AS sub,
                    COALESCE(dm.area, 'Sin área') AS area
                FROM core.dim_maquinas dm
                WHERE dm.matricula IN (SELECT rid FROM bono_rec)
                   OR dm.matricula IN (SELECT recurso_id FROM planning.programacion WHERE recurso_tipo = 'maquina')
                ORDER BY dm.matricula, dm.area, dm.descrip
            """)).mappings().all()
        else:
            rows = conn.execute(text(f"""
                WITH bono_rec AS (
                    SELECT DISTINCT te.rid
                    FROM analytics.v_estado_bonos_activos eba
                    {_recurso_lateral('empleado')}
                    WHERE te.rid IS NOT NULL
                )
                SELECT
                    de.idempleado::text                    AS id,
                    de.nombre_completo                     AS nombre,
                    COALESCE(de.departamento, 'Sin depto') AS sub,
                    COALESCE(de.departamento, 'Sin depto') AS area
                FROM core.dim_empleados de
                WHERE (de.fechabaja IS NULL OR de.fechabaja > CURRENT_DATE)
                  AND ( LOWER(de.departamento) IN ('producción','produccion','logística','logistica')
                        OR de.nombre_completo = 'GILBERTO GOMEZ FERNANDEZ'
                        OR de.idempleado IN (SELECT rid::int FROM bono_rec)
                        OR de.idempleado::text IN (SELECT recurso_id FROM planning.programacion WHERE recurso_tipo = 'empleado') )
                ORDER BY de.nombre_completo
            """)).mappings().all()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────
#  ITEMS  (barras del Gantt — unidad = BONO)
# ─────────────────────────────────────────────────────────────────────

@router.get("/items")
def get_items(
    vista: str = Query("empleado", pattern="^(maquina|empleado)$"),
    desde: Optional[datetime] = None,
    hasta: Optional[datetime] = None,
):
    if desde is None:
        desde = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    if hasta is None:
        hasta = desde + timedelta(days=7)
    desde, hasta = desde.replace(tzinfo=None), hasta.replace(tzinfo=None)
    ahora = datetime.now()
    result = []

    engine = get_engine()
    with engine.connect() as conn:

        # ── Bonos planificados manualmente ───────────────────────────
        planned = conn.execute(text("""
            SELECT
                p.id, p.idorden, p.idbono, p.recurso_id, p.start_planned, p.end_planned, p.notas,
                fb.operacion,
                COALESCE(da.descrip, o.idarticulo) AS articulo,
                o.cantidad_pedida, o.fecha_prevista_fin,
                CASE
                    WHEN o.fecha_prevista_fin IS NULL             THEN 'SIN FECHA'
                    WHEN o.fecha_prevista_fin < CURRENT_DATE      THEN 'VENCIDA'
                    WHEN o.fecha_prevista_fin <= CURRENT_DATE + 7 THEN 'URGENTE'
                    ELSE 'NORMAL'
                END AS prioridad
            FROM planning.programacion p
            JOIN core.fact_ordenes o ON p.idorden = o.idorden::text
            LEFT JOIN core.fact_bonos fb ON fb.idorden = o.idorden AND fb.idbono = p.idbono
            LEFT JOIN core.dim_articulo da ON o.idarticulo = da.idarticulo
            WHERE p.start_planned < :hasta AND p.end_planned > :desde
              AND p.recurso_tipo = :vista
            ORDER BY p.start_planned
        """), {"desde": desde, "hasta": hasta, "vista": vista}).mappings().all()

        for r in planned:
            result.append({
                "id":         r["id"],
                "idorden":    str(r["idorden"]),
                "idbono":     r["idbono"],
                "recurso_id": str(r["recurso_id"]),
                "tipo":       "planificado",
                "estado":     _PRIO_KEY.get(r["prioridad"], "sin-fecha"),
                "estado_label": r["prioridad"],
                "situacion":  "PLANIFICADO",
                "art":        r["articulo"],
                "operacion":  r["operacion"],
                "cantidad":   r["cantidad_pedida"],
                "prev":       r["fecha_prevista_fin"].isoformat() if r["fecha_prevista_fin"] else None,
                "start":      r["start_planned"].isoformat(),
                "end":        r["end_planned"].isoformat(),
                "estimado":   False,
                "progreso":   None,
                "operarios":  None,
                "notas":      r["notas"],
            })

        # ── Bonos activos (en curso · parados · pausados) ────────────
        active = conn.execute(text(f"""
            SELECT
                eba.idorden, eba.idbono, eba.operacion, eba.situacion, eba.semaforo,
                eba.en_curso, eba.min_estimados, eba.min_reales,
                eba.fecha_inicio_real, eba.fecha_fin_real, eba.num_operarios,
                COALESCE(da.descrip, o.idarticulo) AS articulo,
                o.cantidad_pedida, o.fecha_prevista_fin,
                te.rid::text AS recurso_id
            FROM analytics.v_estado_bonos_activos eba
            JOIN core.fact_ordenes o ON eba.idorden = o.idorden
            LEFT JOIN core.dim_articulo da ON o.idarticulo = da.idarticulo
            {_recurso_lateral(vista)}
            WHERE eba.fecha_inicio_real IS NOT NULL AND te.rid IS NOT NULL
        """)).mappings().all()

        for r in active:
            inicio   = r["fecha_inicio_real"]
            min_est  = float(r["min_estimados"] or 0)
            min_real = float(r["min_reales"] or 0)
            situ     = r["situacion"]

            if r["en_curso"]:
                # En curso: el bono sigue abierto (hay un fichaje sin cerrar), así que
                # fecha_fin_real (fin de un fichaje anterior) NO es el fin del bono.
                # Siempre proyectamos el fin hasta, al menos, ahora.
                fin, estimado = _estimar_fin(inicio, min_est, min_real, ahora), True
                estado = _SEM_KEY.get(r["semaforo"] or "SIN ESTIMAR", "sin-estimar")
                estado_label = r["semaforo"] or "SIN ESTIMAR"
            else:
                # Parado / pausado: tramo real ya cerrado
                fin = r["fecha_fin_real"] or _estimar_fin(inicio, min_est, min_real, ahora)
                estimado = r["fecha_fin_real"] is None
                estado = "parada" if situ == "PARADA" else "pausada"
                estado_label = situ

            # Filtro por ventana visible
            if fin <= desde or inicio >= hasta:
                continue

            progreso = round(min(min_real / min_est * 100, 100)) if min_est > 0 else None

            result.append({
                "id":         f"real_{r['idorden']}_{r['idbono']}",
                "idorden":    str(r["idorden"]),
                "idbono":     r["idbono"],
                "recurso_id": str(r["recurso_id"]),
                "tipo":       "real",
                "estado":     estado,
                "estado_label": estado_label,
                "situacion":  situ,
                "art":        r["articulo"],
                "operacion":  r["operacion"],
                "cantidad":   r["cantidad_pedida"],
                "prev":       r["fecha_prevista_fin"].isoformat() if r["fecha_prevista_fin"] else None,
                "start":      inicio.isoformat(),
                "end":        fin.isoformat(),
                "estimado":   estimado,
                "progreso":   progreso,
                "operarios":  r["num_operarios"],
                "notas":      None,
            })

        # ── Vista de operarios: trabajado (pasado) + programado (futuro) ──
        #  Sale de analytics.v_asignaciones_empleado (1 fila por empleado·orden·bono).
        #  El "en curso" ya lo cubre el bloque de bonos activos de arriba.
        if vista == "empleado":

            # C) TRABAJADO: bonos completados, con inicio/fin reales del bono.
            trabajado = conn.execute(text("""
                SELECT idempleado, idorden, idbono, operacion, articulo,
                       cantidad_pedida, fecha_prevista_fin, minutos_reales,
                       piezas_producidas, fecha_inicio_real, fecha_fin_real
                FROM analytics.v_asignaciones_empleado
                WHERE fase = 'TRABAJADO'
                  AND fecha_inicio_real IS NOT NULL AND fecha_fin_real IS NOT NULL
                  AND fecha_inicio_real < :hasta AND fecha_fin_real > :desde
            """), {"desde": desde, "hasta": hasta}).mappings().all()

            for r in trabajado:
                result.append({
                    "id":         f"trab_{r['idempleado']}_{r['idorden']}_{r['idbono']}",
                    "idorden":    str(r["idorden"]),
                    "idbono":     r["idbono"],
                    "recurso_id": str(r["idempleado"]),
                    "tipo":       "trabajado",
                    "estado":     "completado",
                    "estado_label": "Completado",
                    "situacion":  "COMPLETADO",
                    "art":        r["articulo"],
                    "operacion":  r["operacion"],
                    "cantidad":   r["cantidad_pedida"],
                    "prev":       r["fecha_prevista_fin"].isoformat() if r["fecha_prevista_fin"] else None,
                    "start":      r["fecha_inicio_real"].isoformat(),
                    "end":        r["fecha_fin_real"].isoformat(),
                    "estimado":   False,
                    "progreso":   None,
                    "operarios":  None,
                    "notas":      None,
                    "min_real":   float(r["minutos_reales"]) if r["minutos_reales"] is not None else None,
                    "piezas":     float(r["piezas_producidas"]) if r["piezas_producidas"] is not None else None,
                })

            # D) PROGRAMADO: bonos pendientes sin horas reales → cola futura por
            #    empleado, arrancando en "ahora" y encadenando cada bono con su
            #    duración estimada (tope visual para no desbordar el Gantt).
            PROG_FALLBACK_MIN = 90       # sin histórico de su operación
            PROG_CAP_MIN      = 1050     # tope visual: 2 jornadas efectivas
            prog = conn.execute(text("""
                SELECT idempleado, idorden, idbono, operacion, articulo,
                       cantidad_pedida, fecha_prevista_fin, min_estimados
                FROM analytics.v_asignaciones_empleado
                WHERE fase = 'PROGRAMADO' AND estado_orden IN (0, 1)
                ORDER BY idempleado, fecha_prevista_fin ASC NULLS LAST, idorden, idbono
            """)).mappings().all()

            queue_start = _snap_to_work(ahora.replace(second=0, microsecond=0))
            emp_actual, cursor = None, queue_start
            for r in prog:
                if r["idempleado"] != emp_actual:
                    emp_actual, cursor = r["idempleado"], queue_start
                # Duración estimada → ancho de la barra. Tope visual; la estimación
                # real se conserva en `min_est` (tooltip).
                est_real = float(r["min_estimados"] or 0) or PROG_FALLBACK_MIN
                dur      = min(est_real, PROG_CAP_MIN)
                start = _snap_to_work(cursor)
                fin   = add_work_minutes(start, dur)
                cursor = fin
                if fin <= desde or start >= hasta:
                    continue
                result.append({
                    "id":         f"prog_{r['idempleado']}_{r['idorden']}_{r['idbono']}",
                    "idorden":    str(r["idorden"]),
                    "idbono":     r["idbono"],
                    "recurso_id": str(r["idempleado"]),
                    "tipo":       "programado",
                    "estado":     "programado",
                    "estado_label": "Programado",
                    "situacion":  "PROGRAMADO",
                    "art":        r["articulo"],
                    "operacion":  r["operacion"],
                    "cantidad":   r["cantidad_pedida"],
                    "prev":       r["fecha_prevista_fin"].isoformat() if r["fecha_prevista_fin"] else None,
                    "start":      start.isoformat(),
                    "end":        fin.isoformat(),
                    "estimado":   True,
                    "progreso":   None,
                    "operarios":  None,
                    "notas":      None,
                    "min_est":    round(est_real),
                })

    return result


# ─────────────────────────────────────────────────────────────────────
#  BACKLOG  (dos niveles: bonos a replanificar + órdenes en espera)
# ─────────────────────────────────────────────────────────────────────

@router.get("/backlog")
def get_backlog():
    engine = get_engine()
    out = []
    with engine.connect() as conn:

        # A) Bonos a replanificar: activos parados o pausados, no planificados aún
        bonos = conn.execute(text("""
            SELECT
                eba.idorden::text AS idorden, eba.idbono, eba.operacion, eba.situacion, eba.semaforo,
                eba.min_estimados, eba.min_reales, eba.num_operarios,
                eba.fecha_inicio_real, eba.fecha_fin_real,
                COALESCE(da.descrip, o.idarticulo) AS articulo,
                o.cantidad_pedida, o.fecha_prevista_fin
            FROM analytics.v_estado_bonos_activos eba
            JOIN core.fact_ordenes o ON eba.idorden = o.idorden
            LEFT JOIN core.dim_articulo da ON o.idarticulo = da.idarticulo
            WHERE eba.situacion IN ('PARADA', 'PAUSADA')
              AND NOT EXISTS (
                    SELECT 1 FROM planning.programacion p
                    WHERE p.idorden = eba.idorden::text AND p.idbono = eba.idbono
              )
            ORDER BY
                CASE eba.situacion WHEN 'PARADA' THEN 0 ELSE 1 END,
                o.fecha_prevista_fin ASC NULLS LAST, eba.idorden, eba.idbono
        """)).mappings().all()

        for r in bonos:
            horas = round(float(r["min_estimados"]) / 60.0, 1) if r["min_estimados"] else None
            out.append({
                "nivel":        "bono",
                "idorden":      r["idorden"],
                "idbono":       r["idbono"],
                "operacion":    r["operacion"],
                "situacion":    r["situacion"],
                "semaforo":     r["semaforo"],
                "articulo":     r["articulo"],
                "cantidad_pedida": r["cantidad_pedida"],
                "horas_estimadas": horas,
                "num_operarios":   r["num_operarios"],
                "fecha_prevista_fin": r["fecha_prevista_fin"].isoformat() if r["fecha_prevista_fin"] else None,
                "prioridad":    None,
            })

        # B) Órdenes en espera (idestado=0): sin bonos abiertos, nivel orden
        ordenes = conn.execute(text("""
            SELECT
                b.idorden::text AS idorden, b.articulo, b.cantidad_pedida,
                b.fecha_prevista_fin, b.dias_retraso, b.horas_estimadas, b.prioridad
            FROM analytics.v_backlog_ordenes b
            WHERE NOT EXISTS (
                SELECT 1 FROM planning.programacion p
                WHERE p.idorden = b.idorden::text AND p.idbono = 0
            )
            ORDER BY
                CASE b.prioridad WHEN 'VENCIDA' THEN 0 WHEN 'URGENTE' THEN 1 WHEN 'NORMAL' THEN 2 ELSE 3 END,
                b.fecha_prevista_fin ASC NULLS LAST
        """)).mappings().all()

        for r in ordenes:
            out.append({
                "nivel":        "orden",
                "idorden":      r["idorden"],
                "idbono":       0,
                "operacion":    None,
                "situacion":    None,
                "semaforo":     None,
                "articulo":     r["articulo"],
                "cantidad_pedida": r["cantidad_pedida"],
                "horas_estimadas": float(r["horas_estimadas"]) if r["horas_estimadas"] else None,
                "num_operarios":   None,
                "fecha_prevista_fin": r["fecha_prevista_fin"].isoformat() if r["fecha_prevista_fin"] else None,
                "prioridad":    r["prioridad"],
            })

    return out


# ─────────────────────────────────────────────────────────────────────
#  RECURSOS  (selector del modal de asignación)
# ─────────────────────────────────────────────────────────────────────

@router.get("/recursos")
def get_recursos(tipo: str = Query("empleado", pattern="^(maquina|empleado)$")):
    engine = get_engine()
    with engine.connect() as conn:
        if tipo == "maquina":
            rows = conn.execute(text("""
                SELECT matricula AS id, descrip AS nombre, COALESCE(area, '') AS grupo
                FROM core.dim_maquinas ORDER BY area, descrip
            """)).mappings().all()
        else:
            rows = conn.execute(text("""
                SELECT idempleado::text AS id, nombre_completo AS nombre, COALESCE(departamento, '') AS grupo
                FROM core.dim_empleados
                WHERE fechabaja IS NULL OR fechabaja > CURRENT_DATE
                ORDER BY nombre_completo
            """)).mappings().all()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────
#  CRUD de planificación  (por bono)
# ─────────────────────────────────────────────────────────────────────

class ProgramarRequest(BaseModel):
    idorden:      str
    idbono:       int = 0
    recurso_tipo: str
    recurso_id:   str
    start:        datetime
    end:          datetime
    notas:        Optional[str] = None


class MoverRequest(BaseModel):
    start:      datetime
    end:        datetime
    recurso_id: str


@router.post("/programar", status_code=201)
def programar(req: ProgramarRequest):
    if req.end <= req.start:
        raise HTTPException(400, "end debe ser posterior a start")
    engine = get_engine()
    with engine.connect() as conn:
        existe = conn.execute(text(
            "SELECT id FROM planning.programacion WHERE idorden = :id AND idbono = :bono"
        ), {"id": req.idorden, "bono": req.idbono}).fetchone()
        if existe:
            destino = f"bono {req.idbono} de la orden {req.idorden}" if req.idbono else f"la orden {req.idorden}"
            raise HTTPException(409, f"Ya está planificado {destino}")
        row = conn.execute(text("""
            INSERT INTO planning.programacion (idorden, idbono, recurso_tipo, recurso_id, start_planned, end_planned, notas)
            VALUES (:idorden, :idbono, :tipo, :recurso, :start, :end, :notas)
            RETURNING id
        """), {"idorden": req.idorden, "idbono": req.idbono, "tipo": req.recurso_tipo,
               "recurso": req.recurso_id, "start": req.start, "end": req.end, "notas": req.notas}).fetchone()
        conn.commit()
    return {"id": row[0], "idorden": req.idorden, "idbono": req.idbono}


@router.put("/programar/{item_id}")
def mover(item_id: int, req: MoverRequest):
    if req.end <= req.start:
        raise HTTPException(400, "end debe ser posterior a start")
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text("""
            UPDATE planning.programacion
            SET start_planned = :start, end_planned = :end, recurso_id = :recurso, actualizado_en = NOW()
            WHERE id = :id
        """), {"start": req.start, "end": req.end, "recurso": req.recurso_id, "id": item_id})
        conn.commit()
        if result.rowcount == 0:
            raise HTTPException(404, "Programación no encontrada")
    return {"ok": True}


@router.delete("/programar/{item_id}")
def desprogramar(item_id: int):
    engine = get_engine()
    with engine.connect() as conn:
        result = conn.execute(text("DELETE FROM planning.programacion WHERE id = :id"), {"id": item_id})
        conn.commit()
        if result.rowcount == 0:
            raise HTTPException(404, "Programación no encontrada")
    return {"ok": True}


# ─────────────────────────────────────────────────────────────────────
#  REFRESCO BAJO DEMANDA  (dispara el flujo ETL en Prefect self-hosted)
# ─────────────────────────────────────────────────────────────────────
#  Config por entorno (.env):
#    PREFECT_API_URL        p.ej. http://10.0.0.12:4200/api
#    PREFECT_DEPLOYMENT_ID   id del deployment del flujo "prefact"
#    PREFECT_API_KEY         opcional (self-hosted normalmente sin auth)

def _prefect_cfg():
    return (
        (os.getenv("PREFECT_API_URL") or "").rstrip("/"),
        os.getenv("PREFECT_DEPLOYMENT_ID") or "",
        os.getenv("PREFECT_API_KEY") or "",
    )


def _prefect_headers(api_key: str) -> dict:
    h = {"Content-Type": "application/json"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


@router.post("/refrescar")
def refrescar():
    """Crea un flow run del deployment del ETL para traer datos del ERP ahora."""
    api, dep, key = _prefect_cfg()
    if not api or not dep:
        raise HTTPException(503, "Refresco no configurado: define PREFECT_API_URL y PREFECT_DEPLOYMENT_ID en .env")
    url = f"{api}/deployments/{dep}/create_flow_run"
    try:
        r = httpx.post(url, json={}, headers=_prefect_headers(key), timeout=20)
        r.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise HTTPException(502, f"Prefect respondió {e.response.status_code}: {e.response.text[:200]}")
    except httpx.HTTPError as e:
        raise HTTPException(502, f"No se pudo contactar con Prefect: {e}")
    data = r.json()
    return {"flow_run_id": data.get("id"), "estado": (data.get("state") or {}).get("type")}


# ─────────────────────────────────────────────────────────────────────
#  HISTÓRICO DE PRODUCCIÓN POR EMPLEADO
# ─────────────────────────────────────────────────────────────────────

@router.get("/historico/bonos")
def get_historico_bonos(
    idempleado: int = Query(...),
    desde: datetime = Query(...),
    hasta: datetime = Query(...),
):
    desde_dt = desde.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    hasta_excl = (hasta + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT
                f.idorden::text                                  AS idorden,
                f.idbono,
                fb.operacion,
                f.matricula_maquina,
                MIN(f.hinicial)::date                            AS primera_fecha,
                o.idarticulo,
                COALESCE(da.descrip, o.idarticulo::text)        AS articulo,
                o.cantidad_pedida,
                o.fecha_prevista_fin,
                de.nombre_completo,
                ROUND(SUM(
                    CASE WHEN f.hfinal IS NOT NULL
                         THEN EXTRACT(EPOCH FROM (f.hfinal - f.hinicial)) / 60.0
                         ELSE 0 END
                )::numeric, 1)                                   AS minutos_trabajados
            FROM core.fact_fichajes f
            JOIN core.fact_ordenes o
                ON f.idorden::text = o.idorden::text
            LEFT JOIN core.fact_bonos fb
                ON f.idorden::text = fb.idorden::text AND f.idbono = fb.idbono
            JOIN core.dim_empleados de
                ON f.idempleado = de.idempleado
            LEFT JOIN core.dim_articulo da
                ON o.idarticulo = da.idarticulo
            WHERE f.idempleado = :idempleado
              AND f.hinicial >= :desde
              AND f.hinicial <  :hasta_excl
            GROUP BY f.idorden, f.idbono, fb.operacion, f.matricula_maquina,
                     o.idarticulo, da.descrip, o.cantidad_pedida, o.fecha_prevista_fin,
                     de.nombre_completo
            ORDER BY MIN(f.hinicial) DESC
        """), {"idempleado": idempleado, "desde": desde_dt, "hasta_excl": hasta_excl}).mappings().all()
    return [dict(r) for r in rows]


@router.get("/historico/actividad-diaria")
def get_actividad_diaria(
    idempleado: int = Query(...),
    desde: datetime = Query(...),
    hasta: datetime = Query(...),
):
    desde_dt = desde.replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    hasta_excl = (hasta + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    engine = get_engine()
    with engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT
                f.hinicial::date                              AS fecha,
                COUNT(DISTINCT f.idorden::text || '-' || f.idbono::text) AS num_bonos,
                COUNT(DISTINCT f.idorden)                    AS num_ordenes,
                ROUND(SUM(
                    CASE WHEN f.hfinal IS NOT NULL
                         THEN EXTRACT(EPOCH FROM (f.hfinal - f.hinicial)) / 60.0
                         ELSE 0 END
                )::numeric, 1)                               AS minutos_trabajados
            FROM core.fact_fichajes f
            WHERE f.idempleado = :idempleado
              AND f.hinicial >= :desde
              AND f.hinicial <  :hasta_excl
            GROUP BY f.hinicial::date
            ORDER BY f.hinicial::date
        """), {"idempleado": idempleado, "desde": desde_dt, "hasta_excl": hasta_excl}).mappings().all()
    return [dict(r) for r in rows]


@router.get("/refrescar/{flow_run_id}")
def refrescar_estado(flow_run_id: str):
    """Consulta el estado de un flow run (para sondear hasta que termine)."""
    api, _, key = _prefect_cfg()
    if not api:
        raise HTTPException(503, "Refresco no configurado")
    try:
        r = httpx.get(f"{api}/flow_runs/{flow_run_id}", headers=_prefect_headers(key), timeout=20)
        r.raise_for_status()
    except httpx.HTTPError as e:
        raise HTTPException(502, f"No se pudo consultar el estado: {e}")
    st = r.json().get("state") or {}
    return {"estado": st.get("type"), "nombre": st.get("name")}
