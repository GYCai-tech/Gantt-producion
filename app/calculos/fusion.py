"""Fusión de barras: pegar los trozos de un mismo trabajo antes de pintarlo,
y sacar la barra de continuación que deja un montaje abierto.

Opera sobre las listas de items ya construidas. `fundir_troceados` y
`fundir_montaje` MUTAN los items que absorben —igual que hacían en el router—,
así que el orden de llamada importa: troceados primero, montaje después.
"""
from datetime import datetime

from app.calculos.calendario import minutos_laborables_entre, sumar_laborables
#  Mismo bloque nominal que usa la cola para un bono sin dimensionar: es la
#  misma regla, así que se importa en vez de duplicar el número.
from app.calculos.cola import MIN_BLOQUE_SIN_TIEMPO  # misma regla, un solo sitio

#  Un mismo operario ficha a veces el mismo bono en varios trozos seguidos: en
#  6243/70 hay 30 minutos de producción y detrás dos de UN minuto, pegados. Son
#  la misma sesión de trabajo partida por el terminal, no tres trabajos, y en el
#  Gantt salían como tres barras de las que dos son una raya invisible.
#
#  Se funden los trozos consecutivos del mismo bono, operario y tipo de
#  operación cuando van seguidos y alguno de los dos es un trocito. Que valga
#  con que lo sea UNO de los dos es lo que permite absorber tanto una raya que
#  viene detrás de un trabajo largo como una que va delante.
#
#  Medido sobre 3 meses: 395 líneas de 6.700 (6%), repartidas en 172 bonos.
_TROZO_MIN         = 5   # por debajo de esto, un fichaje es un trocito
_HUECO_TROZO_MIN   = 5   # y se pega al anterior si no dista más que esto

#  Hueco máximo entre el fin del montaje y el inicio de la producción para
#  considerar que son el mismo trabajo. Medido sobre 6 meses: 4.091 de 4.749
#  montajes enlazan con su producción en 2 minutos o menos (86%).
_HUECO_MONTAJE_MIN = 2


def dur_min(it: dict) -> float:
    return (it["end"] - it["start"]).total_seconds() / 60


def fundir_troceados(items: list[dict]) -> list[dict]:
    """Une los fichajes que un operario partió en trozos seguidos.

    Va ANTES de `fundir_montaje` a propósito: así el montaje se encuentra la
    producción ya entera y no se cuelga del primer trozo. Y por eso el grupo
    incluye `es_montaje`: fundir preparación con fabricación es el otro
    problema, tiene otra semántica (`pct_montaje`) y lo resuelve esa función.
    """
    grupos: dict = {}
    for it in items:
        if it["tipo"] in ("real", "trabajado", "parcial"):
            clave = (it["idorden"], it["idbono"], it["recurso_id"], it["es_montaje"])
            grupos.setdefault(clave, []).append(it)

    absorbidos = set()
    for trozos in grupos.values():
        if len(trozos) < 2:
            continue
        trozos.sort(key=lambda x: x["start"])
        base = trozos[0]
        for sig in trozos[1:]:
            hueco = (sig["start"] - base["end"]).total_seconds() / 60
            # `dur_min(base)` se mide sobre lo ya fundido: en cuanto la barra
            # deja de ser un trocito, la siguiente que no lo sea tampoco se pega.
            if not (0 <= hueco <= _HUECO_TROZO_MIN
                    and (dur_min(sig) < _TROZO_MIN or dur_min(base) < _TROZO_MIN)):
                base = sig
                continue
            minutos = [x for x in (base.get("min_real"), sig.get("min_real")) if x is not None]
            base["end"] = max(base["end"], sig["end"])
            # Los minutos REALES se suman; el ancho de la barra incluye además
            # el hueco entre trozos, que no se trabajó.
            base["min_real"] = sum(minutos) if len(minutos) == 2 else None
            # Si el trozo que se absorbe sigue abierto, manda él: la barra
            # fundida es trabajo en curso y se queda con su proyección.
            if sig["tipo"] == "real":
                for k in ("tipo", "estado", "en_curso", "fin_estimado", "libre_desde",
                          "min_restantes", "min_hombre", "a_la_vez", "progreso",
                          "_pendiente", "sin_tiempo", "origen_estimado"):
                    if k in sig:
                        base[k] = sig[k]
            absorbidos.add(id(sig))
    return [it for it in items if id(it) not in absorbidos]


def fundir_montaje(items: list[dict]) -> list[dict]:
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
        total = (minutos_laborables_entre(siguiente["start"], siguiente["end"])
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
#  única barra real acaba con el montaje. Pero `proyectar` reserva al
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

def continuar(items: list[dict], ahora: datetime) -> list[dict]:
    """Las barras de fabricación pendiente de los montajes abiertos.

    Consume `_pendiente`, que `proyectar` deja en la barra de montaje. Se
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
        # Sin dimensionar, `ocupacion_actual` reserva la ventana entera. Se
        # dibuja el bloque nominal de la cola y se avisa de que el fin no se
        # sabe: una barra de días enteros diría una precisión que no hay.
        fin = (sumar_laborables(inicio, MIN_BLOQUE_SIN_TIEMPO) if sin_tiempo
               else it.get("libre_desde") or sumar_laborables(inicio, minutos))
        # Misma regla que en la cola: la media de la máquina dimensiona la
        # barra pero no es un ritmo del que fiarse.
        sin_ritmo = sin_tiempo or p["origen"] == "media_maquina"
        barras.append({
            "id":          f"C-{it['idorden']}-{it['idbono']}-{it['recurso_id']}",
            "recurso_id":  it["recurso_id"],
            # La continuación se fabrica en la MISMA máquina que la barra de
            # la que sale. Sin heredarla, el Gantt no sabía que era la misma y
            # le daba un carril propio: la WAFIOS 1 de Elías salía partida en
            # dos líneas, la del fichaje abierto y la de lo que le queda.
            "matricula":   it.get("matricula", ""),
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
            "min_hombre": round(p["min_hombre"]) if p["min_hombre"] is not None else None,
            "a_la_vez":   p["a_la_vez"],
            "base_estimacion": "piezas",
        })
    return barras
