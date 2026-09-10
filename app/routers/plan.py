"""Quién tiene trabajo hoy, mañana y los días siguientes.

El Gantt responde "qué está pasando ahora". Esta vista responde otra cosa:
**quién está cargado y quién se queda sin nada**, que es lo que hace falta para
repartir. Son las mismas barras de `/api/items` —misma cola, misma estimación,
mismo encadenado— agregadas por persona y día en vez de dibujadas en un eje.

Dos cosas que la vista enseña y el Gantt no:

  · **Los operarios sin trabajo.** Salen las 29 personas del censo, no solo
    las que tienen barras. Un hueco es información: hoy trabajan 17 y mañana
    quedan 8, y eso no se ve en un Gantt filtrado por "con actividad".

  · **La carga como porcentaje de la jornada.** 480 minutos es el día; saber
    que alguien tiene 320 proyectados dice más que ver tres barras sueltas.

Y una advertencia que la propia página repite: esto es una PROYECCIÓN. Medido
sobre los bonos ya cerrados, el error típico de una estimación es del 43% y el
46% de las barras se apoyan en la media de la máquina, que no sirve como ritmo.
El primer día es razonable porque casi todo es trabajo ya empezado; a partir
del tercero es una conjetura encadenada sobre conjeturas.
"""
from collections import defaultdict
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Query

from app.routers.api import (JORNADA_FIN, JORNADA_INICIO, _minutos_laborables_entre,
                             get_grupos, get_items)

router = APIRouter()

#  Minutos de una jornada completa (07:00-15:00). El denominador de la carga.
JORNADA_MIN = (JORNADA_FIN - JORNADA_INICIO) * 60

#  Tope de días. Más allá la proyección no dice nada: la cola de hoy no da para
#  llenar dos semanas y lo que se pintaría sería el ruido de la estimación.
_MAX_DIAS = 15

_DIA = ("Lun", "Mar", "Mié", "Jue", "Vie", "Sáb", "Dom")

#  Estados que cuentan como trabajo de la persona. `trabajado` y `parcial` son
#  pasado —ya no ocupan a nadie— y `completado` igual.
_TIPOS = ("real", "programado")

#  Minutos de solape a partir de los cuales se considera que hay dos cosas en
#  marcha a la vez. Por debajo es ruido: dos barras encadenadas que se tocan
#  dejan uno o dos minutos de diferencia entre la suma y la union solo por el
#  redondeo, y marcarlas llenaria la rejilla de avisos que no dicen nada.
_SOLAPE_MIN = 5


def _dias_laborables(desde: date, n: int) -> list[date]:
    dias, d = [], desde
    while len(dias) < n:
        if d.weekday() < 5:
            dias.append(d)
        d += timedelta(days=1)
    return dias


def _ventana_del_dia(dia: date, ahora: datetime) -> tuple:
    """De cuándo a cuándo se mide ese día.

    HOY empieza en `ahora`, no a las 07:00. Si no, la carga se desmorona sola
    según avanza la jornada: lo ya hecho deja de contar en el numerador —es
    pasado, no ocupa a nadie— mientras el denominador sigue siendo la jornada
    entera, y a las 13:00 un operario con la tarde llena aparecía al 25%. La
    pregunta útil no es "qué parte del día tiene ocupada" sino "qué parte de lo
    que le QUEDA de día".
    """
    apertura = datetime.combine(dia, datetime.min.time()).replace(hour=JORNADA_INICIO)
    cierre   = datetime.combine(dia, datetime.min.time()).replace(hour=JORNADA_FIN)
    return (max(apertura, min(ahora, cierre)) if dia == ahora.date() else apertura), cierre


def _tramo_del_dia(item: dict, ventana: tuple):
    """El trozo de esta barra que cae DENTRO de esa ventana, o None.

    Una barra puede cruzar varios días —de hoy 10:09 a mañana 07:54— y
    recortarla es justo lo que convierte un Gantt en una carga por jornada.
    """
    desde, hasta = ventana
    ini, fin = max(item["start"], desde), min(item["end"], hasta)
    return (ini, fin) if fin > ini else None


def _minutos_union(tramos: list) -> float:
    """Los minutos que el recurso está ocupado, contando UNA vez lo simultáneo.

    Sumar los tramos daba cargas imposibles: ETT4 tenía abiertos a la vez el
    6479/10 en la INYECTORA DEU 5000 y el 6479/40 en Manual INYECCION —carga la
    máquina, que trabaja sola, y mientras hace el manual de la misma orden— y
    con los dos ocupando lo que quedaba de jornada salía al 200%. Una persona
    no está el 200% ocupada: está ocupada, y hace dos cosas.

    Se mide con el mismo calendario que la proyección, así que ni la noche ni
    el fin de semana suman; los tramos ya vienen recortados a un solo día.
    """
    total, tope = 0.0, None
    for ini, fin in sorted(tramos):
        arranque = ini if tope is None or ini > tope else tope
        if fin > arranque:
            total += _minutos_laborables_entre(arranque, fin)
        tope = fin if tope is None or fin > tope else tope
    return total


@router.get("/api/plan")
def get_plan(dias: int = Query(5, ge=1, le=_MAX_DIAS),
             vista: str = Query("empleado", pattern="^(maquina|empleado)$")):
    hoy = date.today()
    ahora = datetime.now()
    fechas = _dias_laborables(hoy, dias)
    # Lo que queda de cada jornada. Para hoy encoge con el reloj; para el resto
    # es la jornada entera.
    ventanas = {d: _ventana_del_dia(d, ahora) for d in fechas}
    disponible = {d: _minutos_laborables_entre(*v) for d, v in ventanas.items()}

    # Una sola lectura para toda la ventana: pedir día a día recalcularía la
    # cola desde cero en cada uno y daría planes distintos, porque la
    # ocupación de hoy es la que empuja lo de mañana.
    items = get_items(
        vista=vista,
        desde=datetime.combine(fechas[0], datetime.min.time()),
        hasta=datetime.combine(fechas[-1] + timedelta(days=1), datetime.min.time()),
    )

    carga = defaultdict(lambda: defaultdict(lambda: {"tramos": [], "bonos": []}))
    for it in items:
        if it["tipo"] not in _TIPOS:
            continue
        for dia in fechas:
            tramo = _tramo_del_dia(it, ventanas[dia])
            if tramo is None:
                continue
            minutos = _minutos_laborables_entre(*tramo)
            if minutos <= 0:
                continue
            celda = carga[it["recurso_id"]][dia]
            celda["tramos"].append(tramo)
            celda["bonos"].append({
                "idorden": it["idorden"], "idbono": it["idbono"],
                "art_id": it.get("art_id"), "art": it.get("art"),
                "maquina": it.get("operacion"),
                "estado": it["estado"], "tipo": it["tipo"],
                "min": round(minutos),
                "fin_indeterminado": bool(it.get("fin_indeterminado")),
            })

    personas = []
    for g in get_grupos(vista=vista):
        celdas = []
        for dia in fechas:
            c = carga.get(g["id"], {}).get(dia)
            minutos = round(_minutos_union(c["tramos"])) if c else 0
            queda = disponible[dia]
            # Lo que sumarían los bonos por separado. La diferencia con la
            # unión es tiempo que se hace a la vez, y merece decirse: son dos
            # trabajos en marcha, no el doble de trabajo.
            suma = round(sum(b["min"] for b in c["bonos"])) if c else 0
            celdas.append({
                "fecha": dia,
                "min": minutos,
                "min_bonos": suma,
                "simultaneo": suma - minutos >= _SOLAPE_MIN,
                "disponible": round(queda),
                # Aun con la unión puede pasar del 100%: un dia entero de cola
                # encadenada no cabe en lo que queda de jornada, y eso es una
                # señal de sobrecarga, no un error que haya que recortar.
                "pct": round(100 * minutos / queda) if queda > 0 else 0,
                "bonos": sorted(c["bonos"], key=lambda b: -b["min"]) if c else [],
            })
        # En la vista de máquinas el grupo trae un `area` suelto en vez de la
        # lista que trae el operario.
        areas = g.get("areas") or ([g["area"]] if g.get("area") else [])
        personas.append({
            "id": g["id"], "nombre": g["nombre"], "areas": areas,
            "dias": celdas,
            "total_min": sum(c["min"] for c in celdas),
            "dias_con_trabajo": sum(1 for c in celdas if c["min"] > 0),
        })

    # Los cargados primero y, dentro de ellos, por carga total. Quien no tiene
    # nada se queda al final agrupado, que es donde se lee de un vistazo
    # cuánta gente esta libre.
    personas.sort(key=lambda p: (-p["dias_con_trabajo"], -p["total_min"], p["nombre"]))

    resumen = [{
        "fecha": dia,
        "etiqueta": f"{_DIA[dia.weekday()]} {dia.day}",
        "hoy": dia == hoy,
        "disponible": round(disponible[dia]),
        "personas": sum(1 for p in personas if p["dias"][i]["min"] > 0),
        "min": sum(p["dias"][i]["min"] for p in personas),
    } for i, dia in enumerate(fechas)]

    return {
        "generado": ahora,
        "vista": vista,
        "jornada_min": JORNADA_MIN,
        "plantilla": len(personas),
        "dias": resumen,
        "personas": personas,
    }
