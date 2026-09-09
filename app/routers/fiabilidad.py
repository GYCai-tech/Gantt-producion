"""¿Se puede uno fiar de las estimaciones del Gantt?

Esto no opina: mide. Coge los bonos que YA ESTÁN CERRADOS —de los que se sabe
lo que costaron de verdad—, calcula lo que la cadena de estimación habría
predicho para cada uno, y compara.

Dos decisiones que condicionan el resultado y conviene tener presentes:

  · **Se excluye el propio bono del histórico** (leave-one-out). Si no, la
    media del artículo incluye al bono que se está juzgando y se autoevalúa:
    con 4 bonos por artículo eso solo basta para inflar el acierto. Al quitarlo
    se mide lo que de verdad pasa con un bono nuevo, que es el caso real.

  · **Solo se compara la PRODUCCIÓN**, no la preparación. El montaje son
    líneas con `IdOperacion` 1/2, se estima por otra vía (media de la máquina)
    y mezclarlo contaminaría el ritmo, que es justo el error que se corrigió
    en `_SQL_MEDIAS`.

Las métricas:

  · **MdAPE** — error absoluto MEDIANO, en %. Mediano y no medio porque un
    puñado de bonos con fichajes rotos (alguien que no cerró la línea el
    viernes) desplaza una media y no una mediana.
  · **Dentro de ±25% / ±50%** — qué porcentaje de bonos cae en esa banda. Es
    lo que de verdad importa para planificar: no acertar al minuto, sino no
    equivocarse por un factor de dos.
  · **Sesgo** — real/estimado mediano. Por encima de 1 nos quedamos cortos
    (el trabajo dura más de lo que decimos), por debajo nos pasamos.
"""
from collections import defaultdict
from datetime import datetime
import statistics as st

from fastapi import APIRouter

from app.routers.api import _erp, _HIST_MESES, _MIN_BONOS_MEDIA, get_items

router = APIRouter()

#  Un análisis de 18 meses de bonos cerrados no cambia en una hora. La consulta
#  cuesta 234 ms sobre 10.707 filas, así que tampoco es para dejarla suelta en
#  cada F5 de la página.
_TTL_S = 3600
_cache = {"ts": None, "datos": None}

#  Mismo criterio que `_SQL_MEDIAS` para que se mida lo que se usa: líneas
#  cerradas, bonos terminados, producción separada del montaje. La diferencia
#  es que aquí NO se agrupa: hace falta un bono por fila para poder juzgarlos
#  de uno en uno.
_SQL = """
WITH bono_min AS (
    SELECT obl.IdOrden, obl.IdBono,
           SUM(CASE WHEN obl.IdOperacion = 0
                    THEN DATEDIFF(minute, obl.Hinicial, obl.Hfinal) ELSE 0 END) AS min_produccion,
           MIN(obl.Fecha) AS fecha
    FROM Ordenes_Bonos_Lineas obl
    WHERE obl.Hinicial IS NOT NULL
      AND obl.Hfinal   IS NOT NULL
      AND obl.Hfinal   > obl.Hinicial
      AND obl.Fecha   >= DATEADD(month, :meses, GETDATE())
    GROUP BY obl.IdOrden, obl.IdBono
),
mano_obra AS (
    SELECT IdTrabajo, SUM(Duracion) * 1440.0 AS MinPieza
    FROM Trabajos_ManoObra
    WHERE Duracion > 0
    GROUP BY IdTrabajo
)
SELECT
    bm.IdOrden        AS idorden,
    bm.IdBono         AS idbono,
    bm.fecha          AS fecha,
    bm.min_produccion AS real_min,
    obs.IdArticulo    AS articulo,
    ar.Descrip        AS descrip_articulo,
    ob.IdTrabajo      AS trabajo,
    ob.Matricula      AS maquina,
    obs.Cantidad      AS piezas,
    COALESCE(mo.MinPieza, NULLIF(tf.TiempoMO, 0) * 1440.0) AS teorico
FROM bono_min bm
    JOIN Ordenes_Bonos ob          ON ob.IdOrden  = bm.IdOrden AND ob.IdBono  = bm.IdBono
    JOIN Ordenes_Bonos_Salidas obs ON obs.IdOrden = bm.IdOrden AND obs.IdBono = bm.IdBono
    LEFT JOIN Articulos ar         ON ar.IdArticulo = obs.IdArticulo
    LEFT JOIN mano_obra mo         ON mo.IdTrabajo  = ob.IdTrabajo
    LEFT JOIN Trabajos_Fases tf    ON tf.IdTrabajo  = ob.IdTrabajo
WHERE ob.IdEstado = 2          -- terminado: es el único que tiene tiempo real
  AND obs.Cantidad     > 0
  AND bm.min_produccion > 0
"""

#  Los tramos del histograma, en real/estimado. El corte de 2x y 0,5x es el
#  que separa "una estimación imprecisa" de "una estimación equivocada".
_TRAMOS = [
    ("< 0,5x",     None, 0.50, "Nos pasamos mucho"),
    ("0,5 – 0,75", 0.50, 0.75, "Nos pasamos"),
    ("0,75 – 1",   0.75, 1.00, "Casi"),
    ("1 – 1,25",   1.00, 1.25, "Casi"),
    ("1,25 – 1,5", 1.25, 1.50, "Nos quedamos cortos"),
    ("1,5 – 2x",   1.50, 2.00, "Nos quedamos cortos"),
    ("> 2x",       2.00, None, "Nos quedamos muy cortos"),
]

_ORDEN_ORIGENES = ["teorico", "media_articulo", "media_trabajo", "media_maquina"]


def _leer_bonos() -> list[dict]:
    filas = []
    for r in _erp(_SQL, {"meses": -_HIST_MESES}):
        filas.append({
            "idorden": r["idorden"], "idbono": r["idbono"], "fecha": r["fecha"],
            "real_min": float(r["real_min"]),
            "articulo": r["articulo"],
            "descrip_articulo": (r["descrip_articulo"] or "").strip(),
            "trabajo": r["trabajo"],
            "maquina": (r["maquina"] or "").strip(),
            "piezas": float(r["piezas"]),
            "teorico": float(r["teorico"]) if r["teorico"] else None,
        })
    return filas


def _acumular(filas: list[dict]) -> dict:
    """Los mismos cubos que `_cargar_estimaciones`, pero con el tiempo REAL."""
    acc = {n: defaultdict(lambda: {"n": 0, "min": 0.0, "pz": 0.0})
           for n in ("articulo", "trabajo", "maquina")}
    for f in filas:
        for nivel in ("articulo", "trabajo", "maquina"):
            clave = f[nivel]
            if clave is None or clave == "":
                continue
            a = acc[nivel][clave]
            a["n"]   += 1
            a["min"] += f["real_min"]
            a["pz"]  += f["piezas"]
    return acc


def _estimar_sin_el_mismo(f: dict, acc: dict):
    """El ritmo que la app habría usado para ESTE bono, sin contar con él.

    Réplica de `_estimar` salvo por el descuento: se le restan al acumulador
    los minutos y las piezas del propio bono antes de dividir. Sin eso, un
    artículo con 4 bonos se juzga a sí mismo y el acierto sale inflado.
    """
    if f["teorico"]:
        return f["teorico"], "teorico"
    for origen, nivel in (("media_articulo", "articulo"),
                          ("media_trabajo",  "trabajo"),
                          ("media_maquina",  "maquina")):
        a = acc[nivel].get(f[nivel])
        if not a:
            continue
        n, minutos, piezas = a["n"] - 1, a["min"] - f["real_min"], a["pz"] - f["piezas"]
        if n >= _MIN_BONOS_MEDIA and piezas > 0 and minutos > 0:
            return minutos / piezas, origen
    return None, None


def _pct(parte: int, total: int) -> float:
    """Un decimal por debajo del 1%: el escandallo cubre 52 de 10.707 bonos y
    redondeado a entero sale "0%", que se lee como "ninguno"."""
    if not total:
        return 0.0
    p = 100 * parte / total
    return round(p, 1) if p < 1 else round(p)


def _resumen(ratios: list[float]) -> dict:
    errores = [abs(r - 1) for r in ratios]
    return {
        "bonos":      len(ratios),
        "mdape":      round(100 * st.median(errores)),
        "dentro_25":  round(100 * sum(1 for r in ratios if 0.75 <= r <= 1.25) / len(ratios)),
        "dentro_50":  round(100 * sum(1 for r in ratios if 0.50 <= r <= 2.00) / len(ratios)),
        "sesgo":      round(st.median(ratios), 2),
    }


def _histograma(ratios: list[float]) -> list[dict]:
    total = len(ratios)
    tramos = []
    for etiqueta, lo, hi, sentido in _TRAMOS:
        n = sum(1 for r in ratios
                if (lo is None or r >= lo) and (hi is None or r < hi))
        tramos.append({"tramo": etiqueta, "sentido": sentido, "bonos": n,
                       "pct": _pct(n, total)})
    return tramos


#  Fuera de esta banda la estimación deja de servir para planificar: no es
#  imprecisión, es equivocarse. Es el mismo ±25% de la tabla de arriba.
_ERROR_ACCIONABLE = 25


def _peores(juzgados: list[tuple], tope: int = 12) -> list[dict]:
    """Los artículos donde más horas se pierden por estimar mal.

    Ordenado por horas de error acumuladas y no por porcentaje: un artículo con
    un 300% de error en bonos de 4 minutos no le importa a nadie, y uno con un
    40% en bonos de 12 horas descoloca el taller entero. Esta lista es la
    respuesta a "¿por dónde empiezo a meter tiempos en el escandallo?".

    Pero se exige ADEMÁS que el error mediano pase del ±25%: si no, la lista se
    llena de artículos que están bien estimados y solo son grandes —el NIDO
    CUNA sale con 92 horas de error acumuladas y un 16% de desvío—, y eso no es
    un problema que arreglar, es el tamaño del artículo.
    """
    por_art = defaultdict(lambda: {"bonos": 0, "error_min": 0.0, "ratios": [],
                                   "descrip": "", "origen": ""})
    for ratio, real, est, f in juzgados:
        a = por_art[f["articulo"]]
        a["bonos"]     += 1
        a["error_min"] += abs(real - est)
        a["ratios"].append(ratio)
        a["descrip"] = f["descrip_articulo"]
    filas = [{
        "articulo": clave,
        "descrip": a["descrip"],
        "bonos": a["bonos"],
        "error_horas": round(a["error_min"] / 60),
        "sesgo": round(st.median(a["ratios"]), 2),
        "mdape": round(100 * st.median([abs(r - 1) for r in a["ratios"]])),
    } for clave, a in por_art.items() if a["bonos"] >= _MIN_BONOS_MEDIA]
    filas = [f for f in filas if f["mdape"] >= _ERROR_ACCIONABLE]
    filas.sort(key=lambda x: -x["error_horas"])
    return filas[:tope]


def _cobertura_hoy() -> list[dict]:
    """De lo que se está pintando AHORA en el Gantt, ¿sobre qué se apoya?

    Sin esto la página es historia: dice cómo de fiable ha sido la cadena, pero
    no cuánto de lo que hay en pantalla depende del escalón malo.
    """
    cuenta = defaultdict(int)
    for i in get_items(vista="empleado"):
        if i["tipo"] in ("programado", "real"):
            cuenta[i.get("origen_estimado") or "sin_estimacion"] += 1
    total = sum(cuenta.values())
    return [{"origen": o, "barras": n, "pct": _pct(n, total)}
            for o, n in sorted(cuenta.items(), key=lambda x: -x[1])]


def analizar() -> dict:
    filas = _leer_bonos()
    acc = _acumular(filas)

    juzgados = defaultdict(list)
    sin_estimacion = 0
    for f in filas:
        ritmo, origen = _estimar_sin_el_mismo(f, acc)
        estimado = (ritmo or 0) * f["piezas"]
        if estimado <= 0:
            sin_estimacion += 1
            continue
        juzgados[origen].append((f["real_min"] / estimado, f["real_min"], estimado, f))

    total = len(filas)
    origenes = []
    for origen in _ORDEN_ORIGENES:
        v = juzgados.get(origen, [])
        fila = {"origen": origen, "bonos": len(v), "pct": _pct(len(v), total)}
        if v:
            ratios = [x[0] for x in v]
            fila.update(_resumen(ratios))
            fila["histograma"] = _histograma(ratios)
        origenes.append(fila)

    # El global es lo que responde a la pregunta de la página. Se calcula sobre
    # todos los bonos juzgados, no promediando los orígenes: un origen que
    # cubre el 1% no puede pesar lo mismo que uno que cubre la mitad.
    todos = [x[0] for v in juzgados.values() for x in v]

    return {
        "generado": datetime.now(),
        "meses": _HIST_MESES,
        "bonos": total,
        "sin_estimacion": sin_estimacion,
        "global": _resumen(todos) if todos else None,
        "origenes": origenes,
        "cobertura_hoy": _cobertura_hoy(),
        "peores": _peores([x for v in juzgados.values() for x in v]),
    }


@router.get("/api/fiabilidad")
def get_fiabilidad(refrescar: bool = False):
    ahora = datetime.now()
    ts = _cache["ts"]
    if not refrescar and ts is not None and (ahora - ts).total_seconds() < _TTL_S:
        return _cache["datos"]
    datos = analizar()
    _cache.update(ts=ahora, datos=datos)
    return datos
