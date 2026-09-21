"""La previsión de la cola: qué bono pendiente cae en qué hueco de quién.

Todo lo que necesita —la cola ya leída del ERP, la ocupación actual y los
diccionarios de estimación— entra por parámetro. Aquí no se consulta nada.
"""
from datetime import datetime

from app.calculos.calendario import siguiente_hueco, sumar_laborables
from app.calculos.estimacion import estimar

#  Un bono en cola sin tiempo estimado no se puede dimensionar. Se le da un
#  bloque nominal para que siga ocupando su sitio en la cola (si no, los que
#  van detrás se adelantarían como si no existiera) y va marcado `sin_tiempo`.
MIN_BLOQUE_SIN_TIEMPO = 60

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


def ocupacion_actual(items: list[dict], hasta: datetime, ahora: datetime) -> dict:
    """Reserva operario y máquina hasta que `proyectar` dice que se liberan.

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


def hueco_para(intervalos: list, desde: datetime, dur: float) -> tuple:
    """El primer momento desde `desde` en que caben `dur` minutos seguidos.

    Busca HUECOS en vez de ponerse a la cola detrás de todo: si un recurso
    está reservado mañana por la mañana, hoy por la tarde sigue libre y hay
    que poder usarlo. Con la ocupación como un único "libre a partir de" eso
    era imposible de expresar.

    El bucle avanza siempre —cada choque devuelve un fin posterior al instante
    probado— así que termina.
    """
    t = siguiente_hueco(desde)
    while True:
        fin = sumar_laborables(t, dur)
        choque = max((b for a, b in intervalos if a < fin and t < b), default=None)
        if choque is None:
            return t, fin
        t = siguiente_hueco(choque)


def planificar_cola(cola: list[dict], ocupado_hasta: dict, hasta_dt: datetime,
                    ahora: datetime, teoricos, medias, montajes) -> list[dict]:
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
        # Bono arrancado y con piezas ya declaradas: hay material a medias y la
        # máquina montada, así que se termina antes de empezar nada nuevo. Va
        # por delante incluso de la secuencia manual, pero NUNCA por delante
        # del semáforo: un bono en rojo no se puede continuar por mucho que
        # tenga piezas hechas.
        reanudado = filas[0]["arrancado"] and filas[0]["fabricadas"] > 0
        tareas.append((filas, semaforo, min(secuencias) if secuencias else None,
                       reanudado))
    tareas.sort(key=lambda t: (_PRIO_SEMAFORO[t[1]], 0 if t[3] else 1,
                              t[2] is None, t[2] or 0,
                              t[0][0]["idorden"], t[0][0]["idbono"],
                              t[0][0]["matricula"]))

    ocupado = dict(ocupado_hasta)
    plan = []
    for asignados, semaforo, secuencia, reanudado in tareas:
        b = asignados[0]
        min_pieza, setup, origen = estimar(b, teoricos, medias, montajes)
        pendientes = max(0.0, b["piezas_a_fabricar"] - b["fabricadas"])
        if pendientes <= 0:
            continue
        # La preparación ya fichada no se paga dos veces: la máquina sigue
        # montada desde que se dejó el bono a medias.
        if b["montado"]:
            setup = 0.0
        sin_tiempo = not min_pieza or min_pieza <= 0
        dur = MIN_BLOQUE_SIN_TIEMPO if sin_tiempo else setup + pendientes * min_pieza

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
            arranque, remate = hueco_para(intervalos, ahora, dur_reloj)
            for a in asignados:
                clave = ("empleado", str(a["idempleado"]))
                ocupado.setdefault(clave, []).append((arranque, remate))
                huecos[str(a["idempleado"])] = (arranque, remate)
        else:
            for a in asignados:
                clave = ("empleado", str(a["idempleado"]))
                arranque, remate = hueco_para(
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
            "arrancado": b["arrancado"], "reanudado": reanudado,
            "huecos": huecos,
            "pendientes": pendientes, "sin_tiempo": sin_tiempo,
            "min_pieza": min_pieza, "origen": origen, "duracion": dur_reloj,
            # Los minutos-hombre y cuántos lo hacen: sin esto, el tooltip de un
            # bono de cuadrilla dice "83 min" para 60 piezas a 5,18 min/pieza y
            # no hay forma de cuadrar la cuenta.
            "min_hombre": dur, "a_la_vez": len(asignados) if cuadrilla else 1,
        })
    return plan


def sin_salida(cola: list[dict], trabajando: set[str]) -> dict[str, int]:
    """{idempleado: nº de bonos} de quien tiene TODA su cola en rojo y está parado.

    No es "va justo de trabajo": es que no puede empezar ninguno de los bonos
    que tiene asignados, que es lo que el programa de producción del ERP
    muestra como una pantalla entera en rojo. Hace falta decirlo aparte porque
    bono a bono ya se veía —cada barra sale como "Bloqueada"— y lo que no se
    veía era que no quedara ni uno verde.

    Se mide sobre la cola COMPLETA, no sobre los bonos que caben en la ventana
    visible: el aviso es del operario y no debe encenderse o apagarse al
    cambiar de zoom. Medido hoy: 3 de los 19 operarios con cola, uno con 18
    bonos y dos con uno solo.

    Quien está fichando algo AHORA queda fuera aunque su cola entera esté en
    rojo. No está parado: está produciendo, y una fila roja diciendo que no
    puede hacer nada contradice su propia barra. El bono que ficha no cuenta
    para el color porque ya no está en la cola (ver `_COLA_QUERY`).
    """
    por_empleado: dict[str, list[str]] = {}
    for b in cola:
        por_empleado.setdefault(str(b["idempleado"]), []).append(b["semaforo"])
    return {rid: len(s) for rid, s in por_empleado.items()
            if rid not in trabajando and all(x == "bloqueada" for x in s)}
