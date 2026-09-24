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

#  Cuántas máquinas puede llevar una persona A LA VEZ. No es una constante
#  técnica sino una política, y sale de lo que ya se hace: medido sobre 6
#  meses, dos máquinas desatendidas simultáneas son 463 h, tres son 141 h y
#  cuatro bajan a 5 h. Dos es corriente, tres pasa, cuatro no.
#
#  Hace falta un tope porque la atención no acota nada por sí sola: con una
#  inyectora al 10%, un bono de cinco horas solo ata al operario media hora y
#  el reparto le colgaría cinco máquinas sin despeinarse.
_MAX_SIMULTANEAS = 3


def _minutos_cubiertos(linea: dict, otras: list[dict]) -> float:
    """Minutos de `linea` que caen dentro de la UNIÓN de `otras`.

    La unión y no la suma: quien lleva tres máquinas a la vez tiene cada línea
    solapada por dos, y sumarlas daría más minutos solapados que minutos
    fichados —atenciones negativas—. Lo que se pregunta es "¿estaba haciendo
    otra cosa?", que se responde una sola vez por minuto.
    """
    tramos = []
    for o in otras:
        ini, fin = max(linea["inicio"], o["inicio"]), min(linea["fin"], o["fin"])
        if fin > ini:
            tramos.append((ini, fin))
    total, tope = 0.0, None
    for ini, fin in sorted(tramos):
        arranque = ini if tope is None or ini > tope else tope
        if fin > arranque:
            total += (fin - arranque).total_seconds() / 60
        tope = fin if tope is None or fin > tope else tope
    return total


def medir_atencion(lineas: list[dict], min_horas: float) -> dict[str, float]:
    """{matricula: fracción de su tiempo que necesita a alguien encima}.

    Se mide sobre fichajes cerrados: para cada línea, qué parte transcurrió
    mientras el MISMO operario tenía otra abierta. Una máquina que cicla sola
    aparece solapada una y otra vez; una que exige manos, nunca.

    Es una costumbre observada, no una especificación: dice lo que se hizo, no
    lo que la máquina permite. Por eso no decide sola qué máquina es
    automática —eso lo declara producción— sino cuánta atención pide una de
    las que ya están declaradas, que es el número que nadie puede mantener a
    mano.

    Las máquinas con poco histórico se quedan fuera y quien las mire no
    encontrará nada: sin medida el plan las trata como atendidas, que es
    exactamente como se comporta hoy.
    """
    por_persona_dia: dict = {}
    for l in lineas:
        clave = (str(l["idempleado"]), l["inicio"].date())
        por_persona_dia.setdefault(clave, []).append(l)

    acc: dict = {}
    for grupo in por_persona_dia.values():
        for i, l in enumerate(grupo):
            matricula = str(l["matricula"] or "").strip()
            if not matricula:
                continue
            a = acc.setdefault(matricula, [0.0, 0.0])
            a[0] += _minutos_cubiertos(l, [o for j, o in enumerate(grupo) if j != i])
            a[1] += (l["fin"] - l["inicio"]).total_seconds() / 60

    return {m: max(0.0, 1.0 - solapado / total)
            for m, (solapado, total) in acc.items()
            if total >= min_horas * 60}


def ocupacion_actual(items: list[dict], hasta: datetime, ahora: datetime,
                     atencion: dict | None = None) -> dict:
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

    Sin `atencion` reserva a los dos por igual, que es como se comportaba
    antes de que existiera este parámetro.
    """
    ocupado: dict = {}
    for it in items:
        fin = it.get("libre_desde")
        if fin is None:
            fin = max(hasta, ahora)
        # La máquina que cicla sola no ata a quien la vigila. Sin esto, el
        # operario con una inyectora abierta a las 09:00 no recibía NADA en lo
        # que quedaba de día: es el mismo error que en la cola, pero sobre
        # trabajo real, que es la parte fiable de la proyección.
        cuanto = (atencion or {}).get(str(it.get("matricula") or "").strip(), 1.0)
        fin_persona = fin if cuanto >= 1 else ahora + (fin - ahora) * cuanto
        for tipo, rid, hasta_cuando in (("empleado", it["idempleado"], fin_persona),
                                        ("maquina", it["matricula"], fin)):
            if rid and hasta_cuando > ahora:
                ocupado.setdefault((tipo, str(rid)), []).append((ahora, hasta_cuando))
        # Aunque esté suelto sigue siendo responsable de ella: cuenta para el
        # tope de máquinas simultáneas.
        if atencion and cuanto < 1 and it["idempleado"] and fin > ahora:
            ocupado.setdefault(("vigila", str(it["idempleado"])), []).append((ahora, fin))
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
    t = hueco_compartido([(intervalos, dur, 0)], desde)
    return t, sumar_laborables(t, dur)


def hueco_compartido(requisitos: list[tuple], desde: datetime) -> datetime:
    """El primer instante en que se cumplen TODOS los requisitos a la vez.

    Cada requisito es `(intervalos, duración, tope)`: cuántos solapes tolera
    ese recurso. Con `tope` a 0 es exclusivo —una máquina no hace dos cosas—
    y con 2 admite dos cosas ya en marcha, que es lo que permite que una
    persona vigile varias máquinas sin que el plan lo trate como un choque.

    Hace falta que sea conjunto y no una cadena de llamadas porque las
    duraciones son DISTINTAS: la máquina hay que tenerla libre las cinco
    horas del bono y al operario solo la media hora que le presta. Buscar
    hueco para cada uno por separado daría dos instantes que no tienen por
    qué coincidir.

    Termina por lo mismo que antes: cada espera devuelve un fin posterior al
    instante probado, así que `t` solo avanza.
    """
    t = siguiente_hueco(desde)
    for _ in range(400):
        espera = None
        for intervalos, dur, tope in requisitos:
            fin = sumar_laborables(t, dur)
            chocan = sorted(b for a, b in intervalos if a < fin and t < b)
            if len(chocan) <= tope:
                continue
            # Hay que esperar a que se libere lo justo para bajar al tope: el
            # que antes acabe de los que sobran, no el último de todos.
            candidato = chocan[len(chocan) - tope - 1]
            if espera is None or candidato > espera:
                espera = candidato
        if espera is None:
            return t
        t = siguiente_hueco(espera)
    return t


def _requisitos(ocupa_maquina: list, ocupa_persona: list, vigila: list,
                dur_maquina: float, dur_persona: float, desatendida: bool) -> list:
    """Los tres recursos que tiene que haber libres para colocar un bono."""
    reqs = [(ocupa_maquina, dur_maquina, 0), (ocupa_persona, dur_persona, 0)]
    if desatendida:
        # Ya en marcha puede tener _MAX_SIMULTANEAS - 1, porque esta cuenta
        # también.
        reqs.append((vigila, dur_maquina, _MAX_SIMULTANEAS - 1))
    return reqs


def _reservar(ocupado: dict, rid: str, arranque: datetime,
              dur_maquina: float, dur_persona: float, desatendida: bool) -> None:
    """Apunta lo que este bono le quita al operario, y lo que solo vigila."""
    ocupado.setdefault(("empleado", rid), []).append(
        (arranque, sumar_laborables(arranque, dur_persona)))
    if desatendida:
        ocupado.setdefault(("vigila", rid), []).append(
            (arranque, sumar_laborables(arranque, dur_maquina)))


def planificar_cola(cola: list[dict], ocupado_hasta: dict, hasta_dt: datetime,
                    ahora: datetime, teoricos, medias, montajes,
                    atencion: dict | None = None) -> list[dict]:
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

    MÁQUINAS QUE TRABAJAN SOLAS. `atencion` trae, por matrícula, qué parte
    del bono necesita a alguien encima. Un bono en una inyectora ocupa la
    MÁQUINA de principio a fin pero solo ata a la PERSONA mientras la
    necesita: monta el molde, arranca y se va a otra cosa. Sin este
    diccionario los dos recursos reservan lo mismo, que es como se comportaba
    esto antes y por qué el 12,9% del trabajo real de la planta no cabía en
    ninguna proyección.

    La preparación no se descuenta nunca: montar el utillaje ocupa a la
    persona entera, y el ERP ya la ficha aparte (ver OPERACION_MONTAJE).

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

        # Lo que el bono le cuesta a la PERSONA, que no es lo que dura.
        # La preparación se paga entera y solo la producción se descuenta.
        # Un bono sin estimar no se toca: su bloque nominal es un hueco
        # reservado a ojo y aplicarle una fracción sería afinar una conjetura.
        cuanto = (atencion or {}).get((b["matricula"] or "").strip(), 1.0)
        setup_reloj = setup / len(asignados) if cuadrilla else setup
        if sin_tiempo or cuanto >= 1:
            dur_persona = dur_reloj
        else:
            dur_persona = setup_reloj + cuanto * max(0.0, dur_reloj - setup_reloj)
        desatendida = dur_persona < dur_reloj

        huecos = {}
        if cuadrilla:
            # Trabajan JUNTOS, así que hace falta un hueco en el que estén
            # libres todos a la vez. Es lo contrario del caso de abajo y por
            # eso convive con él: ahí "asignado" significa "que lo coja quien
            # pueda" y esperar a los demás vaciaba colas enteras.
            intervalos = [
                iv for a in asignados
                for iv in ocupado.get(("empleado", str(a["idempleado"])), ())
            ]
            vigila = [iv for a in asignados
                      for iv in ocupado.get(("vigila", str(a["idempleado"])), ())]
            arranque = hueco_compartido(
                _requisitos(ocupa_maquina, intervalos, vigila,
                            dur_reloj, dur_persona, desatendida), ahora)
            for a in asignados:
                _reservar(ocupado, str(a["idempleado"]), arranque,
                          dur_reloj, dur_persona, desatendida)
                huecos[str(a["idempleado"])] = (arranque,
                                                sumar_laborables(arranque, dur_reloj))
        else:
            for a in asignados:
                rid = str(a["idempleado"])
                arranque = hueco_compartido(
                    _requisitos(ocupa_maquina, list(ocupado.get(("empleado", rid), ())),
                                list(ocupado.get(("vigila", rid), ())),
                                dur_reloj, dur_persona, desatendida), ahora)
                _reservar(ocupado, rid, arranque, dur_reloj, dur_persona, desatendida)
                # La barra se pinta con lo que dura el BONO, no con lo que le
                # cuesta a la persona: en su fila tiene que verse la máquina
                # corriendo bajo su nombre hasta que termina. Lo que encoge es
                # la reserva, no el dibujo.
                huecos[rid] = (arranque, sumar_laborables(arranque, dur_reloj))

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
            # Cuánto de la barra ata de verdad al operario. Con la máquina
            # atendida coincide con `duracion` y no hay nada que explicar.
            "min_atencion": dur_persona, "desatendida": desatendida,
            # La preparación va al principio de la barra y en minutos de reloj.
            # No cambia nada del plan: la usa la hoja del día, que en las
            # máquinas automáticas solo cuenta al operario el montaje.
            "min_preparacion": 0.0 if sin_tiempo else setup_reloj,
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
