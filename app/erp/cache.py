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
from datetime import datetime

from sqlalchemy import text

import app.erp.consultas as consultas
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
