import os
import httpx
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import text
from datetime import datetime, timedelta
from typing import Optional
from app.db import get_engine

router = APIRouter(prefix="/api")

JORNADA_INICIO = 7
JORNADA_FIN    = 16
_SEGMENTOS = [(7 * 60, 11 * 60), (11 * 60 + 15, 16 * 60)]


def _next_workday_start(dt: datetime) -> datetime:
    nxt = (dt + timedelta(days=1)).replace(hour=JORNADA_INICIO, minute=0, second=0, microsecond=0)
    while nxt.weekday() >= 5:
        nxt += timedelta(days=1)
    return nxt


def add_work_minutes(start: datetime, minutes: float) -> datetime:
    if minutes <= 0:
        return start
    remaining = float(minutes)
    day = start
    for _ in range(400):
        if day.weekday() >= 5:
            day = _next_workday_start(day); continue
        midnight = day.replace(hour=0, minute=0, second=0, microsecond=0)
        cm = (day - midnight).total_seconds() / 60
        for a, b in _SEGMENTOS:
            if cm >= b:
                continue
            seg_start = max(cm, a)
            avail = b - seg_start
            if remaining <= avail:
                return midnight + timedelta(minutes=seg_start + remaining)
            remaining -= avail
        day = _next_workday_start(day)
    return day


def _estimar_fin(inicio: datetime, min_est: float, min_real: float, ahora: datetime) -> datetime:
    min_rest = max(min_est - min_real, 0)
    base = max(inicio, ahora)
    if min_est > 0:
        fin = add_work_minutes(base, min_rest if min_rest > 0 else 10)
    else:
        fin = base.replace(hour=JORNADA_FIN, minute=0, second=0, microsecond=0)
        if fin <= base:
            fin = base + timedelta(hours=1)
    return max(fin, ahora + timedelta(minutes=10))


# ─────────────────────────────────────────────────────────────────────
#  GRUPOS  (filas del Gantt)
# ─────────────────────────────────────────────────────────────────────

@router.get("/grupos")
def get_grupos(vista: str = Query("empleado", pattern="^(maquina|empleado)$")):
    engine = get_engine()
    with engine.connect() as conn:
        if vista == 'maquina':
            rows = conn.execute(text("""
                SELECT
                    dm.matricula::text                    AS id,
                    dm.descrip                            AS nombre,
                    COALESCE(dm.tipo, 'Sin tipo')         AS sub,
                    COALESCE(dm.area, 'Sin área')         AS area
                FROM core.dim_maquinas dm
                ORDER BY dm.area, dm.descrip
            """)).mappings().all()
        else:
            rows = conn.execute(text("""
                SELECT
                    de.idempleado::text                    AS id,
                    de.nombre_completo                     AS nombre,
                    COALESCE(de.departamento, 'Sin depto') AS sub,
                    COALESCE(de.departamento, 'Sin depto') AS area
                FROM core.dim_empleados de
                WHERE (de.fechabaja IS NULL OR de.fechabaja > CURRENT_DATE)
                  AND (
                      LOWER(de.departamento) IN ('producción','produccion','logística','logistica')
                      OR de.idempleado IN (
                          SELECT DISTINCT idempleado FROM analytics.v_asignaciones_empleado
                          WHERE fase = 'EN_CURSO'
                      )
                  )
                ORDER BY de.nombre_completo
            """)).mappings().all()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────
#  ITEMS  (barras del Gantt)
# ─────────────────────────────────────────────────────────────────────

@router.get("/items")
def get_items(
    vista: str = Query("empleado", pattern="^(maquina|empleado)$"),
    desde: Optional[datetime] = None,
    hasta: Optional[datetime] = None,
):
    if desde is None:
        desde = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=7)
    if hasta is None:
        hasta = datetime.now() + timedelta(hours=1)
    desde, hasta = desde.replace(tzinfo=None), hasta.replace(tzinfo=None)
    ahora = datetime.now()
    result = []

    engine = get_engine()
    with engine.connect() as conn:

        if vista == 'maquina':
            # ── Máquinas en curso ──────────────────────────────────────
            activos = conn.execute(text("""
                WITH hist_art_op AS (
                    SELECT idarticulo::text, operacion,
                           AVG(min_reales / NULLIF(cantidad_objetivo, 0)) AS mpp
                    FROM core.fact_bonos
                    WHERE estado_orden = 2
                      AND cantidad_objetivo > 0 AND min_reales > 0
                    GROUP BY idarticulo, operacion
                ),
                hist_op AS (
                    SELECT operacion,
                           AVG(min_reales / NULLIF(cantidad_objetivo, 0)) AS mpp
                    FROM core.fact_bonos
                    WHERE estado_orden = 2
                      AND cantidad_objetivo > 0 AND min_reales > 0
                    GROUP BY operacion
                )
                SELECT
                    m.matricula, m.maquina, m.idorden, m.idbono, m.operacion, m.articulo,
                    m.situacion, m.cantidad_pedida, m.fecha_prevista_fin,
                    m.minutos_reales, m.fichaje_activo_desde,
                    ROUND(COALESCE(hao.mpp, ho.mpp) * NULLIF(m.cantidad_objetivo, 0)) AS min_estimados
                FROM core.fact_asignaciones_maquina m
                LEFT JOIN hist_art_op hao ON hao.idarticulo = m.idarticulo AND hao.operacion = m.operacion
                LEFT JOIN hist_op     ho  ON ho.operacion = m.operacion
                WHERE m.situacion = 'EN_CURSO'
            """)).mappings().all()

            for r in activos:
                inicio   = r["fichaje_activo_desde"] or ahora
                min_est  = float(r["min_estimados"] or 0)
                min_real = float(r["minutos_reales"] or 0)
                fin      = _estimar_fin(inicio, min_est, min_real, ahora)
                result.append({
                    "id":           f"maq_real_{r['matricula']}_{r['idorden']}_{r['idbono']}",
                    "idorden":      str(r["idorden"]),
                    "idbono":       r["idbono"],
                    "recurso_id":   str(r["matricula"]),
                    "tipo":         "real",
                    "en_curso":     True,
                    "estado":       "plazo",
                    "estado_label": "En curso",
                    "situacion":    "EN_CURSO",
                    "art":          r["articulo"],
                    "operacion":    r["operacion"],
                    "cantidad":     r["cantidad_pedida"],
                    "prev":         r["fecha_prevista_fin"].isoformat() if r["fecha_prevista_fin"] else None,
                    "start":        inicio.isoformat(),
                    "end":          fin.isoformat(),
                    "estimado":     True,
                    "progreso":     round(min(min_real / min_est * 100, 100)) if min_est > 0 else None,
                    "operarios":    None,
                    "notas":        None,
                })

            # ── Máquinas completadas (dentro de la ventana) ────────────
            completados = conn.execute(text("""
                SELECT
                    matricula, maquina, idorden, idbono, operacion, articulo,
                    cantidad_pedida, fecha_prevista_fin, minutos_reales,
                    piezas_producidas, fecha_asignacion
                FROM core.fact_asignaciones_maquina
                WHERE situacion = 'COMPLETADO'
                  AND fecha_asignacion IS NOT NULL
                  AND fecha_asignacion < :hasta
                  AND fecha_asignacion > :desde_aprox
            """), {"hasta": hasta, "desde_aprox": desde - timedelta(days=1)}).mappings().all()

            for r in completados:
                inicio   = r["fecha_asignacion"]
                min_real = float(r["minutos_reales"] or 0)
                fin      = add_work_minutes(inicio, min_real) if min_real > 0 else inicio + timedelta(minutes=15)
                if fin <= desde:
                    continue
                result.append({
                    "id":           f"maq_trab_{r['matricula']}_{r['idorden']}_{r['idbono']}",
                    "idorden":      str(r["idorden"]),
                    "idbono":       r["idbono"],
                    "recurso_id":   str(r["matricula"]),
                    "tipo":         "trabajado",
                    "estado":       "completado",
                    "estado_label": "Completado",
                    "situacion":    "COMPLETADO",
                    "art":          r["articulo"],
                    "operacion":    r["operacion"],
                    "cantidad":     r["cantidad_pedida"],
                    "prev":         r["fecha_prevista_fin"].isoformat() if r["fecha_prevista_fin"] else None,
                    "start":        inicio.isoformat(),
                    "end":          fin.isoformat(),
                    "estimado":     False,
                    "progreso":     None,
                    "operarios":    None,
                    "notas":        None,
                    "min_real":     float(r["minutos_reales"]) if r["minutos_reales"] is not None else None,
                    "piezas":       float(r["piezas_producidas"]) if r["piezas_producidas"] is not None else None,
                })

            return result

        # ── Empleados: bonos en curso ──────────────────────────────────
        activos = conn.execute(text("""
            SELECT
                idempleado, idorden, idbono, operacion, articulo,
                situacion, cantidad_pedida, fecha_prevista_fin,
                min_estimados, minutos_reales,
                COALESCE(fichaje_activo_desde, fecha_inicio_real) AS inicio
            FROM analytics.v_asignaciones_empleado
            WHERE fase = 'EN_CURSO'
        """)).mappings().all()

        for r in activos:
            inicio   = r["inicio"] or ahora
            min_est  = float(r["min_estimados"] or 0)
            min_real = float(r["minutos_reales"] or 0)
            fin      = _estimar_fin(inicio, min_est, min_real, ahora)
            result.append({
                "id":           f"real_{r['idorden']}_{r['idbono']}",
                "idorden":      str(r["idorden"]),
                "idbono":       r["idbono"],
                "recurso_id":   str(r["idempleado"]),
                "tipo":         "real",
                "en_curso":     True,
                "estado":       "plazo",
                "estado_label": "En curso",
                "situacion":    str(r["situacion"]),
                "art":          r["articulo"],
                "operacion":    r["operacion"],
                "cantidad":     r["cantidad_pedida"],
                "prev":         r["fecha_prevista_fin"].isoformat() if r["fecha_prevista_fin"] else None,
                "start":        inicio.isoformat(),
                "end":          fin.isoformat(),
                "estimado":     True,
                "progreso":     round(min(min_real / min_est * 100, 100)) if min_est > 0 else None,
                "operarios":    None,
                "notas":        None,
            })

        # ── Empleados: bonos trabajados (completados en la ventana) ────
        trabajados = conn.execute(text("""
            SELECT
                idempleado, idorden, idbono, operacion, articulo,
                cantidad_pedida, fecha_prevista_fin, minutos_reales,
                piezas_producidas, fecha_inicio_real, fecha_fin_real
            FROM analytics.v_asignaciones_empleado
            WHERE fase = 'TRABAJADO'
              AND fecha_inicio_real IS NOT NULL AND fecha_fin_real IS NOT NULL
              AND fecha_inicio_real < :hasta AND fecha_fin_real > :desde
        """), {"desde": desde, "hasta": hasta}).mappings().all()

        for r in trabajados:
            result.append({
                "id":           f"trab_{r['idempleado']}_{r['idorden']}_{r['idbono']}",
                "idorden":      str(r["idorden"]),
                "idbono":       r["idbono"],
                "recurso_id":   str(r["idempleado"]),
                "tipo":         "trabajado",
                "estado":       "completado",
                "estado_label": "Completado",
                "situacion":    "COMPLETADO",
                "art":          r["articulo"],
                "operacion":    r["operacion"],
                "cantidad":     r["cantidad_pedida"],
                "prev":         r["fecha_prevista_fin"].isoformat() if r["fecha_prevista_fin"] else None,
                "start":        r["fecha_inicio_real"].isoformat(),
                "end":          r["fecha_fin_real"].isoformat(),
                "estimado":     False,
                "progreso":     None,
                "operarios":    None,
                "notas":        None,
                "min_real":     float(r["minutos_reales"]) if r["minutos_reales"] is not None else None,
                "piezas":       float(r["piezas_producidas"]) if r["piezas_producidas"] is not None else None,
            })

    return result


# ─────────────────────────────────────────────────────────────────────
#  REFRESCO ETL  (dispara el flujo Prefect bajo demanda)
# ─────────────────────────────────────────────────────────────────────

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

@router.get("/refrescar/{flow_run_id}")
def refrescar_estado(flow_run_id: str):
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
