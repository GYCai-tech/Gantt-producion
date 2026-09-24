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

from app.calculos.calendario import (DESCANSO_FIN, DESCANSO_INICIO, JORNADA_FIN,
                                     JORNADA_INICIO, minutos_laborables_entre,
                                     sumar_laborables)
from app.erp.cliente import ErpNoDisponible
from app.services import produccion
from app.services.produccion import alfabetico

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


def _jornadas_de(item: dict) -> list[date]:
    """Los días laborables en los que esta barra ocupa algo de jornada.

    Es la barra ENTERA, no la ventana que se está pidiendo: lo que necesita la
    hoja del día es poder decir "día 2 de 3" de un bono que empezó ayer. Se
    cuenta por jornada con minutos, no por fecha de calendario, para que una
    barra que acaba justo a las 07:00 no aparezca como un día más que no toca.
    """
    dias, d = [], item["start"].date()
    while d <= item["end"].date():
        if d.weekday() < 5:
            apertura = datetime.combine(d, datetime.min.time()).replace(hour=JORNADA_INICIO)
            cierre = datetime.combine(d, datetime.min.time()).replace(hour=JORNADA_FIN)
            tramo = _tramo_del_dia(item, (apertura, cierre))
            if tramo and minutos_laborables_entre(*tramo) > 0:
                dias.append(d)
        d += timedelta(days=1)
    return dias


def _piezas_del_dia(item: dict, minutos_dia: float, ahora: datetime):
    """Cuántas de las piezas pendientes caen en este trozo de la barra.

    Es un reparto proporcional al tiempo: si al bono le quedan 1.200 piezas y
    este día se lleva un tercio de lo que le queda de barra, le tocan unas 400.
    Es una aproximación —el ritmo no es constante— y la hoja lo dice con "≈".
    Sin piezas pendientes conocidas no se inventa nada.
    """
    pendientes = item.get("piezas_pendientes")
    if pendientes is None:
        return None
    resto = minutos_laborables_entre(max(item["start"], ahora), item["end"])
    if resto <= 0:
        return round(pendientes)
    return round(pendientes * min(1.0, minutos_dia / resto))


def _sin_descanso(ini: datetime, fin: datetime) -> float:
    """Minutos laborables de [ini, fin) quitando el descanso de 11:00 a 11:15.

    Es la medida de la hoja del día, que cuenta lo que TRABAJA el operario:
    una jornada completa son 7h45, no 8. La proyección no lo hace así a
    propósito (ver `calendario.DESCANSO_INICIO`)."""
    total = minutos_laborables_entre(ini, fin)
    d = ini.date()
    while d <= fin.date():
        if d.weekday() < 5:
            desde = max(ini, datetime.combine(d, DESCANSO_INICIO))
            hasta = min(fin, datetime.combine(d, DESCANSO_FIN))
            if hasta > desde:
                total -= (hasta - desde).total_seconds() / 60
        d += timedelta(days=1)
    return total


def _union_sin_descanso(ventanas: list) -> float:
    """El tiempo efectivo de una persona en un día: la unión de sus ventanas
    —lo simultáneo cuenta una vez, igual que en la rejilla— sin el descanso."""
    total, actual = 0.0, None
    for ini, fin in sorted(ventanas):
        if actual and ini <= actual[1]:
            actual = (actual[0], max(actual[1], fin))
            continue
        if actual:
            total += _sin_descanso(*actual)
        actual = (ini, fin)
    return total + (_sin_descanso(*actual) if actual else 0.0)


def _ventana_operario(item: dict, tramo: tuple, automaticas: set):
    """El trozo de este tramo en el que el operario trabaja de verdad, o None.

    En una máquina de `maquinas-auto.txt` al operario solo le cuenta el
    MONTAJE: la producción la hace la máquina sola. Lo decidió producción para
    la hoja del día —es el tiempo efectivo de la persona— y solo para ella; el
    plan y la rejilla de carga siguen igual.

      · barra real de montaje  → cuenta entera, está montando;
      · barra real de producción, o la fabricación que sigue a un montaje en
        curso → no cuenta nada, la máquina va sola;
      · bono programado → cuenta su preparación, que va al principio de la
        barra. Si la máquina ya estaba montada, esa preparación es 0.

    En cualquier otra máquina la persona está ocupada todo el tramo.
    """
    if (item.get("matricula") or "").strip() not in automaticas:
        return tramo
    if item["tipo"] == "real":
        return tramo if item.get("es_montaje") else None
    preparacion = item.get("min_preparacion") or 0
    if preparacion <= 0:
        return None
    fin_montaje = sumar_laborables(item["start"], preparacion)
    ini, fin = max(tramo[0], item["start"]), min(tramo[1], fin_montaje)
    #  Menos de un minuto no es un montaje, es el sobrante de uno que empezó a
    #  las 14:59 del día anterior: en la hoja salía como "07:00–07:00".
    return (ini, fin) if (fin - ini).total_seconds() >= 60 else None


def _falta_entero(ausencia):
    """El motivo si la ausencia tapa el día entero; None si no falta o es parcial."""
    if not ausencia or ausencia.get("parcial"):
        return None
    return ausencia.get("motivo") or "Ausencia"


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
            total += minutos_laborables_entre(arranque, fin)
        tope = fin if tope is None or fin > tope else tope
    return total


@router.get("/api/plan")
def get_plan(dias: int = Query(5, ge=1, le=_MAX_DIAS),
             vista: str = Query("empleado", pattern="^(maquina|empleado)$"),
             ausencias: bool = False):
    """`ausencias=true` lo pide la hoja del día: quien falta el día entero no
    se cuenta. La rejilla de carga no lo pide y no paga esas consultas."""
    hoy = date.today()
    ahora = datetime.now()
    fechas = _dias_laborables(hoy, dias)
    faltan = ({d: produccion.ausencias(d) for d in fechas}
              if ausencias and vista == "empleado" else {})
    # Lo que queda de cada jornada. Para hoy encoge con el reloj; para el resto
    # es la jornada entera.
    ventanas = {d: _ventana_del_dia(d, ahora) for d in fechas}
    disponible = {d: minutos_laborables_entre(*v) for d, v in ventanas.items()}

    # Una sola lectura para toda la ventana: pedir día a día recalcularía la
    # cola desde cero en cada uno y daría planes distintos, porque la
    # ocupación de hoy es la que empuja lo de mañana.
    items = produccion.calcular_items(
        vista=vista,
        desde=datetime.combine(fechas[0], datetime.min.time()),
        hasta=datetime.combine(fechas[-1] + timedelta(days=1), datetime.min.time()),
    )

    automaticas = produccion.maquinas_automaticas()
    carga = defaultdict(lambda: defaultdict(lambda: {"tramos": [], "bonos": [], "operario": []}))
    for it in items:
        if it["tipo"] not in _TIPOS:
            continue
        jornadas = _jornadas_de(it)
        for dia in fechas:
            tramo = _tramo_del_dia(it, ventanas[dia])
            if tramo is None:
                continue
            minutos = minutos_laborables_entre(*tramo)
            if minutos <= 0:
                continue
            celda = carga[it["recurso_id"]][dia]
            celda["tramos"].append(tramo)
            op = _ventana_operario(it, tramo, automaticas)
            if op:
                celda["operario"].append(op)
            celda["bonos"].append({
                "idorden": it["idorden"], "idbono": it["idbono"],
                "art_id": it.get("art_id"), "art": it.get("art"),
                "maquina": it.get("operacion"),
                "estado": it["estado"], "tipo": it["tipo"],
                "min": round(minutos),
                "fin_indeterminado": bool(it.get("fin_indeterminado")),
                # Lo que necesita la hoja del día, que se imprime la víspera y
                # se cuelga para toda la planta. La rejilla de carga no lo usa.
                #  · a qué hora empieza y acaba ESTE trozo, dentro de este día;
                #  · si el bono viene de un día anterior o sigue al siguiente,
                #    y qué día de cuántos es: una orden de tres días tiene que
                #    leerse como tal, no como tres trabajos sueltos;
                #  · las piezas: las pendientes del bono y el reparto
                #    aproximado de este día.
                "inicio": tramo[0], "fin": tramo[1],
                "viene": dia in jornadas and jornadas.index(dia) > 0,
                "sigue": dia in jornadas and jornadas.index(dia) < len(jornadas) - 1,
                "dia_n": jornadas.index(dia) + 1 if dia in jornadas else 1,
                "dias_n": max(1, len(jornadas)),
                "piezas_objetivo": it.get("piezas_objetivo") or it.get("piezas"),
                "piezas_pendientes": (round(it["piezas_pendientes"])
                                      if it.get("piezas_pendientes") is not None else None),
                "piezas_dia": _piezas_del_dia(it, minutos, ahora),
                #  El tiempo EFECTIVO del operario, para la hoja del día: en
                #  las máquinas automáticas solo el montaje, y en todas sin el
                #  descanso de 11:00 a 11:15.
                "automatica": (it.get("matricula") or "").strip() in automaticas,
                "op_inicio": op[0] if op else None,
                "op_fin": op[1] if op else None,
                "min_operario": round(_sin_descanso(*op)) if op else 0,
            })

    personas = []
    for g in produccion.censo(vista):
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
                # Para la hoja del día: lo que trabaja de verdad la persona y
                # lo que tiene de jornada, las dos sin el descanso (7h45).
                "min_operario": round(_union_sin_descanso(c["operario"])) if c else 0,
                "disponible_operario": round(_sin_descanso(*ventanas[dia])),
                # Quien falta el día ENTERO (baja, vacaciones...). Una ausencia
                # parcial no cuenta aquí: el resto de la jornada sí trabaja.
                "ausencia": _falta_entero(faltan.get(dia, {}).get(str(g["id"]))),
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
    # cuánta gente esta libre. El criterio no cambia; el desempate por nombre
    # usa `alfabetico` para que "marcos" no caiga siempre el último ni "ETT1"
    # se cuele delante de "Elías" (ver el porqué en api.py).
    personas.sort(key=lambda p: (-p["dias_con_trabajo"], -p["total_min"],
                                 alfabetico(p["nombre"])))

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
