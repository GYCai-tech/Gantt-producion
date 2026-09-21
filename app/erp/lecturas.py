"""Las lecturas del ERP: una función por pregunta que la app le hace.

Cada función devuelve estructuras de Python ya limpias —nunca filas crudas de
SQLAlchemy— para que las capas de arriba no tengan que saber cómo se llama una
columna en el ERP. Lo que NO hay aquí es ningún cálculo: la estimación, la
jornada y la cola proyectada viven en `app/calculos/`.

QUÉ PASA SI EL ERP FALLA — el reparto es intencionado y distinto en cada una:

  · `leer_lineas`, `leer_abiertas`, `leer_cola`, `leer_censo_*`,
    `leer_areas_empleado`  →  propagan `ErpNoDisponible`. Sin estas filas no
    hay pantalla que pintar, así que la capa HTTP lo traduce a 503.
  · `leer_avance`  →  propaga `ErpNoDisponible` con su propio mensaje. Sin el
    avance, lo que queda por hacer sería mentira: también 503.
  · `leer_paradas` y `leer_ausencias`  →  devuelven `{}`. Una anotación o una
    ausencia que no se puede leer es una marca de menos, no una pantalla rota.
  · el escandallo, las medias y el semáforo degradan reutilizando su caché
    caducada; están en `app/erp/cache.py`.
"""
from datetime import date, datetime, timedelta

from sqlalchemy import bindparam, text

#  OJO COORDINADOR: la jornada la define `app/calculos/calendario.py` (Agente
#  2). Se importa en vez de repetir el 7 y el 15 aquí, porque `cuando_falta`
#  tiene que decidir si una ausencia parcial tapa la jornada entera y eso
#  depende de dónde empieza y acaba. Es la ÚNICA dependencia de `app/erp/`
#  hacia `app/calculos/`.
from app.calculos.calendario import JORNADA_FIN, JORNADA_INICIO
import app.erp.cache as cache
import app.erp.consultas as consultas
from app.erp.cliente import ErpNoDisponible, consultar, ejecutar


def nombre_completo(r) -> str:
    """"Nombre Apellidos" del empleado, o "#id" si la ficha no trae nombre."""
    partes = [p.strip() for p in (r["nombre"], r["apellidos"]) if p and p.strip()]
    return " ".join(partes) or f"#{r['idempleado']}"


# ─────────────────────────────────────────────────────────────────────
#  LÍNEAS DE BONO  (las barras del Gantt)
# ─────────────────────────────────────────────────────────────────────

def normalizar_lineas(filas) -> list[dict]:
    """Deduplica por (orden, bono, línea) y resuelve inicio/fin de la barra.

    El JOIN con `Ordenes_Bonos_Salidas` repite fila cuando un bono declara más
    de un artículo de salida, y en el Gantt saldrían dos barras idénticas
    superpuestas. La identidad real de una barra es (orden, bono, línea).
    """
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
            "empleado":          nombre_completo(r),
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


def leer_lineas(desde: date, hasta: date) -> list[dict]:
    """Las líneas de bono del rango, deduplicadas y con inicio/fin resueltos."""
    filas = consultar(consultas.consulta_lineas(consultas.FILTRO_RANGO),
                      {"desde": desde, "hasta": hasta})
    return normalizar_lineas(filas)


def leer_abiertas(ahora: datetime) -> list[dict]:
    """Ocupación actual, independiente del día que se está consultando."""
    return normalizar_lineas(consultar(consultas.consulta_lineas(consultas.FILTRO_ABIERTAS), {
        "ahora": ahora,
        "limite": ahora - timedelta(hours=consultas.HORAS_LINEA_VIVA),
    }))


def leer_paradas() -> dict:
    """{(orden, bono, linea): {motivo, minutos}} de las paradas anotadas.

    Degrada a vacío como el semáforo o las ausencias: que no se pueda leer una
    anotación es una marca de menos, no una pantalla rota."""
    try:
        filas = consultar(consultas.SQL_PARADAS)
    except ErpNoDisponible:
        print("[items] paradas no disponibles: ¿falta Ordenes_Bonos_Lineas_Inc?")
        return {}
    return {
        (r["idorden"], r["idbono"], r["idlinea"]): {
            #  El catálogo puede no tener el código; mejor el código suelto que
            #  una marca sin texto.
            "motivo":  (r["motivo"] or r["tipo"] or "").strip() or "Parada",
            "minutos": round(float(r["minutos"] or 0)) or None,
            "observaciones": (r["observaciones"] or "").strip() or None,
        }
        for r in filas
    }


# ─────────────────────────────────────────────────────────────────────
#  CENSO  (las filas del Gantt)
# ─────────────────────────────────────────────────────────────────────

def leer_censo_maquinas() -> list[dict]:
    """Toda máquina que alguna vez tuvo una línea de bono: {id, nombre, area}.

    Filas en crudo: quien las pide decide cómo se presentan."""
    return consultar(consultas.SQL_CENSO_MAQUINAS)


def leer_censo_empleados() -> list[dict]:
    """Los operarios de PRODUCCIÓN: {idempleado, nombre, apellidos}."""
    return consultar(consultas.SQL_CENSO_EMPLEADOS,
                     {"departamento": consultas.DEPARTAMENTO_PRODUCCION})


def leer_areas_empleado() -> list[dict]:
    """{idempleado, area} de la actividad reciente, una fila por par."""
    return consultar(consultas.SQL_AREAS_EMPLEADO,
                     {"dias": -consultas.AREAS_RECIENTES_DIAS})


# ─────────────────────────────────────────────────────────────────────
#  AVANCE POR BONO
# ─────────────────────────────────────────────────────────────────────

def leer_avance(lineas: list[dict], ahora: datetime) -> dict:
    """Lo que lleva cada bono: minutos gastados, piezas declaradas y cuántos
    operarios lo tienen abierto ahora.

    Si el ERP falla, `ErpNoDisponible`: sin esto lo que queda por hacer sería
    una cifra inventada, así que la capa HTTP debe responder 503 y no una
    pantalla a medias.

    (El porqué de la consulta —líneas fantasma, minutos-hombre, montadores—
    está en la cabecera de `consultas.SQL_AVANCE`.)"""
    ordenes = sorted({l["idorden"] for l in lineas})
    if not ordenes:
        return {}

    consulta = text(consultas.SQL_AVANCE).bindparams(
        bindparam("ordenes", expanding=True))

    try:
        filas = ejecutar(consulta, {
            "ordenes": ordenes, "ahora": ahora,
            "limite": ahora - timedelta(hours=consultas.HORAS_LINEA_VIVA),
        })
    except ErpNoDisponible as e:
        raise ErpNoDisponible("No se pudo consultar el avance del ERP") from e

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


# ─────────────────────────────────────────────────────────────────────
#  COLA
# ─────────────────────────────────────────────────────────────────────

def leer_cola() -> list[dict]:
    """Los bonos asignados que están por hacer, deduplicados por (bono, operario).

    Son los que nadie ha empezado y los que están a medias sin nadie fichando
    (ver la cabecera de `consultas.SQL_COLA`). La vista repite fila cuando un
    bono declara más de un artículo de salida, igual que la consulta de líneas.

    El color del semáforo viene de la caché (`cache.cargar_semaforo`), que
    degrada sola: si no se puede leer, la cola sale igual con el último color
    conocido."""
    filas = consultar(consultas.SQL_COLA,
                      {"dias_arrancado": -consultas.DIAS_BONO_ARRANCADO,
                       "horas_viva": -consultas.HORAS_LINEA_VIVA})
    semaforo = cache.cargar_semaforo()
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
            "empleado":          nombre_completo(r),
            "ordenar":           int(r["ordenar"] or 0),
            "matricula":         (r["matricula"] or "").strip(),
            "descrip_maquina":   r["descrip_maquina"],
            "area":              (r["area"] or "").strip() or None,
            "descrip_salida":    r["descrip_salida"],
            "idarticulo_salida": r["idarticulo_salida"],
            "idtrabajo":         r["idtrabajo"],
            "piezas_a_fabricar": float(r["objetivo"] or 0),
            "fabricadas":        float(r["fabricadas"] or 0),
            # Trabajo a medias: el bono ya está arrancado en el ERP.
            "arrancado":         r["idestado"] == 1,
            "ultimo_fichaje":    r["ultimo_fichaje"],
            # Si ya se fichó la preparación, la máquina está montada y ese
            # tiempo no se vuelve a gastar. Son 23 de los 25 bonos arrancados,
            # a 31 minutos de montaje por defecto cada uno.
            "montado":           bool(r["montajes"]),
        })
    return cola


# ─────────────────────────────────────────────────────────────────────
#  AUSENCIAS
# ─────────────────────────────────────────────────────────────────────
#  Cómo se LEE una ausencia parcial en planta. Decir "07:00–10:00" describe el
#  hueco; lo que hace falta saber es que ese día el operario entra a las 10:00.
#
#  La frase depende de dónde caiga la franja respecto a la jornada. Medido sobre
#  las 255 parciales aprobadas, y las cuatro formas son reales:
#
#      franja intermedia ....... 109    se marcha antes ..... 97
#      entra más tarde .........  46    jornada entera ......  3
#
#  Esas 3 últimas vienen marcadas como parciales pero cubren de 07:00 a 15:00:
#  no son parciales de verdad, así que se devuelven como ausencia de día entero
#  y la fila se raya como cualquier otra. Ninguna de las 255 viene sin horas,
#  pero se contempla porque son columnas nullable.
APERTURA = f"{JORNADA_INICIO:02d}:00"
CIERRE   = f"{JORNADA_FIN:02d}:00"


def cuando_falta(hora_ini: str | None, hora_fin: str | None) -> str | None:
    """Cómo afecta la franja a la jornada, o None si falta el día entero.

    Las horas llegan como texto "HH:MM" con cero delante, así que se comparan
    como cadenas sin convertirlas."""
    if not hora_ini or not hora_fin:
        return None
    if hora_ini <= APERTURA and hora_fin >= CIERRE:
        return None
    if hora_ini <= APERTURA:
        return f"entra a las {hora_fin}"
    if hora_fin >= CIERRE:
        return f"se marcha a las {hora_ini}"
    return f"fuera de {hora_ini} a {hora_fin}"


def leer_ausencias(dia: date) -> dict[str, dict]:
    """{idempleado: {motivo, desde, hasta}} de quien no está en planta ese día.

    Degrada a vacío igual que el semáforo o el escandallo: una ausencia que no
    se puede leer es información de menos, no una pantalla rota. Mientras no
    haya permisos en PORTALHR devuelve {} y el Gantt se pinta exactamente como
    hoy, sin marcas.
    """
    if not consultas.SQL_AUSENCIAS:
        return {}
    try:
        filas = consultar(consultas.SQL_AUSENCIAS, {"dia": dia})
    except ErpNoDisponible:
        print("[avisos] ausencias no disponibles: ¿faltan permisos en PORTALHR?")
        return {}
    ausencias = {}
    for r in filas:
        hora_ini = (r["hora_ini"] or "").strip() or None
        hora_fin = (r["hora_fin"] or "").strip() or None
        # Una ausencia PARCIAL no es "no vino": quien falta de 07:00 a 10:00
        # trabaja el resto de la jornada. Marcarle el día entero sería
        # falso. `cuando` es la frase ya resuelta —"entra a las 10:00"—, y que
        # salga None es lo que decide que la ausencia es de día completo: así el
        # front no tiene que volver a razonar sobre horarios.
        cuando = cuando_falta(hora_ini, hora_fin) if r["parcial"] else None
        ausencias[str(r["idempleado"])] = {
            #  Ya viene de un literal de la consulta —nunca de texto que haya
            #  escrito una persona—, así que el respaldo es solo por si algún
            #  día la consulta cambia y deja de garantizarlo.
            "motivo":  (r["motivo"] or "").strip() or "Ausencia",
            "desde":   r["desde"],
            "hasta":   r["hasta"],
            "parcial": bool(cuando),
            "cuando":  cuando,
        }
    return ausencias
