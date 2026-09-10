from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError

from app.db import get_erp_engine

router = APIRouter(prefix="/api")


# ─────────────────────────────────────────────────────────────────────
#  Líneas de bono: la actividad real que acompaña a la previsión de cola
# ─────────────────────────────────────────────────────────────────────
#  Traducción a T-SQL de la consulta de Access que define esta pantalla:
#  las líneas de bono en estado 1, con el operario que las fichó, la máquina
#  en la que se hicieron y el artículo que producen.
#
#  Dos columnas se añaden sobre la consulta original porque el Gantt no se
#  puede pintar sin ellas:
#    · Hinicial/Hfinal — inicio y fin reales de la línea (la barra). `Fecha`
#      es solo el instante en que el ERP grabó la línea, no su duración.
#    · Apellidos — `Nombre` a secas repite mucho (hay varios "José"), y las
#      filas del Gantt son personas: necesitan un nombre distinguible.
#
#  El JOIN con Ordenes_Bonos_Salidas puede duplicar una línea si un bono
#  declara más de un artículo de salida (medido: 3 bonos de 16.386, pero en
#  el Gantt saldría como dos barras idénticas superpuestas). Se deduplica en
#  Python por (orden, bono, línea), que es la identidad real de una barra.
# ─────────────────────────────────────────────────────────────────────

_LINEAS_SELECT = """
SELECT
    obl.IdOrden       AS idorden,
    obl.IdBono        AS idbono,
    obl.IdLinea       AS idlinea,
    obl.IdOperacion   AS idoperacion,
    obl.IdEmpleado    AS idempleado,
    ed.Nombre         AS nombre,
    ed.Apellidos      AS apellidos,
    obl.Fecha         AS fecha,
    obl.Hinicial      AS hinicial,
    obl.Hfinal        AS hfinal,
    obl.Matricula     AS matricula,
    ob.IdTrabajo      AS idtrabajo,
    a_maq.Descrip     AS descrip_maquina,
    am.Area           AS area,
    COALESCE(NULLIF(obs.Cantidad, 0), ob.CantidadTotal) AS piezas_a_fabricar,
    obs.IdArticulo    AS idarticulo_salida,
    a_sal.Descrip     AS descrip_salida
FROM Ordenes_Bonos_Lineas obl
    JOIN Ordenes_Bonos ob          ON obl.IdOrden     = ob.IdOrden
                                  AND obl.IdBono      = ob.IdBono
    JOIN Articulos_Maquinas am     ON obl.Matricula   = am.IdArticulo
    JOIN Articulos a_maq           ON am.IdArticulo   = a_maq.IdArticulo
    JOIN Ordenes_Bonos_Salidas obs ON obl.IdOrden     = obs.IdOrden
                                  AND obl.IdBono      = obs.IdBono
    JOIN Articulos a_sal           ON obs.IdArticulo  = a_sal.IdArticulo
    JOIN Empleados_Datos ed        ON obl.IdEmpleado  = ed.IdEmpleado
-- Una línea es pintable si tiene hora de inicio. NO se filtra por
-- `obl.IdEstado`: ahí el estado es TRANSITORIO —1 mientras el fichaje está
-- abierto, 2 al cerrarlo— y no significa "línea válida". Filtrando por 1 el
-- Gantt solo enseñaba lo que estaba abierto en ese instante y tiraba todo el
-- trabajo ya terminado: ETT3 pintaba 1 barra de las 9 que hizo hoy, y un día
-- pasado enseñaba 7 líneas de las 126 que hubo.
WHERE obl.Hinicial IS NOT NULL
"""

_LINEAS_ORDEN = """
ORDER BY obl.Fecha
"""

#  Los dos filtros que se le cuelgan. La consulta se COMPONE con ellos en vez
#  de parchear el WHERE con un `replace`: si el texto buscado dejara de
#  encajar, un replace no falla — se queda sin sustituir y la consulta sale sin
#  filtrar o revienta con los parámetros sin bindear, que es un 503 opaco.
_FILTRO_RANGO    = "CAST(obl.Fecha AS date) BETWEEN :desde AND :hasta"
_FILTRO_ABIERTAS = "obl.Hfinal IS NULL AND obl.Hinicial BETWEEN :limite AND :ahora"


def _consulta_lineas(filtro: str) -> str:
    """La consulta de líneas con el filtro que toque, sin duplicar el SELECT."""
    return _LINEAS_SELECT + "  AND " + filtro + _LINEAS_ORDEN


# ─────────────────────────────────────────────────────────────────────
#  Preparar la máquina no es fabricar
# ─────────────────────────────────────────────────────────────────────
#  `Ordenes_Bonos_Lineas.IdOperacion` distingue el tipo de fichaje, según la
#  tabla `Operaciones` del ERP:
#
#      0 = Funcionamiento normal      1 = Montaje utillaje      2 = Desmontaje
#
#  Cada línea tiene un solo tipo (medido: 54.619 de 54.619), así que montaje y
#  producción ya llegan como barras separadas — solo faltaba distinguirlas.
#
#  Que esto no es una etiqueta cosmética lo dicen los números: en 18 meses el
#  montaje son 5.127 h frente a 28.915 de producción (15%), pero **el 86% del
#  tiempo en bonos de 1-5 piezas** y el 56% en los de 6-50. Las líneas de
#  montaje declaran cero piezas — las 4.753, sin excepción.
#
#  El desmontaje (2) no se usa: cero líneas en 6 meses. Se agrupa con el
#  montaje por si algún día aparece, que es donde encaja.
# ─────────────────────────────────────────────────────────────────────
_OPERACION_MONTAJE = (1, 2)


def _erp(query: str, params: dict):
    try:
        with get_erp_engine().connect() as conn:
            return conn.execute(text(query), params).mappings().all()
    except SQLAlchemyError as e:
        raise HTTPException(status_code=503, detail=f"No se pudo consultar el ERP: {e.__class__.__name__}")


def _nombre_completo(r) -> str:
    partes = [p.strip() for p in (r["nombre"], r["apellidos"]) if p and p.strip()]
    return " ".join(partes) or f"#{r['idempleado']}"


def _leer_lineas(desde: date, hasta: date) -> list[dict]:
    """Las líneas de bono del rango, deduplicadas y con inicio/fin resueltos."""
    filas = _erp(_consulta_lineas(_FILTRO_RANGO), {"desde": desde, "hasta": hasta})
    return _normalizar_lineas(filas)


def _leer_abiertas(ahora: datetime) -> list[dict]:
    """Ocupación actual, independiente del día que se está consultando."""
    return _normalizar_lineas(_erp(_consulta_lineas(_FILTRO_ABIERTAS), {
        "ahora": ahora,
        "limite": ahora - timedelta(hours=_HORAS_LINEA_VIVA),
    }))


def _normalizar_lineas(filas) -> list[dict]:

    lineas, vistas = [], set()
    for r in filas:
        clave = (r["idorden"], r["idbono"], r["idlinea"])
        if clave in vistas:
            continue
        vistas.add(clave)

        # `inicio`/`fin` son lo que dibuja la barra. Si el ERP no tiene hora de
        # inicio, la línea no es pintable: cae a `Fecha` (instante de grabado).
        # Sin `Hfinal` la línea sigue abierta.
        lineas.append({
            "idorden":           r["idorden"],
            "idbono":            r["idbono"],
            "idlinea":           r["idlinea"],
            "idoperacion":       r["idoperacion"],
            "idempleado":        r["idempleado"],
            "empleado":          _nombre_completo(r),
            "fecha":             r["fecha"],
            "inicio":            r["hinicial"] or r["fecha"],
            "fin":               r["hfinal"],
            "abierta":           r["hfinal"] is None,
            "matricula":         r["matricula"],
            "idtrabajo":         r["idtrabajo"],
            "descrip_maquina":   r["descrip_maquina"],
            "area":              (r["area"] or "").strip() or None,
            "piezas_a_fabricar": r["piezas_a_fabricar"],
            "idarticulo_salida": r["idarticulo_salida"],
            "descrip_salida":    r["descrip_salida"],
        })
    return lineas


@router.get("/lineas")
def get_lineas(
    dia: Optional[date] = Query(None, description="Día a consultar (YYYY-MM-DD). Por defecto, hoy."),
):
    """La consulta en crudo, un día. No la usa el Gantt (que va por /items),
    pero es el sitio donde mirar qué está devolviendo el ERP."""
    dia = dia or date.today()
    lineas = _leer_lineas(dia, dia)
    return {"dia": dia, "ahora": datetime.now(), "total": len(lineas), "lineas": lineas}


# ─────────────────────────────────────────────────────────────────────
#  GRUPOS  (las filas del Gantt)
# ─────────────────────────────────────────────────────────────────────
#  El frontend carga los grupos UNA vez (y al cambiar de vista), no al
#  navegar entre días. Por eso la lista no puede depender de la ventana
#  visible: se toman todos los operarios/máquinas que alguna vez han tenido
#  una línea de bono (29 y 103 respectivamente — el censo es pequeño). Así
#  ninguna barra se queda sin fila a la que colgarse, se navegue a donde se
#  navegue.
# ─────────────────────────────────────────────────────────────────────

_AREAS_RECIENTES_DIAS = 90


@router.get("/grupos")
def get_grupos(vista: str = Query("empleado", pattern="^(maquina|empleado)$")):
    if vista == "maquina":
        filas = _erp("""
            SELECT DISTINCT
                obl.Matricula  AS id,
                a.Descrip      AS nombre,
                am.Area        AS area
            FROM Ordenes_Bonos_Lineas obl
                JOIN Articulos_Maquinas am ON obl.Matricula = am.IdArticulo
                JOIN Articulos a           ON am.IdArticulo = a.IdArticulo
            ORDER BY a.Descrip
        """, {})
        return [{
            "id":     str(r["id"]).strip(),
            "nombre": (r["nombre"] or "").strip() or str(r["id"]).strip(),
            "sub":    f"Matrícula {str(r['id']).strip()}",
            "area":   (r["area"] or "").strip() or "Sin área",
        } for r in filas]

    # Operarios: el censo completo, sin depender de fechas.
    censo = _erp("""
        SELECT DISTINCT
            obl.IdEmpleado AS idempleado,
            ed.Nombre      AS nombre,
            ed.Apellidos   AS apellidos
        FROM Ordenes_Bonos_Lineas obl
            JOIN Empleados_Datos ed ON obl.IdEmpleado = ed.IdEmpleado
    """, {})

    # El área de un operario no es su departamento del ERP sino la de las
    # máquinas en las que trabaja: es lo que agrupa de verdad en planta. Solo
    # se mira la actividad reciente, para que quien cambió de sección no
    # arrastre para siempre las áreas de su puesto anterior.
    areas_por_empleado: dict[str, set] = {}
    for r in _erp("""
        SELECT DISTINCT obl.IdEmpleado AS idempleado, am.Area AS area
        FROM Ordenes_Bonos_Lineas obl
            JOIN Articulos_Maquinas am ON obl.Matricula = am.IdArticulo
        WHERE obl.Fecha >= DATEADD(day, :dias, GETDATE())
          AND am.Area IS NOT NULL
    """, {"dias": -_AREAS_RECIENTES_DIAS}):
        area = (r["area"] or "").strip()
        if area:
            areas_por_empleado.setdefault(str(r["idempleado"]), set()).add(area)

    grupos = []
    for r in censo:
        gid = str(r["idempleado"])
        areas = sorted(areas_por_empleado.get(gid, ())) or ["Sin máquina"]
        grupos.append({
            "id":     gid,
            "nombre": _nombre_completo(r),
            "sub":    ", ".join(areas),
            "areas":  areas,
        })
    return sorted(grupos, key=lambda g: g["nombre"])


# ─────────────────────────────────────────────────────────────────────
#  ITEMS  (las barras del Gantt)
# ─────────────────────────────────────────────────────────────────────

def _dia_local(dt: datetime) -> date:
    """El frontend manda `days[0].toISOString()`: medianoche LOCAL escrita en
    UTC. Hay que devolverla a hora local antes de quedarse con el día o, en
    horario español, se pierde un día entero.

    Depende de la zona del proceso, así que el contenedor fija TZ=Europe/Madrid
    (ver docker-compose.yml); si no, dentro de Docker sería UTC."""
    return (dt.astimezone() if dt.tzinfo else dt).date()


# ─────────────────────────────────────────────────────────────────────
#  DURACIÓN ESTIMADA DE UN BONO
# ─────────────────────────────────────────────────────────────────────
#  Cadena de prioridad, en este orden:
#    1. tiempo TEÓRICO del escandallo del ERP (Trabajos_ManoObra + montaje)
#    2. MEDIA de los registros reales: artículo → trabajo → máquina
#    3. nada: la barra se marca "sin tiempo" y salta el aviso
#
#  Cobertura medida el 2026-09-04 sobre los 570 bonos abiertos:
#    · escandallo .......................  9 bonos ( 1,6 %)
#    · campos Media*/Moda* de Ordenes_Bonos: 0-1,8 % — están vacíos, por eso
#      la media NO se lee del ERP sino que se calcula aquí desde el histórico
#      de líneas ya cerradas
#    · media del histórico ............... los 13 bonos del Gantt de hoy
#      (6 por artículo, 7 por máquina)
#
#  Dos limitaciones conocidas y asumidas:
#    · la media por máquina es gruesa — una misma máquina hace piezas muy
#      distintas — pero es el último escalón antes del aviso;
#    · la preparación se estima por separado; las medias son orientativas y
#      dependen de la calidad de los fichajes del ERP.
# ─────────────────────────────────────────────────────────────────────

_ESTIMA_TTL_S     = 600   # el escandallo y el histórico no cambian por minutos
_HIST_MESES       = 18
_MIN_BONOS_MEDIA  = 3     # con menos bonos, la media es ruido
#  Cuánto puede apartarse el escandallo del histórico del artículo antes de
#  dejar de creérselo. Ver `_estimar`.
_FACTOR_ESCANDALLO = 5
_escandallos_avisados: set = set()
_HORAS_LINEA_VIVA = 24    # una línea abierta más vieja que esto es fantasma, no trabajo

_SQL_TEORICO = """
WITH mano_obra AS (
    SELECT IdTrabajo, SUM(Duracion) * 1440.0 AS MinPieza
    FROM Trabajos_ManoObra
    WHERE Duracion > 0        -- 0 no es una medición, es la casilla sin rellenar
    GROUP BY IdTrabajo
)
SELECT
    ob.IdOrden AS idorden,
    ob.IdBono  AS idbono,
    -- Duracion viene en DÍAS (IdUnidadDuracion='D'): x1440 -> min/pieza.
    -- Se prefiere el detalle de Trabajos_ManoObra sobre Trabajos_Fases.TiempoMO,
    -- que es la copia desnormalizada y a veces está sin recalcular.
    COALESCE(mo.MinPieza, NULLIF(tf.TiempoMO, 0) * 1440.0) AS min_pieza,
    COALESCE(NULLIF(ob.TiempoMontaje, 0), 0)
      + COALESCE(NULLIF(ob.TiempoDesMontaje, 0), 0)        AS setup_min
FROM Ordenes_Bonos ob
    LEFT JOIN mano_obra mo      ON mo.IdTrabajo = ob.IdTrabajo
    LEFT JOIN Trabajos_Fases tf ON tf.IdTrabajo = ob.IdTrabajo
WHERE ob.IdEstado IN (0, 1, 3)   -- solo bonos abiertos; los cerrados ya tienen minutos reales
  -- Basta con que el ERP declare UNA de las dos cosas. Antes se exigía el
  -- min/pieza, y como el escandallo (10 bonos) y la preparación (18) casi no
  -- se solapan -- solo 1 bono tiene ambas --, 17 de los 18 tiempos de montaje
  -- declarados se tiraban a la basura: el bono caía a la media histórica y
  -- perdía su setup por el camino.
  AND (COALESCE(mo.MinPieza, NULLIF(tf.TiempoMO, 0) * 1440.0) > 0
       OR COALESCE(NULLIF(ob.TiempoMontaje, 0), 0)
        + COALESCE(NULLIF(ob.TiempoDesMontaje, 0), 0) > 0)
"""

#  OJO con el CASE por IdOperacion: sumar todos los minutos y dividirlos entre
#  las piezas mete el montaje dentro del ritmo. Y como el montaje no produce
#  nada, se reparte entre las piezas del lote: en un bono de 3 piezas con 22 min
#  de montaje y 6 de fabricación salía un "min/pieza" de 9,3 cuando el ritmo real
#  es 1,9 -- cinco veces inflado. Ese número contaminaba la media del artículo y
#  luego se aplicaba a lotes de miles de piezas. El ritmo se mide solo con
#  producción; la preparación va aparte, en su propia columna.
_SQL_MEDIAS = """
WITH bono_min AS (
    SELECT obl.IdOrden, obl.IdBono,
           SUM(CASE WHEN obl.IdOperacion = 0
                    THEN DATEDIFF(minute, obl.Hinicial, obl.Hfinal) ELSE 0 END) AS min_produccion,
           SUM(CASE WHEN obl.IdOperacion IN (1, 2)
                    THEN DATEDIFF(minute, obl.Hinicial, obl.Hfinal) ELSE 0 END) AS min_montaje
    FROM Ordenes_Bonos_Lineas obl
    WHERE obl.Hinicial IS NOT NULL
      AND obl.Hfinal   IS NOT NULL
      AND obl.Hfinal   > obl.Hinicial
      AND obl.Fecha   >= DATEADD(month, :meses, GETDATE())
    GROUP BY obl.IdOrden, obl.IdBono
)
SELECT
    obs.IdArticulo AS idarticulo,
    ob.IdTrabajo   AS idtrabajo,
    ob.Matricula   AS matricula,
    COUNT(*)                    AS n,
    SUM(bm.min_produccion)      AS minutos,
    SUM(bm.min_montaje)         AS minutos_montaje,
    SUM(obs.Cantidad)           AS piezas
FROM bono_min bm
    JOIN Ordenes_Bonos ob          ON ob.IdOrden  = bm.IdOrden AND ob.IdBono  = bm.IdBono
    JOIN Ordenes_Bonos_Salidas obs ON obs.IdOrden = bm.IdOrden AND obs.IdBono = bm.IdBono
WHERE ob.IdEstado = 2            -- solo bonos terminados: los abiertos aún no miden nada
  AND obs.Cantidad > 0
  -- Un bono con montaje pero sin producción aportaría piezas con cero minutos
  -- y desinflaría el ritmo de toda la clave.
  AND bm.min_produccion > 0
GROUP BY obs.IdArticulo, ob.IdTrabajo, ob.Matricula
"""

#  Cuánto se tarda en montar el utillaje, por máquina y por trabajo. Un montaje
#  no produce piezas, así que no se puede estimar con el modelo de piezas: sin
#  esto, la barra de Elías preparando la 001 se estiraba hasta las 20:08 porque
#  se le aplicaba el tiempo de fabricar el bono entero.
#
#  Varía muchísimo entre máquinas y por eso se guarda por matrícula: la 107 monta
#  en 11 min de media y la 001 en 113. El tope de 480 min (una jornada) descarta
#  las líneas fantasma que nadie cerró.
_SQL_MONTAJE = """
SELECT
    obl.Matricula AS matricula,
    ob.IdTrabajo  AS idtrabajo,
    COUNT(*)      AS n,
    AVG(CAST(DATEDIFF(minute, obl.Hinicial, obl.Hfinal) AS float)) AS media
FROM Ordenes_Bonos_Lineas obl
    JOIN Ordenes_Bonos ob ON ob.IdOrden = obl.IdOrden AND ob.IdBono = obl.IdBono
WHERE obl.IdOperacion IN (1, 2)
  AND obl.Hfinal > obl.Hinicial
  AND DATEDIFF(minute, obl.Hinicial, obl.Hfinal) BETWEEN 1 AND 480
  AND obl.Hinicial >= DATEADD(month, :meses, GETDATE())
GROUP BY obl.Matricula, ob.IdTrabajo
"""

#  Último recurso si la máquina no tiene histórico de montajes: la media global
#  medida sobre 18 meses.
_MONTAJE_POR_DEFECTO_MIN = 31

_cache_estima = {"ts": None, "teoricos": {}, "medias": {"articulo": {}, "trabajo": {}, "maquina": {}},
                 "montajes": {"trabajo": {}, "maquina": {}}}


def _cargar_teoricos() -> dict:
    """El escandallo del ERP, SIN cachear: leído en cada request.

    Es el único dato de la cadena que una persona edita y espera ver reflejado
    al momento. Cuesta 16 ms sobre 30 filas —solo mira bonos abiertos—, así que
    cachearlo solo servía para que un tiempo recién metido tardara hasta diez
    minutos en aparecer. Los agregados históricos, que sí son caros (123 y 52
    ms sobre miles de filas), siguen en `_cargar_estimaciones`: esos hablan de
    18 meses de bonos cerrados y no cambian de un minuto a otro.

    `min_pieza` puede venir a None: hay bonos que declaran la preparación y no
    el escandallo. Se guardan igual, porque el setup sirve aunque el ritmo
    tenga que salir del histórico.
    """
    try:
        with get_erp_engine().connect() as conn:
            teoricos = {
                (int(r["idorden"]), int(r["idbono"])):
                    (float(r["setup_min"] or 0),
                     float(r["min_pieza"]) if r["min_pieza"] is not None else None)
                for r in conn.execute(text(_SQL_TEORICO)).mappings()
            }
    except SQLAlchemyError as e:
        print(f"[items] escandallo no disponible, se reutiliza el último: {e.__class__.__name__}")
        return _cache_estima["teoricos"]
    _cache_estima["teoricos"] = teoricos
    return teoricos


def _cargar_estimaciones():
    """Medias históricas, cacheadas _ESTIMA_TTL_S segundos. El escandallo va
    aparte y en vivo (ver `_cargar_teoricos`).

    Si el ERP falla se reutiliza la última caché aunque esté caducada: es
    preferible estimar con datos de hace diez minutos que marcar de golpe
    todas las barras como "sin tiempo"."""
    ahora = datetime.now()
    teoricos = _cargar_teoricos()
    ts = _cache_estima["ts"]
    if ts is not None and (ahora - ts).total_seconds() < _ESTIMA_TTL_S:
        return teoricos, _cache_estima["medias"]

    try:
        with get_erp_engine().connect() as conn:
            medias = {"articulo": {}, "trabajo": {}, "maquina": {}}
            for r in conn.execute(text(_SQL_MEDIAS), {"meses": -_HIST_MESES}).mappings():
                for nivel, clave in (("articulo", r["idarticulo"]),
                                     ("trabajo",  r["idtrabajo"]),
                                     ("maquina",  r["matricula"])):
                    if clave is None:
                        continue
                    if isinstance(clave, str):
                        clave = clave.strip()
                    acc = medias[nivel].setdefault(clave, {"n": 0, "minutos": 0.0, "piezas": 0.0})
                    acc["n"]       += int(r["n"] or 0)
                    acc["minutos"] += float(r["minutos"] or 0)
                    acc["piezas"]  += float(r["piezas"] or 0)

            montajes = {"trabajo": {}, "maquina": {}}
            for r in conn.execute(text(_SQL_MONTAJE), {"meses": -_HIST_MESES}).mappings():
                for nivel, clave in (("trabajo", r["idtrabajo"]), ("maquina", r["matricula"])):
                    if clave is None:
                        continue
                    if isinstance(clave, str):
                        clave = clave.strip()
                    acc = montajes[nivel].setdefault(clave, {"n": 0, "minutos": 0.0})
                    acc["n"]       += int(r["n"] or 0)
                    acc["minutos"] += float(r["media"] or 0) * int(r["n"] or 0)
    except SQLAlchemyError as e:
        print(f"[items] históricos no disponibles, se reutiliza la caché: {e.__class__.__name__}")
        return teoricos, _cache_estima["medias"]

    # `teoricos` no entra aquí: lo refresca y guarda `_cargar_teoricos` en cada
    # request, y meterlo en este update lo ataría otra vez al TTL de 10 minutos.
    _cache_estima.update(ts=ahora, medias=medias, montajes=montajes)
    return teoricos, medias


def _minutos_montaje(linea: dict) -> float:
    """Cuánto suele durar montar el utillaje de este bono, en minutos.

    Por máquina primero (es lo que determina el montaje: la 107 son 11 min y la
    001 son 113), con respaldo al trabajo y, si no hay histórico de ninguno, la
    media global."""
    montajes = _cache_estima["montajes"]
    for nivel, clave in (("maquina", (linea["matricula"] or "").strip()),
                         ("trabajo", linea["idtrabajo"])):
        acc = montajes[nivel].get(clave)
        if acc and acc["n"] >= _MIN_BONOS_MEDIA:
            return acc["minutos"] / acc["n"]
    return _MONTAJE_POR_DEFECTO_MIN


def _avisar_escandallo(linea, teorico, historico, desvio) -> None:
    """Un escandallo descartado no puede pasar en silencio: es un dato que
    alguien metió a mano y que hay que corregir en el ERP. Se avisa una vez por
    trabajo y proceso, no en cada request."""
    clave = linea["idtrabajo"]
    if clave in _escandallos_avisados:
        return
    _escandallos_avisados.add(clave)
    print(f"[items] escandallo descartado en el trabajo {clave} "
          f"({linea['idorden']}/{linea['idbono']}): dice {teorico:.3f} min/pieza y el "
          f"histórico del artículo da {historico:.3f} ({desvio:.0f}x). Se usa el histórico.")


def _estimar(linea: dict, teoricos: dict, medias: dict):
    """(min/pieza, setup en minutos, origen) para el bono, o (None, 0, None).

    Devuelve el RITMO, no el total: quien llama multiplica por las piezas que
    de verdad quedan por hacer. Es la diferencia entre "cuánto cuesta el bono
    entero" y "cuánto falta", que es lo que hay que pintar."""
    teorico = teoricos.get((linea["idorden"], linea["idbono"]))
    setup_erp, min_pieza_erp = teorico if teorico else (0.0, None)

    # La preparación es del BONO y el ritmo es del histórico: son dos datos
    # independientes en el ERP, mantenidos por gente distinta y en tablas
    # distintas. Atarlos hacía que un bono con su montaje declarado lo perdiera
    # solo porque el escandallo de su trabajo estaba vacío. Si el ERP no lo
    # declara se usa lo que suele tardarse en montar esa máquina.
    setup = setup_erp if setup_erp > 0 else _minutos_montaje(linea)

    matricula = (linea["matricula"] or "").strip()

    # Un escandallo mal metido no puede arrastrar al Gantt. El trabajo 1932
    # dice 345 min/pieza y sus 7 bonos cerrados dan 5,86: alguien puso ahí el
    # total de la operación en vez del tiempo unitario, y 6585/60 pintaba una
    # barra de 13 días. Se contrasta contra el histórico del artículo, que es
    # un dato MEDIDO; si se aparta más de _FACTOR_ESCANDALLO veces en
    # cualquiera de los dos sentidos, no se usa y se cae al histórico.
    #
    # El umbral sale de los datos: de los 7 bonos vivos con escandallo, seis
    # caen entre 0,9x y 1,5x del histórico y el séptimo en 53x. No hay nada en
    # medio, así que el corte no es delicado.
    if min_pieza_erp:
        acc = medias["articulo"].get(linea["idarticulo_salida"])
        if acc and acc["n"] >= _MIN_BONOS_MEDIA and acc["piezas"] > 0:
            hist = acc["minutos"] / acc["piezas"]
            desvio = min_pieza_erp / hist if hist > 0 else 1.0
            if not (1 / _FACTOR_ESCANDALLO <= desvio <= _FACTOR_ESCANDALLO):
                _avisar_escandallo(linea, min_pieza_erp, hist, desvio)
                min_pieza_erp = None

    if min_pieza_erp:
        return min_pieza_erp, setup, "teorico"

    for origen, nivel, clave in (
        ("media_articulo", "articulo", linea["idarticulo_salida"]),
        ("media_trabajo",  "trabajo",  linea["idtrabajo"]),
        ("media_maquina",  "maquina",  matricula),
    ):
        acc = medias[nivel].get(clave)
        if acc and acc["n"] >= _MIN_BONOS_MEDIA and acc["piezas"] > 0:
            return acc["minutos"] / acc["piezas"], setup, origen

    return None, setup, None


def _avance_por_bono(lineas: list[dict], ahora: datetime) -> dict:
    """Lo que lleva cada bono: minutos gastados, piezas declaradas y cuántos
    operarios lo tienen abierto ahora.

    Se cuentan TODAS las líneas del bono, no solo las de la ventana visible:
    un bono que empezó ayer ya lleva tiempo consumido y lo que queda por hacer
    hoy es menos. Son minutos-hombre (dos operarios a la vez gastan dos
    minutos por cada minuto de reloj), igual que la media histórica.

    OJO con las líneas fantasma: el ERP tiene líneas abiertas que nadie cerró
    hace meses o años. Contarlas hasta GETDATE() dispara el consumo (medido:
    un bono con 3.383 min "gastados" que en realidad lleva unas horas) y además
    infla el recuento de operarios activos, que es el divisor del tiempo que
    queda. Una línea abierta solo cuenta si empezó en las últimas
    _HORAS_LINEA_VIVA horas; el resto aporta cero."""
    ordenes = sorted({l["idorden"] for l in lineas})
    if not ordenes:
        return {}

    consulta = text("""
        WITH consumo AS (
            SELECT *, DATEDIFF(minute, Hinicial,
                CASE WHEN Hfinal >= Hinicial THEN Hfinal
                     WHEN Hfinal IS NULL AND Hinicial BETWEEN :limite AND :ahora
                     THEN :ahora ELSE Hinicial END) AS minutos_linea
            FROM Ordenes_Bonos_Lineas
            WHERE Hinicial IS NOT NULL AND IdOrden IN :ordenes
        )
        SELECT IdOrden AS idorden, IdBono AS idbono,
               SUM(minutos_linea) AS minutos,
               SUM(CASE WHEN IdOperacion = 0 THEN minutos_linea ELSE 0 END) AS min_produccion,
               SUM(CASE WHEN IdOperacion IN (1, 2) THEN minutos_linea ELSE 0 END) AS min_montaje,
               COUNT(DISTINCT CASE
                     WHEN Hfinal IS NULL
                      AND IdOperacion = 0
                      AND Hinicial BETWEEN :limite AND :ahora
                     THEN IdEmpleado END) AS operarios_activos,
               -- Los que están MONTANDO. Mientras solo hay preparación fichada
               -- no existe ninguna línea de producción abierta y el recuento de
               -- arriba da cero, así que no habría por quién dividir la
               -- fabricación que viene detrás: en 6469/40 los tres montadores
               -- recibían cada uno los 453 minutos del bono entero.
               COUNT(DISTINCT CASE
                     WHEN Hfinal IS NULL
                      AND IdOperacion IN (1, 2)
                      AND Hinicial BETWEEN :limite AND :ahora
                     THEN IdEmpleado END) AS operarios_montando,
               SUM(ISNULL(TotalPiezas, 0)) AS piezas
        FROM consumo
        GROUP BY IdOrden, IdBono
    """).bindparams(bindparam("ordenes", expanding=True))

    try:
        with get_erp_engine().connect() as conn:
            filas = conn.execute(consulta, {
                "ordenes": ordenes, "ahora": ahora,
                "limite": ahora - timedelta(hours=_HORAS_LINEA_VIVA),
            }).mappings().all()
    except SQLAlchemyError:
        raise HTTPException(status_code=503, detail="No se pudo consultar el avance del ERP")

    return {
        (r["idorden"], r["idbono"]): {
            "minutos":   float(r["minutos"] or 0),
            "min_produccion": float(r["min_produccion"] or 0),
            "min_montaje": float(r["min_montaje"] or 0),
            "operarios": max(1, int(r["operarios_activos"] or 0)),
            "montando":  max(1, int(r["operarios_montando"] or 0)),
            "piezas":    float(r["piezas"] or 0),
        }
        for r in filas
    }


@router.get("/items")
def get_items(
    vista: str = Query("empleado", pattern="^(maquina|empleado)$"),
    desde: Optional[datetime] = None,
    hasta: Optional[datetime] = None,
):
    hoy = date.today()
    d0 = _dia_local(desde) if desde else hoy
    # `hasta` llega como el día siguiente al último visible (fin exclusivo).
    d1 = _dia_local(hasta - timedelta(seconds=1)) if hasta else hoy
    if d1 < d0:
        d1 = d0

    ahora = datetime.now()
    lineas = _leer_lineas(d0, d1)
    abiertas = _leer_abiertas(ahora) if d1 >= hoy else []
    # La ocupación de hoy debe seguir reservada al navegar a mañana. También
    # permite dibujar la continuación de un bono iniciado fuera de la ventana.
    por_id = {(l["idorden"], l["idbono"], l["idlinea"]): l for l in lineas}
    por_id.update({(l["idorden"], l["idbono"], l["idlinea"]): l for l in abiertas})
    lineas = list(por_id.values())
    teoricos, medias = _cargar_estimaciones()
    avance = _avance_por_bono([l for l in lineas if l["abierta"]], ahora)

    items = []
    for l in lineas:
        inicio  = l["inicio"]
        abierta = (l["abierta"] and d1 >= hoy
                   and timedelta(0) <= ahora - inicio <= timedelta(hours=_HORAS_LINEA_VIVA))
        fin     = l["fin"] or ahora
        if l["abierta"] and not abierta:
            fin = min(ahora, max(inicio, inicio.replace(hour=JORNADA_FIN, minute=0, second=0, microsecond=0)))
        min_real = None if l["abierta"] else round((fin - inicio).total_seconds() / 60)

        # La línea abierta es trabajo EN CURSO; la cerrada, trabajo hecho.
        # No hay un tercer estado que inventar: el ERP no dice nada más.
        montaje = l.get("idoperacion") in _OPERACION_MONTAJE
        item = {
            "id":         f"{l['idorden']}-{l['idbono']}-{l['idlinea']}",
            "recurso_id": str(l["idempleado"]) if vista == "empleado" else str(l["matricula"]).strip(),
            "idempleado": str(l["idempleado"]),
            "matricula": (l["matricula"] or "").strip(),
            "tipo":       "real" if abierta else "parcial" if l["abierta"] else "trabajado",
            "estado":     "plazo" if abierta else "parcial" if l["abierta"] else "completado",
            "en_curso":   abierta,
            # Preparar la máquina no es fabricar: son fichajes distintos y hay
            # que poder distinguirlos. Ver _OPERACION_MONTAJE.
            "es_montaje":   montaje,
            "tipo_trabajo": "montaje" if montaje else "produccion",
            "estimado":   False,
            "start":      inicio,
            "end":        fin,
            "idorden":    l["idorden"],
            "idbono":     l["idbono"],
            "art":        l["descrip_salida"],
            "art_id":     l["idarticulo_salida"],
            # El área es del BONO —la de su máquina—, no de quien lo hace. Un
            # operario que toca tres secciones no convierte en ESTRUCTURAS un
            # bono de CHAPA. Coincide con `PersVTrazaordenesOperarios.Area`.
            "area":       l["area"],
            # En la vista de operarios interesa saber la máquina; en la de
            # máquinas, quién estaba en ella. Solo el nombre de la máquina: la
            # matrícula delante comía sitio en una barra que suele ser estrecha
            # y no aporta nada a quien lee el Gantt.
            "operacion":  (l["descrip_maquina"] if vista == "empleado" else l["empleado"]),
            "operarios":  l["empleado"],
            "piezas":     l["piezas_a_fabricar"],
            "min_real":   min_real,
            "sin_tiempo": False,
        }

        # Solo se estima lo que sigue abierto: una línea cerrada ya tiene su
        # tiempo real medido y no hay nada que predecir.
        if abierta:
            _proyectar(item, l, ahora, teoricos, medias, avance)

        items.append(item)

    # La cola solo tiene sentido si la ventana llega a hoy o más allá: en un
    # día pasado no había "programado", había lo que pasó.
    if d1 >= hoy:
        hasta_dt = datetime.combine(d1, datetime.min.time()).replace(hour=JORNADA_FIN)
        ids_abiertas = {f"{l['idorden']}-{l['idbono']}-{l['idlinea']}" for l in abiertas}
        ocupado_hasta = _ocupacion_actual(
            [it for it in items if it["id"] in ids_abiertas], hasta_dt, ahora,
        )
        cola = _encolar(vista, ocupado_hasta, hasta_dt, ahora, teoricos, medias)
    else:
        cola = []

    inicio_ventana = datetime.combine(d0, datetime.min.time())
    fin_ventana = datetime.combine(d1 + timedelta(days=1), datetime.min.time())
    # `_continuar` va sobre la lista ya fundida y siempre se llama: es lo que
    # pinta la reserva de un bono en montaje y, de paso, lo que limpia la clave
    # interna que `_proyectar` deja en esas barras.
    visibles = _fundir_montaje(items)
    return [it for it in visibles + _continuar(visibles, ahora) + cola
            if it["end"] > inicio_ventana and it["start"] < fin_ventana]


#  Margen antes de dar un bono por retrasado. El ritmo real contra el esperado
#  baila solo con que la preparación caiga dentro o fuera de lo ya declarado;
#  sin margen, el ámbar sería ruido.
_TOLERANCIA_RITMO = 0.15


# ─────────────────────────────────────────────────────────────────────
#  COLA: los bonos que un operario tiene asignados y aún no ha empezado
# ─────────────────────────────────────────────────────────────────────
#  Fuente: `persV_DatosAsociadoEmpleado`, vista del ERP sobre
#  `Pers_EmpleadosOrdenBono` (Orden, Bono → IdEmpleado). Ahí SÍ está la
#  asignación operario↔bono; `Ordenes_Bonos.IdEmpleado`, que es donde parecía
#  que debía vivir, está a NULL en los 562 bonos abiertos.
#
#  La vista trae ya resuelto todo lo que hace falta para una barra: máquina,
#  área, artículo, piezas objetivo, piezas hechas y la posición manual
#  (`Conf_OrdenesBonos.ordenar`). Medido: 19.517 asignaciones, 25 empleados,
#  238 de los 562 bonos abiertos con operario.
#
#  OJO con `Fabricadas`: sale de `Ordenes_Bonos_Salidas.CantidadTotal`. En esa
#  tabla `Cantidad` es el objetivo y `CantidadTotal` lo ya producido — justo al
#  revés que en `Ordenes_Bonos`, donde `CantidadTotal` es el objetivo.
#
#  Se toma solo `IdEstado = 0` (aún sin arrancar): los de estado 1 ya salen
#  como barras reales de su propio fichaje.
# ─────────────────────────────────────────────────────────────────────

_COLA_QUERY = """
SELECT
    v.IdEmpleado                              AS idempleado,
    ed.Nombre                                 AS nombre,
    ed.Apellidos                              AS apellidos,
    v.idorden                                 AS idorden,
    v.IdBono                                  AS idbono,
    v.ordenar                                 AS ordenar,
    v.CdgMaq                                  AS matricula,
    v.Maquina                                 AS descrip_maquina,
    v.Area                                    AS area,
    v.ArtFabricar                             AS descrip_salida,
    TRY_CAST(v.PiezasFabricar AS decimal(18,4)) AS objetivo,
    v.Fabricadas                              AS fabricadas,
    ob.IdTrabajo                              AS idtrabajo,
    obs.IdArticulo                            AS idarticulo_salida
FROM persV_DatosAsociadoEmpleado v
    JOIN Ordenes_Bonos ob            ON ob.IdOrden  = v.idorden AND ob.IdBono  = v.IdBono
    JOIN Empleados_Datos ed          ON ed.IdEmpleado = v.IdEmpleado
    LEFT JOIN Ordenes_Bonos_Salidas obs ON obs.IdOrden = v.idorden AND obs.IdBono = v.IdBono
WHERE ob.IdEstado = 0
"""

#  Un bono en cola sin tiempo estimado no se puede dimensionar. Se le da un
#  bloque nominal para que siga ocupando su sitio en la cola (si no, los que
#  van detrás se adelantarían como si no existiera) y va marcado `sin_tiempo`.
_MIN_BLOQUE_SIN_TIEMPO = 60


# ─────────────────────────────────────────────────────────────────────
#  SEMÁFORO: ¿puede ESTE operario trabajar ESTE bono ahora mismo?
# ─────────────────────────────────────────────────────────────────────
#  `dbo.persFTrazaordenesOperariosColor(idorden, idbono, idempleado)` es la
#  función del ERP que alimenta el semáforo del programa de producción. Es la
#  misma que consume la vista `PersVTrazaordenesOperarios`; se invoca aquí
#  directamente sobre nuestra consulta en vez de usar esa vista, porque la
#  vista además reescribe `ordenar = 0` como 999 — un valor que se colaría
#  como posición real y colocaría los bonos sin secuencia por delante de los
#  que sí la tienen.
#
#  Devuelve un RGB concatenado. El rojo NO es `IdEstado = 3`: de las 86 filas
#  rojas medidas, cero son bonos bloqueados. Es disponibilidad *para esa
#  persona* — falta material, lo tiene cogido otro, falta una fase previa. Por
#  eso el mismo bono puede ser verde para uno y rojo para otro.
#
#  La función está WITH ENCRYPTION: es una caja negra. Sabemos QUE algo está
#  rojo, no POR QUÉ.
# ─────────────────────────────────────────────────────────────────────

_SEMAFORO = {
    '153255255': 'en_curso',    # azul  — la está trabajando ahora mismo
    '000204051': 'disponible',  # verde — puede ponerse con ella
    '255051051': 'bloqueada',   # rojo  — la tiene asignada pero no puede
}

#  Orden en que se sirve la cola. Lo que se puede hacer va primero; dentro de
#  cada grupo sigue mandando la secuencia manual del ERP.
_PRIO_SEMAFORO = {'en_curso': 0, 'disponible': 1, 'bloqueada': 2}

#  A partir de cuántos asignados un bono se trata como CUADRILLA: trabajan a la
#  vez y el tiempo estimado —que son minutos-HOMBRE— se reparte entre ellos.
#
#  Medido sobre los bonos cerrados que conservan su asignación, mirando si dos
#  fichajes del mismo bono se solapan en el tiempo:
#
#      asignados   bonos   media que llegan a ficharlo   solapan
#          1       11.819            1,02                  0%
#          2        2.678            1,47                 28%
#          3          281            2,20                 54%
#          4          158            3,86                 95%
#
#  Con DOS, "asignado" significa casi siempre "que lo coja quien pueda": el 72%
#  de las veces acaba haciéndolo una sola persona, y repartir el tiempo entre
#  dos partiría por la mitad 2.678 bonos que nadie hace en pareja. Con tres ya
#  es mayoría y con cuatro es la norma.
_MIN_CUADRILLA = 3

#  La función se evalúa fila a fila: la consulta pasa de 12 ms a ~360 ms. Se
#  cachea porque el color cambia cuando llega material o alguien coge un bono
#  —minutos, no segundos— y el Gantt se refresca solo cada pocos minutos.
_SEMAFORO_TTL_S = 120
_cache_semaforo = {"ts": None, "mapa": {}}

_SEMAFORO_QUERY = """
SELECT v.idorden, v.IdBono AS idbono, v.IdEmpleado AS idempleado, col.color
FROM persV_DatosAsociadoEmpleado v
    JOIN Ordenes_Bonos ob ON ob.IdOrden = v.idorden AND ob.IdBono = v.IdBono
    OUTER APPLY dbo.persFTrazaordenesOperariosColor(v.idorden, v.IdBono, v.IdEmpleado) col
WHERE ob.IdEstado = 0
"""


def _cargar_semaforo() -> dict:
    """{(idorden, idbono, idempleado): 'disponible'|'bloqueada'|'en_curso'}.

    Si el ERP falla se reutiliza la última caché aunque esté caducada: es mejor
    ordenar con colores de hace unos minutos que perder el semáforo entero y
    volver a servir la cola en un orden que el operario no puede seguir."""
    ahora = datetime.now()
    ts = _cache_semaforo["ts"]
    if ts is not None and (ahora - ts).total_seconds() < _SEMAFORO_TTL_S:
        return _cache_semaforo["mapa"]
    try:
        filas = _erp(_SEMAFORO_QUERY, {})
    except HTTPException:
        print("[items] semáforo no disponible, se reutiliza la caché")
        return _cache_semaforo["mapa"]

    mapa = {
        (r["idorden"], r["idbono"], r["idempleado"]): _SEMAFORO.get(r["color"], 'disponible')
        for r in filas
    }
    _cache_semaforo.update(ts=ahora, mapa=mapa)
    return mapa

JORNADA_INICIO = 7
#  Medido sobre 6 meses de fichajes: el ultimo cierre del dia es 15:01 en 38
#  dias, 15:02 en 17, 15:00 en 12 y 15:03 en 10 -- 77 de 110. Por minuto, las
#  15:00 concentran 506 cierres y las 16:00 solo 98. La jornada acaba a las 15,
#  no a las 16: con 16 la app daba 540 min/dia cuando son 480, un 12,5% de
#  capacidad inflada en toda proyeccion. Debe coincidir con WORK_FIN en app.js
#  o las barras se pintan en el pixel equivocado.
JORNADA_FIN    = 15


def _siguiente_hueco(dt: datetime) -> datetime:
    """El primer instante laborable a partir de `dt` (07:00–15:00, L-V)."""
    t = dt
    for _ in range(14):
        if t.weekday() >= 5:
            t = (t + timedelta(days=1)).replace(hour=JORNADA_INICIO, minute=0, second=0, microsecond=0)
            continue
        if t.hour < JORNADA_INICIO:
            return t.replace(hour=JORNADA_INICIO, minute=0, second=0, microsecond=0)
        if t.hour >= JORNADA_FIN:
            t = (t + timedelta(days=1)).replace(hour=JORNADA_INICIO, minute=0, second=0, microsecond=0)
            continue
        return t
    return t


def _sumar_laborables(inicio: datetime, minutos: float) -> datetime:
    """Avanza `minutos` de trabajo desde `inicio` sin salirse de la jornada.

    Cuenta la jornada entera (07:00–15:00 = 480 min) sin descontar el descanso
    de 11:00–11:15 a propósito: el eje del Gantt tampoco lo comprime, lo pinta
    como una banda. Descontarlo aquí desalinearía las barras del eje."""
    t = _siguiente_hueco(inicio)
    restante = float(minutos)
    for _ in range(400):
        fin_jornada = t.replace(hour=JORNADA_FIN, minute=0, second=0, microsecond=0)
        hueco = (fin_jornada - t).total_seconds() / 60
        if restante <= hueco:
            return t + timedelta(minutes=restante)
        restante -= hueco
        t = _siguiente_hueco(fin_jornada)
    return t


def _minutos_laborables_entre(inicio: datetime, fin: datetime) -> float:
    """Mide el mismo calendario que usa la proyección, sin noches ni fines de semana."""
    t, total = inicio, 0.0
    while t < fin:
        apertura = t.replace(hour=JORNADA_INICIO, minute=0, second=0, microsecond=0)
        cierre = t.replace(hour=JORNADA_FIN, minute=0, second=0, microsecond=0)
        if t.weekday() < 5:
            total += max(0.0, (min(fin, cierre) - max(t, apertura)).total_seconds() / 60)
        t = (t + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return total


def _leer_cola() -> list[dict]:
    """Los bonos asignados y aún sin empezar, deduplicados por (bono, operario).

    La vista repite fila cuando un bono declara más de un artículo de salida,
    igual que la consulta de líneas."""
    filas = _erp(_COLA_QUERY, {})
    semaforo = _cargar_semaforo()
    cola, vistas = [], set()
    for r in filas:
        clave = (r["idorden"], r["idbono"], r["idempleado"])
        if clave in vistas:
            continue
        vistas.add(clave)
        cola.append({
            # Si el ERP no devuelve color, se asume disponible: es preferible
            # ofrecer trabajo de más que esconderlo por un fallo de la función.
            "semaforo":          semaforo.get(clave, 'disponible'),
            "idorden":           r["idorden"],
            "idbono":            r["idbono"],
            "idempleado":        r["idempleado"],
            "empleado":          _nombre_completo(r),
            "ordenar":           int(r["ordenar"] or 0),
            "matricula":         (r["matricula"] or "").strip(),
            "descrip_maquina":   r["descrip_maquina"],
            "area":              (r["area"] or "").strip() or None,
            "descrip_salida":    r["descrip_salida"],
            "idarticulo_salida": r["idarticulo_salida"],
            "idtrabajo":         r["idtrabajo"],
            "piezas_a_fabricar": float(r["objetivo"] or 0),
            "fabricadas":        float(r["fabricadas"] or 0),
        })
    return cola


def _ocupacion_actual(items: list[dict], hasta: datetime, ahora: datetime) -> dict:
    """Reserva operario y máquina hasta que `_proyectar` dice que se liberan.

    `libre_desde` responde a "¿cuándo queda libre el recurso?", que no es el
    fin de la barra: un montaje libera cuando acaba la producción que viene
    detrás, no cuando termina de preparar la máquina.

    Si falta, es que NO SE SABE —el fichaje sigue abierto y no hay con qué
    dimensionarlo— y entonces se reserva la ventana entera: ignorar cuánto
    queda no es estar libre. Es distinto de saber que ya no queda trabajo,
    que es un `libre_desde` en `ahora` y suelta el recurso.

    Devuelve INTERVALOS `(inicio, fin)` por recurso, no un "libre a partir de".
    La diferencia importa: con un solo instante, reservar la máquina 004
    mañana de 07:09 a 07:41 la marcaba ocupada desde ahora mismo, y a José
    Ramón —libre hoy a las 11:47, con sus dos bonos en esa máquina— se le iba
    todo a mañana por un trabajo que ni siquiera empieza hoy.
    """
    ocupado: dict = {}
    for it in items:
        fin = it.get("libre_desde")
        if fin is None:
            fin = max(hasta, ahora)
        for tipo, rid in (("empleado", it["idempleado"]), ("maquina", it["matricula"])):
            if rid and fin > ahora:
                ocupado.setdefault((tipo, str(rid)), []).append((ahora, fin))
    return ocupado


def _hueco_para(intervalos: list, desde: datetime, dur: float) -> tuple:
    """El primer momento desde `desde` en que caben `dur` minutos seguidos.

    Busca HUECOS en vez de ponerse a la cola detrás de todo: si un recurso
    está reservado mañana por la mañana, hoy por la tarde sigue libre y hay
    que poder usarlo. Con la ocupación como un único "libre a partir de" eso
    era imposible de expresar.

    El bucle avanza siempre —cada choque devuelve un fin posterior al instante
    probado— así que termina.
    """
    t = _siguiente_hueco(desde)
    while True:
        fin = _sumar_laborables(t, dur)
        choque = max((b for a, b in intervalos if a < fin and t < b), default=None)
        if choque is None:
            return t, fin
        t = _siguiente_hueco(choque)


def _planificar_cola(cola: list[dict], ocupado_hasta: dict, hasta_dt: datetime,
                     ahora: datetime, teoricos, medias) -> list[dict]:
    """Una previsión de la cola pendiente, con un hueco propio por operario.

    Un bono con varios asignados NO espera a que coincidan todos. El ERP los
    lista para decir quién *puede* hacerlo, no que tengan que hacerlo juntos:
    José Luís tiene fichado él solo el 6629/10 en la INYECCION mientras
    comparte otros tres bonos con ETT2, sin empezarlos. Encadenándolos entre
    sí, ETT2 se quedaba con la cola vacía —sus cuatro bonos los comparte con
    José Luís, ocupado hasta el día siguiente en otra máquina— aunque él
    quedara libre a las 10:09 y su máquina estuviera parada todo el día.

    Así que cada asignado recibe su hueco en `huecos`, calculado con su propia
    ocupación y la de la máquina. La máquina se reserva una sola vez —el bono
    se fabrica una vez— con el primer hueco, el del operario que antes queda
    libre. Se conservan todos los asignados y no se infiere una ganancia de
    velocidad por su número.

    Y se buscan HUECOS, no el final de la cola. Con la ocupación como un único
    "libre a partir de", un bono que arrancaba mañana dejaba su máquina
    inservible hoy: ETT4 tiene 1,8 jornadas de cola, así que su bono 6534/30
    caía mañana a las 07:09 y con él reservaba la 004; José Ramón, libre hoy a
    las 11:47 y con sus dos bonos en esa misma máquina, se iba entero a mañana
    por un trabajo que ni siquiera empieza hoy. El orden de prioridad no
    cambia —semáforo, secuencia, orden—: lo que cambia es que una tarea puede
    caer ANTES que otra ya colocada si le cabe en un hueco que aquella dejó.

    La prioridad es semáforo, secuencia manual y orden/bono; el cálculo es
    conservador y no intenta optimizar huecos ni reasignar trabajo del ERP.
    """
    agrupados = {}
    for b in cola:
        clave = (b["idorden"], b["idbono"], b["matricula"])
        agrupados.setdefault(clave, {})[str(b["idempleado"])] = b

    tareas = []
    for asignados in agrupados.values():
        filas = sorted(asignados.values(), key=lambda b: str(b["idempleado"]))
        # Si algún asignado está bloqueado, el bono es condicional y va
        # detrás del trabajo disponible.
        semaforo = max((b["semaforo"] for b in filas), key=_PRIO_SEMAFORO.get)
        secuencias = [b["ordenar"] for b in filas if b["ordenar"] > 0]
        tareas.append((filas, semaforo, min(secuencias) if secuencias else None))
    tareas.sort(key=lambda t: (_PRIO_SEMAFORO[t[1]], t[2] is None,
                              t[2] or 0, t[0][0]["idorden"], t[0][0]["idbono"],
                              t[0][0]["matricula"]))

    ocupado = dict(ocupado_hasta)
    plan = []
    for asignados, semaforo, secuencia in tareas:
        b = asignados[0]
        min_pieza, setup, origen = _estimar(b, teoricos, medias)
        pendientes = max(0.0, b["piezas_a_fabricar"] - b["fabricadas"])
        if pendientes <= 0:
            continue
        sin_tiempo = not min_pieza or min_pieza <= 0
        dur = _MIN_BLOQUE_SIN_TIEMPO if sin_tiempo else setup + pendientes * min_pieza

        # `dur` son minutos-HOMBRE: tanto el escandallo como la media histórica
        # suman las líneas de TODOS los operarios del bono. El eje del Gantt es
        # un reloj, así que una cuadrilla de cuatro ocupa la cuarta parte de
        # tiempo. Sin esto, 6595/70 —60 piezas a 5,18 min/pieza, cuatro
        # asignados— pintaba 331 minutos a cada uno cuando entre los cuatro son
        # 83 de reloj.
        cuadrilla = len(asignados) >= _MIN_CUADRILLA
        dur_reloj = dur / len(asignados) if cuadrilla else dur

        maquina = ("maquina", b["matricula"]) if b["matricula"] else None
        # Foto de la máquina ANTES de colocar este bono: los asignados compiten
        # por ella entre sí, pero el bono se fabrica una vez, así que cada uno
        # se mide contra la misma disponibilidad.
        ocupa_maquina = list(ocupado.get(maquina, ())) if maquina else []
        huecos = {}
        if cuadrilla:
            # Trabajan JUNTOS, así que hace falta un hueco en el que estén
            # libres todos a la vez. Es lo contrario del caso de abajo y por
            # eso convive con él: ahí "asignado" significa "que lo coja quien
            # pueda" y esperar a los demás vaciaba colas enteras.
            intervalos = ocupa_maquina + [
                iv for a in asignados
                for iv in ocupado.get(("empleado", str(a["idempleado"])), ())
            ]
            arranque, remate = _hueco_para(intervalos, ahora, dur_reloj)
            for a in asignados:
                clave = ("empleado", str(a["idempleado"]))
                ocupado.setdefault(clave, []).append((arranque, remate))
                huecos[str(a["idempleado"])] = (arranque, remate)
        else:
            for a in asignados:
                clave = ("empleado", str(a["idempleado"]))
                arranque, remate = _hueco_para(
                    list(ocupado.get(clave, ())) + ocupa_maquina, ahora, dur_reloj)
                ocupado.setdefault(clave, []).append((arranque, remate))
                huecos[str(a["idempleado"])] = (arranque, remate)

        inicio, fin = min(huecos.values())
        if maquina:
            ocupado.setdefault(maquina, []).append((inicio, fin))
        # Las reservas se calculan incluso fuera de la ventana: de lo
        # contrario cambiar de Día a Semana cambiaría el orden de la cola.
        if inicio >= hasta_dt:
            continue
        plan.append({
            "bono": b, "asignados": asignados, "semaforo": semaforo,
            "secuencia": secuencia, "start": inicio, "end": fin,
            "huecos": huecos,
            "pendientes": pendientes, "sin_tiempo": sin_tiempo,
            "min_pieza": min_pieza, "origen": origen, "duracion": dur_reloj,
            # Los minutos-hombre y cuántos lo hacen: sin esto, el tooltip de un
            # bono de cuadrilla dice "83 min" para 60 piezas a 5,18 min/pieza y
            # no hay forma de cuadrar la cuenta.
            "min_hombre": dur, "a_la_vez": len(asignados) if cuadrilla else 1,
        })
    return plan


def _encolar(vista: str, ocupado_hasta: dict, hasta_dt: datetime,
             ahora: datetime, teoricos, medias) -> list[dict]:
    """Proyecta el mismo plan en filas de operarios o de máquinas."""
    plan = _planificar_cola(_leer_cola(), ocupado_hasta, hasta_dt, ahora, teoricos, medias)
    items = []
    for tarea in plan:
        b = tarea["bono"]
        asignados = tarea["asignados"]
        empleados = ", ".join(a["empleado"] for a in asignados)
        filas = [(str(a["idempleado"]), a) for a in asignados] if vista == "empleado" else (
            [(b["matricula"], b)] if b["matricula"] else []
        )
        for rid, asignado in filas:
            # Cada operario arranca cuando queda libre él: un compañero
            # ocupado no le vacía la cola. La máquina va con el primer hueco,
            # que es el que ya trae la tarea.
            inicio, fin = (tarea["huecos"][rid] if vista == "empleado"
                           else (tarea["start"], tarea["end"]))
            if inicio >= hasta_dt:
                continue
            sin_tiempo, min_pieza = tarea["sin_tiempo"], tarea["min_pieza"]
            semaforo = tarea["semaforo"]
            # La media de la máquina DIMENSIONA la barra pero no es un ritmo
            # del que fiarse: una misma máquina hace piezas muy distintas. La
            # 018 da 0,673 min/pieza de media y el artículo que corre ahora en
            # ella, 0,350 — casi el doble. Las barras abiertas ya lo avisaban
            # (ver `_proyectar`); la cola las pintaba en verde como si la
            # estimación fuera buena. Mismo origen, mismo aviso.
            sin_ritmo = sin_tiempo or tarea["origen"] == "media_maquina"
            items.append({
                "id": f"P-{b['idorden']}-{b['idbono']}-{b['matricula']}-{rid}",
                "recurso_id": rid,
                "tipo": "programado",
                "estado": ("sin-estimar" if sin_ritmo else
                           "parada" if semaforo == "bloqueada" else "disponible"),
                "fin_indeterminado": sin_ritmo,
                "semaforo": semaforo,
                "semaforo_asignacion": asignado["semaforo"],
                "en_curso": False, "estimado": True,
                "start": inicio, "end": fin,
                "idorden": b["idorden"], "idbono": b["idbono"],
                "art": b["descrip_salida"], "art_id": b["idarticulo_salida"],
                "area": b["area"],
                "operacion": (b["descrip_maquina"] if vista == "empleado" else empleados),
                "operarios": empleados,
                "piezas": b["piezas_a_fabricar"], "min_real": None,
                "sin_tiempo": sin_tiempo, "orden_manual": tarea["secuencia"],
                "origen_estimado": tarea["origen"],
                "piezas_objetivo": b["piezas_a_fabricar"] or None,
                "piezas_hechas": b["fabricadas"],
                "piezas_pendientes": tarea["pendientes"],
                "min_pieza": round(min_pieza, 3) if min_pieza else None,
                "min_restantes": round(tarea["duracion"]),
                "min_hombre": round(tarea["min_hombre"]),
                "a_la_vez": tarea["a_la_vez"],
                "base_estimacion": "piezas",
            })
    return items


#  Hueco máximo entre el fin del montaje y el inicio de la producción para
#  considerar que son el mismo trabajo. Medido sobre 6 meses: 4.091 de 4.749
#  montajes enlazan con su producción en 2 minutos o menos (86%).
_HUECO_MONTAJE_MIN = 2


def _fundir_montaje(items: list[dict]) -> list[dict]:
    """Funde la barra de montaje con la de producción del mismo bono y operario.

    El ERP graba el montaje como una línea aparte, así que llegan como dos
    barras seguidas. Se pintan como una sola con la parte de preparación
    marcada dentro (`pct_montaje`), que es como se lee de un vistazo cuánto de
    ese bono fue preparar y cuánto fabricar.

    Solo se funden si van pegadas (<= 2 min): el 86% de los casos. Si el
    montaje fue otro día —Elías montó la 001 el jueves y siguió el lunes— son
    trabajos separados de verdad y se quedan como dos barras.
    """
    prod = {}
    for it in items:
        if it["tipo"] in ("real", "trabajado") and not it["es_montaje"]:
            prod.setdefault((it["idorden"], it["idbono"], it["recurso_id"]), []).append(it)

    fundidos, absorbidos = [], set()
    for it in items:
        if not it["es_montaje"] or it["tipo"] not in ("real", "trabajado"):
            continue
        clave = (it["idorden"], it["idbono"], it["recurso_id"])
        # La producción que arranca justo después de este montaje.
        siguiente = min(
            (p for p in prod.get(clave, [])
             if 0 <= (p["start"] - it["end"]).total_seconds() / 60 <= _HUECO_MONTAJE_MIN),
            key=lambda p: p["start"], default=None)
        if siguiente is None:
            continue
        min_montaje = (it["end"] - it["start"]).total_seconds() / 60
        siguiente["start"]       = it["start"]
        siguiente["min_montaje"] = round(min_montaje)
        total = (_minutos_laborables_entre(siguiente["start"], siguiente["end"])
                 if siguiente["en_curso"] else
                 (siguiente["end"] - siguiente["start"]).total_seconds() / 60)
        siguiente["pct_montaje"] = round(100 * min_montaje / total, 1) if total > 0 else 0
        absorbidos.add(id(it))
        fundidos.append(siguiente)

    return [it for it in items if id(it) not in absorbidos]


# ─────────────────────────────────────────────────────────────────────
#  CONTINUACIÓN: lo que queda por fabricar del bono que se está montando
# ─────────────────────────────────────────────────────────────────────
#  Un bono recién arrancado solo tiene fichada la PREPARACIÓN, así que su
#  única barra real acaba con el montaje. Pero `_proyectar` reserva al
#  operario y a la máquina hasta terminar de fabricarlo (`libre_desde`), que
#  es lo correcto: el siguiente bono no puede empezar antes.
#
#  Esa reserva no la pintaba nadie, y era un agujero grande. La cola no la
#  recoge —filtra por `IdEstado = 0` y un bono arrancado ya está en 1, sobre
#  el supuesto de que "ya sale como barra real de su propio fichaje", que es
#  falso mientras lo único fichado sea el montaje— y la barra real tampoco,
#  porque el fichaje de producción todavía no existe. Medido en 6589/20:
#  Elías quedaba ocupado hasta el día 15 (1.200 piezas a 1,63 min/pieza)
#  mientras la pantalla lo enseñaba libre a las 13:16, y sus bonos en cola
#  aparecían a seis días vista sin nada que lo explicara.
#
#  La barra es una proyección, como las de la cola, y va del fin del montaje
#  al fin de la reserva. Se distingue en que este bono YA está arrancado: no
#  tiene semáforo que consultar, lleva estado propio.
# ─────────────────────────────────────────────────────────────────────

def _continuar(items: list[dict], ahora: datetime) -> list[dict]:
    """Las barras de fabricación pendiente de los montajes abiertos.

    Consume `_pendiente`, que `_proyectar` deja en la barra de montaje. Se
    llama SIEMPRE, aunque no haya nada que dibujar, porque además de generar
    las barras es lo que saca esa clave interna del payload.

    Se corre sobre la lista ya fundida: si el montaje enlazó con su barra de
    producción, esa barra tiene su propia proyección y aquí no hay nada que
    añadir."""
    barras = []
    for it in items:
        p = it.pop("_pendiente", None)
        if not p:
            continue
        minutos = p["minutos"]
        # Dimensionada en cero: las piezas ya están hechas y lo que queda es
        # cerrar el fichaje, no fabricar. Distinto de no saber cuánto queda.
        if minutos is not None and minutos <= 0:
            continue
        sin_tiempo = minutos is None
        inicio = max(it["end"], ahora)
        # Sin dimensionar, `_ocupacion_actual` reserva la ventana entera. Se
        # dibuja el bloque nominal de la cola y se avisa de que el fin no se
        # sabe: una barra de días enteros diría una precisión que no hay.
        fin = (_sumar_laborables(inicio, _MIN_BLOQUE_SIN_TIEMPO) if sin_tiempo
               else it.get("libre_desde") or _sumar_laborables(inicio, minutos))
        # Misma regla que en la cola: la media de la máquina dimensiona la
        # barra pero no es un ritmo del que fiarse.
        sin_ritmo = sin_tiempo or p["origen"] == "media_maquina"
        barras.append({
            "id":          f"C-{it['idorden']}-{it['idbono']}-{it['recurso_id']}",
            "recurso_id":  it["recurso_id"],
            "tipo":        "programado",
            "estado":      "sin-estimar" if sin_ritmo else "continuacion",
            "continuacion": True,
            "fin_indeterminado": sin_ritmo,
            "en_curso":    False,
            "estimado":    True,
            "start":       inicio,
            "end":         fin,
            "idorden":     it["idorden"],
            "idbono":      it["idbono"],
            "art":         it["art"],
            "art_id":      it["art_id"],
            "area":        it["area"],
            "operacion":   it["operacion"],
            "operarios":   it["operarios"],
            "piezas":      it["piezas"],
            "min_real":    None,
            "sin_tiempo":  sin_tiempo,
            "origen_estimado":   p["origen"],
            "piezas_objetivo":   p["objetivo"] or None,
            "piezas_hechas":     p["hechas"],
            "piezas_pendientes": max(0.0, p["objetivo"] - p["hechas"]),
            "min_pieza":     round(p["min_pieza"], 3) if p["min_pieza"] else None,
            "min_restantes": round(minutos) if minutos is not None else None,
            "base_estimacion": "piezas",
        })
    return barras


def _proyectar(item: dict, linea: dict, ahora: datetime, teoricos, medias, avance) -> None:
    """Estira la barra abierta hasta su fin estimado, o la marca sin tiempo.

    Lo que queda por delante se calcula **en piezas**, no en minutos:

        (objetivo − declaradas) × min/pieza

    Restar minutos era lo anterior y estaba mal: un bono puede cambiar de
    manos, y entonces la barra de quien lo tiene ahora heredaba el tiempo
    que gastó otro (medido en 6372/30: 1.434 de los 3.400 minutos eran de un
    compañero que lo dejó hace días). En piezas eso no pasa — da igual quién
    hizo las anteriores, lo que falta es lo que falta.

    El problema es que **solo 11 de 565 bonos abiertos declaran piezas**. Sin
    ese dato no hay forma de saber lo avanzado, así que se cae al criterio
    viejo (presupuesto de minutos menos lo gastado) y el item lo dice en
    `base_estimacion` para que no haya que adivinarlo."""
    # Un montaje NO produce piezas (las 4.753 líneas de montaje declaran cero),
    # así que el modelo de piezas no le aplica: se estima con lo que suele
    # tardar montar esa máquina. Sin esto, preparar la 001 se proyectaba con el
    # tiempo de fabricar el bono entero y la barra llegaba hasta las 20:08.
    if linea.get("idoperacion") in _OPERACION_MONTAJE:
        dur = _minutos_montaje(linea)
        item["origen_estimado"] = "media_montaje"
        restante = max(0.0, dur - _minutos_laborables_entre(item["start"], ahora))
        item["min_restantes"] = round(restante)
        if restante > 0:
            item["end"] = item["fin_estimado"] = _sumar_laborables(ahora, restante)
        # Al terminar el montaje aún queda fabricar el bono. Reservar solo
        # hasta el fin de preparación adelantaría el siguiente bono.
        ritmo, _, origen = _estimar(linea, teoricos, medias)
        gasto = avance.get((linea["idorden"], linea["idbono"]))
        objetivo = float(linea["piezas_a_fabricar"] or 0)
        produccion = None
        # Sin `restante > 0`: que la preparación se haya pasado de su media no
        # quita que detrás siga habiendo un bono que fabricar. Lo que no se
        # puede dimensionar es la producción, y eso ya lo dice el `if`.
        if ritmo and objetivo > 0 and gasto:
            produccion = (max(0.0, objetivo - gasto["piezas"]) * ritmo
                          if gasto["piezas"] > 0 else
                          max(0.0, objetivo * ritmo - gasto["min_produccion"]))
            # Minutos-HOMBRE a minutos de reloj: los que están montando juntos
            # son los que van a fabricar juntos. `restante` no se divide, que
            # es tiempo de preparación ya medido por persona.
            produccion /= gasto["montando"]
            item["libre_desde"] = _sumar_laborables(ahora, restante + produccion)
        # Lo que acabamos de reservar tiene que verse: `_continuar` lo convierte
        # en barra propia. La clave se consume ahí y no llega al frontend.
        item["_pendiente"] = {
            "minutos":   produccion,
            "min_pieza": ritmo,
            "origen":    origen,
            "objetivo":  objetivo,
            "hechas":    min(gasto["piezas"], objetivo) if gasto and objetivo else 0.0,
        }
        return

    min_pieza, setup, origen = _estimar(linea, teoricos, medias)
    item["origen_estimado"] = origen

    # `end` se queda en "ahora" porque no hay nada que proyectar, no porque el
    # bono vaya a terminar ahora. Sin avisarlo, el tooltip decía "Fin 09:51" de
    # un bono del que justo se acaba de reconocer que no se sabe cuánto dura.
    if not min_pieza or min_pieza <= 0:
        item["sin_tiempo"] = True
        item["estado"] = "sin-estimar"
        item["fin_indeterminado"] = True
        return

    gasto = avance.get((linea["idorden"], linea["idbono"]))
    if gasto is None:
        item["sin_tiempo"] = True
        item["estado"] = "sin-estimar"
        item["fin_indeterminado"] = True
        return
    objetivo  = float(linea["piezas_a_fabricar"] or 0)
    hechas    = min(gasto["piezas"], objetivo) if objetivo else gasto["piezas"]
    consumido = gasto["min_produccion"]

    item["min_pieza"]       = round(min_pieza, 3)
    item["min_consumidos"]  = round(consumido)
    item["min_montaje_consumidos"] = round(gasto["min_montaje"])
    item["piezas_objetivo"] = objetivo or None
    item["piezas_hechas"]   = hechas
    # La preparación solo cuenta si el bono aún no ha arrancado; si ya hay
    # minutos gastados, esa preparación ya está pagada.
    item["min_estimados"]   = round(objetivo * min_pieza + (setup if gasto["minutos"] == 0 else 0))

    if hechas > 0 and objetivo > 0:
        pendientes = max(0.0, objetivo - hechas)
        restante   = pendientes * min_pieza
        ritmo_real = consumido / hechas
        item["base_estimacion"] = "piezas"
        item["piezas_pendientes"] = pendientes
        item["min_pieza_real"]    = round(ritmo_real, 3)
        item["progreso_piezas"]   = round(hechas / objetivo * 100)
        # Un bono que ya ha hecho todas sus piezas no puede ir "lento": no le
        # queda trabajo. Marcarlo en ámbar era ruido -- no hay nada que corregir
        # en planta, hay que cerrar el fichaje.
        item["excedido"] = pendientes > 0 and ritmo_real > min_pieza * (1 + _TOLERANCIA_RITMO)
    else:
        # Sin piezas declaradas no se puede medir el avance real.
        restante = max(0.0, item["min_estimados"] - consumido)
        item["base_estimacion"] = "minutos"
        item["excedido"] = consumido > item["min_estimados"]

    # ── Dónde se agota el tiempo teórico, sobre ESTA barra ──────────────
    # El presupuesto (`min_estimados`) y lo gastado (`consumido`) son del BONO
    # entero, pero la barra es UNA sesión de fichaje. Así que el punto de corte
    # no es "start + presupuesto": hay que descontar lo que ya se gastó en
    # sesiones anteriores. En 6135/90 el bono lleva 162 min consumidos, 131 de
    # ellos en esta barra, luego antes se gastaron 31 y del presupuesto de 43
    # solo quedaban 12 al empezarla: el corte cae a los 12 minutos, no a los 43.
    #
    # Se manda como INSTANTE y no como porcentaje: el eje del Gantt salta el
    # descanso y las noches, así que un % del ancho caería en el sitio
    # equivocado en cuanto la barra cruce una de esas bandas.
    # No se pinta exceso contra un baremo que ya hemos declarado poco fiable:
    # seria contradictorio que la misma barra dijera "sin datos fiables" y a la
    # vez acusara de 240 minutos de mas. El rojo solo aparece cuando el tiempo
    # de referencia es del escandallo o del historico del propio articulo.
    if (not item.get("sin_tiempo") and item.get("min_estimados")
            and origen != "media_maquina"):
        minutos_barra   = _minutos_laborables_entre(item["start"], ahora)
        consumido_antes = max(0.0, consumido - minutos_barra)
        resto           = item["min_estimados"] - consumido_antes
        item["fin_teorico"] = (item["start"] if resto <= 0
                               else _sumar_laborables(item["start"], resto))
        if consumido > item["min_estimados"]:
            item["min_exceso"] = round(consumido - item["min_estimados"])

    if objetivo > 0 and hechas >= objetivo:
        # Fabricadas todas las piezas y el fichaje sigue abierto. Y mientras
        # siga abierto los minutos consumidos crecen con el reloj, así que el
        # ritmo real empeora solo: sin este caso, el bono se hundía en ámbar
        # cuanto más tardaran en cerrarlo (6583/50: 100% de piezas y +21%).
        item["estado"] = "pendiente-cierre"
    elif origen == "media_maquina":
        # La media de la MÁQUINA mezcla todo lo que pasa por ese puesto, piezas
        # de cualquier tamaño, así que no sirve para juzgar si un bono va lento:
        # en 6135/90 (artículo sin ningún bono cerrado en 18 meses) daba 25
        # piezas en 11 minutos y lo marcaba en riesgo a los 57 reales. El aviso
        # era del dato, no del taller.
        #
        # Se conserva la estimación —la barra y la cola mantienen su anchura—
        # y solo cambia la etiqueta: "sin datos" en vez de "en riesgo".
        item["estado"] = "sin-estimar"
    elif item["excedido"] and not item.get("min_exceso"):
        # Va a peor ritmo del esperado pero AÚN NO ha agotado el presupuesto
        # del bono: no hay un punto del que decir "a partir de aquí te pasaste",
        # así que el aviso tiene que ser de toda la barra. Es un problema
        # distinto del de abajo — "va lento" frente a "ya se pasó".
        item["estado"] = "riesgo"
    # Si hay `min_exceso`, la barra se queda en su color normal y el aviso lo
    # da el tramo rojo, que además dice desde cuándo y cuánto. Pintarla ADEMÁS
    # de ámbar era decir lo mismo dos veces y, peor, teñía de alarma la parte
    # que sí fue dentro de presupuesto: la barra iba ámbar → rojo → ámbar y el
    # mismo ámbar significaba "esto iba bien" a la izquierda y "esto aún no ha
    # pasado" a la derecha.

    # Cuándo acaba esto no se sabe, y hay que decirlo en vez de dar una hora:
    #   · "sin datos fiables" ya declara que el ritmo no vale, así que una hora
    #     de fin al minuto se contradice con su propia etiqueta;
    #   · agotado el presupuesto, lo que queda por delante es justo lo que el
    #     modelo no supo prever, y el fin cae en "ahora", que se lee como
    #     "termina ya" cuando es lo contrario: lleva rato pasado de tiempo.
    # `pendiente-cierre` queda fuera a propósito: ahí las piezas están hechas y
    # el trabajo SÍ ha terminado; lo único que falta es cerrar el fichaje.
    if item["estado"] == "sin-estimar" or (item.get("min_exceso")
                                           and item["estado"] != "pendiente-cierre"):
        item["fin_indeterminado"] = True

    # Los minutos son minutos-HOMBRE. Para llevarlos al eje de tiempo se
    # reparten entre los operarios que tienen el bono abierto ahora mismo;
    # en la práctica casi siempre es uno (Ordenes_Bonos.Operarios = 1).
    restante_reloj = restante / gasto["operarios"]
    item["min_restantes"] = round(restante_reloj)
    if restante_reloj <= 0:
        # Ya no queda trabajo que estimar: es el bono con todas sus piezas
        # hechas y el fichaje sin cerrar. Eso es SABER que el recurso está
        # libre, no ignorar cuándo lo estará, así que se suelta ya. Reservarlo
        # la ventana entera dejaba sin cola al operario y a su máquina por un
        # fichaje que nadie cerró — justo lo contrario del diagnóstico.
        item["libre_desde"] = ahora
        return

    # Conserva la ocupación hasta terminar, saltando noches y fines de semana.
    # El frontend recorta la barra a su ventana de horas laborables.
    fin_estimado = _sumar_laborables(ahora, restante_reloj)
    item["end"]          = fin_estimado
    item["fin_estimado"] = fin_estimado
    item["libre_desde"]  = fin_estimado

    # `progreso` es el relleno visual de la barra: qué parte de ella ya ha
    # transcurrido, para que lo sólido acabe justo en la línea de ahora.
    # El avance en piezas va aparte, en `progreso_piezas`.
    total = _minutos_laborables_entre(item["start"], fin_estimado)
    if total > 0:
        transcurrido = _minutos_laborables_entre(item["start"], ahora)
        item["progreso"] = round(max(0.0, min(1.0, transcurrido / total)) * 100)


# ─────────────────────────────────────────────────────────────────────
#  REFRESCO
# ─────────────────────────────────────────────────────────────────────
#  Ya no hay ETL que lanzar: los datos vienen del ERP en vivo, así que cada
#  carga de /items ya trae lo último. Se mantienen las dos rutas porque el
#  botón "Actualizar" del frontend espera el protocolo de Prefect
#  (lanzar → sondear estado → recargar); se responde COMPLETED de inmediato
#  y el frontend recarga, que es exactamente lo que hace falta.
# ─────────────────────────────────────────────────────────────────────

@router.post("/refrescar")
def refrescar():
    return {"flow_run_id": "erp-en-vivo", "estado": "COMPLETED"}


@router.get("/refrescar/{flow_run_id}")
def refrescar_estado(flow_run_id: str):
    return {"flow_run_id": flow_run_id, "estado": "COMPLETED"}
