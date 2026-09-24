"""Las lecturas del ERP que se cachean, y su política de degradación.

Tres cosas se cachean y cada una por un motivo distinto:

  · el ESCANDALLO (`cargar_teoricos`) NO se cachea: lo edita una persona y
    espera verlo al momento. Solo se guarda una copia para poder reutilizarla
    si el ERP falla.
  · las MEDIAS y los MONTAJES (`cargar_estimaciones`) hablan de 18 meses de
    bonos cerrados y cuestan 123 y 52 ms: TTL de 10 minutos.
  · el SEMÁFORO (`cargar_semaforo`) cuesta ~360 ms porque evalúa una función
    del ERP fila a fila: TTL de 2 minutos.

DEGRADACIÓN — esto es lo importante de este módulo. Ninguna de las tres
funciones propaga el fallo: **todas reutilizan la última caché aunque esté
caducada**. Es preferible estimar con datos de hace diez minutos que marcar de
golpe todas las barras como "sin tiempo", y es mejor ordenar la cola con
colores de hace unos minutos que servirla en un orden que el operario no puede
seguir. Ninguna de las tres debe acabar nunca en un 503.

LOS DICCIONARIOS DE CACHÉ SON UNA SOLA INSTANCIA. No se copian, no se
reexportan por valor y no se reconstruyen: si alguien acaba con una copia
propia, la app hace el doble de consultas al ERP y nadie se entera.
"""
import os
import unicodedata
from datetime import datetime
from pathlib import Path

from sqlalchemy import text

import app.erp.consultas as consultas
from app.calculos.cola import medir_atencion
from app.erp.cliente import ErpNoDisponible, conexion, consultar

#  El escandallo y el histórico no cambian por minutos.
ESTIMA_TTL_S = 600

#  La función del semáforo se evalúa fila a fila: la consulta pasa de 12 ms a
#  ~360 ms. Se cachea porque el color cambia cuando llega material o alguien
#  coge un bono —minutos, no segundos— y el Gantt se refresca solo cada pocos
#  minutos.
SEMAFORO_TTL_S = 120

#  LAS caché. Únicas instancias de todo el proceso.
_cache_estima = {"ts": None, "teoricos": {}, "medias": {"articulo": {}, "trabajo": {}, "maquina": {}},
                 "montajes": {"trabajo": {}, "maquina": {}}}

_cache_semaforo = {"ts": None, "mapa": {}}

#  La atención cambia aún menos que las medias —es el comportamiento de una
#  máquina sobre seis meses— pero comparte TTL con ellas para no inventar una
#  política de caché más. La lectura son ~12.000 líneas en crudo.
_cache_atencion = {"ts": None, "mapa": {}}

#  La ficha donde producción declara qué máquinas pueden trabajar solas. Es un
#  dato de NEGOCIO: qué máquina cicla sin nadie encima no se deduce de un
#  histórico, se sabe. Lo que sí se mide es cuánta atención pide cada una,
#  porque ese número nadie lo mantendría a mano.
FICHA_MAQUINAS_AUTO = os.getenv("FICHA_MAQUINAS_AUTO", "maquinas-auto.txt")


def cargar_teoricos() -> dict:
    """El escandallo del ERP, SIN cachear: leído en cada request.

    Es el único dato de la cadena que una persona edita y espera ver reflejado
    al momento. Cuesta 16 ms sobre 30 filas —solo mira bonos abiertos—, así que
    cachearlo solo servía para que un tiempo recién metido tardara hasta diez
    minutos en aparecer. Los agregados históricos, que sí son caros (123 y 52
    ms sobre miles de filas), siguen en `cargar_estimaciones`: esos hablan de
    18 meses de bonos cerrados y no cambian de un minuto a otro.

    `min_pieza` puede venir a None: hay bonos que declaran la preparación y no
    el escandallo. Se guardan igual, porque el setup sirve aunque el ritmo
    tenga que salir del histórico.

    DEGRADA: si el ERP falla devuelve el último escandallo leído. Nunca 503.
    """
    try:
        teoricos = {
            (int(r["idorden"]), int(r["idbono"])):
                (float(r["setup_min"] or 0),
                 float(r["min_pieza"]) if r["min_pieza"] is not None else None)
            for r in consultar(consultas.SQL_TEORICO)
        }
    except ErpNoDisponible as e:
        print(f"[items] escandallo no disponible, se reutiliza el último: {e}")
        return _cache_estima["teoricos"]
    _cache_estima["teoricos"] = teoricos
    return teoricos


def cargar_estimaciones() -> tuple[dict, dict, dict]:
    """(teoricos, medias, montajes). Las dos últimas cacheadas ESTIMA_TTL_S
    segundos; el escandallo va aparte y en vivo (ver `cargar_teoricos`).

    DEGRADA: si el ERP falla se reutiliza la última caché aunque esté caducada.
    Es preferible estimar con datos de hace diez minutos que marcar de golpe
    todas las barras como "sin tiempo". Nunca 503.

    Las dos consultas comparten conexión a propósito: si la de montajes falla
    se descarta también la de medias y se devuelve la caché entera, en vez de
    mezclar medias nuevas con montajes viejos.
    """
    ahora = datetime.now()
    teoricos = cargar_teoricos()
    ts = _cache_estima["ts"]
    if ts is not None and (ahora - ts).total_seconds() < ESTIMA_TTL_S:
        return teoricos, _cache_estima["medias"], _cache_estima["montajes"]

    try:
        with conexion() as conn:
            medias = {"articulo": {}, "trabajo": {}, "maquina": {}}
            for r in conn.execute(text(consultas.SQL_MEDIAS),
                                  {"meses": -consultas.HIST_MESES}).mappings():
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
            for r in conn.execute(text(consultas.SQL_MONTAJE),
                                  {"meses": -consultas.HIST_MESES}).mappings():
                for nivel, clave in (("trabajo", r["idtrabajo"]), ("maquina", r["matricula"])):
                    if clave is None:
                        continue
                    if isinstance(clave, str):
                        clave = clave.strip()
                    acc = montajes[nivel].setdefault(clave, {"n": 0, "minutos": 0.0})
                    acc["n"]       += int(r["n"] or 0)
                    acc["minutos"] += float(r["media"] or 0) * int(r["n"] or 0)
    except ErpNoDisponible as e:
        print(f"[items] históricos no disponibles, se reutiliza la caché: {e}")
        return teoricos, _cache_estima["medias"], _cache_estima["montajes"]

    # `teoricos` no entra aquí: lo refresca y guarda `cargar_teoricos` en cada
    # request, y meterlo en este update lo ataría otra vez al TTL de 10 minutos.
    _cache_estima.update(ts=ahora, medias=medias, montajes=montajes)
    return teoricos, medias, montajes


def cargar_semaforo() -> dict:
    """{(idorden, idbono, idempleado): 'disponible'|'bloqueada'|'en_curso'}.

    DEGRADA: si el ERP falla se reutiliza la última caché aunque esté caducada.
    Es mejor ordenar con colores de hace unos minutos que perder el semáforo
    entero y volver a servir la cola en un orden que el operario no puede
    seguir. Nunca 503.
    """
    ahora = datetime.now()
    ts = _cache_semaforo["ts"]
    if ts is not None and (ahora - ts).total_seconds() < SEMAFORO_TTL_S:
        return _cache_semaforo["mapa"]
    try:
        filas = consultar(consultas.SQL_SEMAFORO)
    except ErpNoDisponible:
        print("[items] semáforo no disponible, se reutiliza la caché")
        return _cache_semaforo["mapa"]

    mapa = {
        (r["idorden"], r["idbono"], r["idempleado"]):
            consultas.SEMAFORO.get(r["color"], 'disponible')
        for r in filas
    }
    _cache_semaforo.update(ts=ahora, mapa=mapa)
    return mapa



# ─────────────────────────────────────────────────────────────────────
#  MÁQUINAS QUE TRABAJAN SOLAS
# ─────────────────────────────────────────────────────────────────────
def _sin_tildes(texto: str) -> str:
    limpio = "".join(c for c in unicodedata.normalize("NFD", texto or "")
                     if unicodedata.category(c) != "Mn")
    return " ".join(limpio.split()).casefold()


def leer_ficha(ruta=None) -> tuple[set, list]:
    """(matrículas declaradas, nombres sueltos) de la ficha de automáticas.

    Cada línea es `matrícula<TAB>nombre` o un nombre a secas. La matrícula es
    lo que manda: un nombre hay que resolverlo contra el catálogo del ERP y
    basta con que alguien renombre la máquina para que deje de encontrarse.

    Si no hay ficha no hay máquinas automáticas y todo se comporta como antes.
    Es la degradación correcta: sin la declaración de producción, nadie queda
    suelto por una estadística.
    """
    fichero = Path(ruta or FICHA_MAQUINAS_AUTO)
    if not fichero.is_file():
        return set(), []
    matriculas, nombres = set(), []
    for linea in fichero.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        if "	" in linea:
            matriculas.add(linea.split("	", 1)[0].strip())
        else:
            nombres.append(linea)
    return matriculas, nombres


def _declaradas(ruta=None) -> set:
    """Las matrículas de la ficha, con los nombres ya resueltos."""
    matriculas, nombres = leer_ficha(ruta)
    if not nombres:
        return matriculas
    catalogo = {}
    for r in consultar(consultas.SQL_CENSO_MAQUINAS):
        catalogo.setdefault(_sin_tildes(r["nombre"]), []).append(str(r["id"]).strip())
    for nombre in nombres:
        hallado = catalogo.get(_sin_tildes(nombre))
        if not hallado:
            print(f"[atencion] la ficha nombra «{nombre}» y el ERP no tiene ninguna "
                  f"máquina que se llame así: se trata como atendida")
            continue
        if len(hallado) > 1:
            print(f"[atencion] «{nombre}» son {len(hallado)} máquinas "
                  f"({', '.join(hallado)}): se declaran todas")
        matriculas.update(hallado)
    return matriculas


def maquinas_automaticas() -> set:
    """Las matrículas que la ficha declara automáticas, tal cual.

    Es la lista de producción sin cruzar con el histórico. La usa la hoja del
    día, que en estas máquinas solo cuenta al operario el montaje: la regla es
    "si está en la lista", no "si el histórico dice que va sola". Lo que mide
    el histórico es `cargar_atencion`, y eso es cosa del planificador.

    DEGRADA: si hay nombres sin matrícula y el ERP no responde para
    resolverlos, se quedan las matrículas escritas. La hoja sale igual.
    """
    try:
        return _declaradas()
    except ErpNoDisponible as e:
        print(f"[atencion] no se pudieron resolver los nombres de la ficha: {e}")
        return leer_ficha()[0]


def cargar_atencion() -> dict:
    """{matricula: fracción de atención} de las máquinas que trabajan solas.

    Cruza las dos fuentes: la ficha dice QUÉ máquina puede ir sola y el
    histórico dice CUÁNTA atención pide. Solo salen las declaradas, y solo
    las que tienen histórico suficiente; el resto no aparece y la cola las
    trata como siempre.

    De paso avisa al revés: una máquina que se comporta como automática sin
    estar declarada. Eso no cambia el plan —la ficha manda— pero es lo que
    detecta que a la ficha le falta una línea, o que le sobra.

    DEGRADA: si el ERP falla se reutiliza la última caché aunque esté
    caducada, igual que las medias. Nunca 503.
    """
    ahora = datetime.now()
    ts = _cache_atencion["ts"]
    if ts is not None and (ahora - ts).total_seconds() < ESTIMA_TTL_S:
        return _cache_atencion["mapa"]
    try:
        declaradas = _declaradas()
        if not declaradas:
            _cache_atencion.update(ts=ahora, mapa={})
            return {}
        lineas = consultar(consultas.SQL_ATENCION,
                           {"meses": -consultas.ATENCION_MESES})
    except ErpNoDisponible as e:
        print(f"[atencion] no disponible, se reutiliza la caché: {e}")
        return _cache_atencion["mapa"]

    medido = medir_atencion(
        [{"idempleado": r["idempleado"], "matricula": r["matricula"],
          "inicio": r["inicio"], "fin": r["fin"]} for r in lineas],
        consultas.ATENCION_MIN_HORAS)

    mapa = {m: f for m, f in medido.items() if m in declaradas}
    for m in sorted(declaradas - set(mapa)):
        print(f"[atencion] la máquina {m} está declarada automática y no tiene "
              f"{consultas.ATENCION_MIN_HORAS} h de histórico: se trata como atendida")
    for m, f in sorted(medido.items()):
        if m not in declaradas and f < 0.5:
            print(f"[atencion] la máquina {m} trabaja sola el {100 * (1 - f):.0f}% "
                  f"de su tiempo y NO está en la ficha: revisar")
    _cache_atencion.update(ts=ahora, mapa=mapa)
    return mapa
