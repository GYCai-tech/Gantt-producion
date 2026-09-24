"""Cuánto dura un bono y cuánto le queda: el ritmo, la preparación y la
proyección de una barra abierta.

Los diccionarios del ERP (`teoricos`, `medias`, `montajes`, `avance`) entran
SIEMPRE por parámetro. En particular `montajes`, que antes se leía de la caché
global `_cache_estima["montajes"]`: era la única dependencia mutable escondida
de toda la cadena de cálculo y hacía imposible probar `minutos_montaje` sin
montar la caché entera.
"""
from datetime import datetime

from app.calculos.calendario import minutos_laborables_entre, sumar_laborables

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
OPERACION_MONTAJE = (1, 2)

_MIN_BONOS_MEDIA  = 3     # con menos bonos, la media es ruido
#  Cuánto puede apartarse el escandallo del histórico del artículo antes de
#  dejar de creérselo. Ver `estimar`.
_FACTOR_ESCANDALLO = 5
_escandallos_avisados: set = set()

#  Último recurso si la máquina no tiene histórico de montajes: la media global
#  medida sobre 18 meses.
_MONTAJE_POR_DEFECTO_MIN = 31

#  Margen antes de dar un bono por retrasado. El ritmo real contra el esperado
#  baila solo con que la preparación caiga dentro o fuera de lo ya declarado;
#  sin margen, el ámbar sería ruido.
_TOLERANCIA_RITMO = 0.15

#  Cuánto del bono hay que llevar hecho para que su ritmo observado sirva para
#  extrapolar lo que falta. Al principio de un bono el ritmo es casi todo
#  preparación: con 2 piezas de 1500 salían 704,5 min/pieza, y extrapolar eso a
#  las 1498 restantes anunciaba «17.589 h al ritmo real». El dato es cierto y
#  no significa nada, así que por debajo de este avance no se ofrece.
_AVANCE_MINIMO_PARA_EXTRAPOLAR = 0.20


def minutos_montaje(linea: dict, montajes: dict) -> float:
    """Cuánto suele durar montar el utillaje de este bono, en minutos.

    Por máquina primero (es lo que determina el montaje: la 107 son 11 min y la
    001 son 113), con respaldo al trabajo y, si no hay histórico de ninguno, la
    media global.

    `montajes` es el `{"trabajo": {...}, "maquina": {...}}` que carga la caché
    del ERP. Antes se leía de la global `_cache_estima["montajes"]`; ahora entra
    por parámetro para que esta función no dependa de nada que no se le pase."""
    for nivel, clave in (("maquina", (linea["matricula"] or "").strip()),
                         ("trabajo", linea["idtrabajo"])):
        acc = montajes[nivel].get(clave)
        if acc and acc["n"] >= _MIN_BONOS_MEDIA:
            return acc["minutos"] / acc["n"]
    return _MONTAJE_POR_DEFECTO_MIN


def avisar_escandallo(linea, teorico, historico, desvio) -> None:
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


def estimar(linea: dict, teoricos: dict, medias: dict, montajes: dict):
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
    setup = setup_erp if setup_erp > 0 else minutos_montaje(linea, montajes)

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
                avisar_escandallo(linea, min_pieza_erp, hist, desvio)
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


def proyectar(item: dict, linea: dict, ahora: datetime, teoricos, medias,
              montajes, avance) -> None:
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
    if linea.get("idoperacion") in OPERACION_MONTAJE:
        dur = minutos_montaje(linea, montajes)
        item["origen_estimado"] = "media_montaje"
        restante = max(0.0, dur - minutos_laborables_entre(item["start"], ahora))
        item["min_restantes"] = round(restante)
        if restante > 0:
            item["end"] = item["fin_estimado"] = sumar_laborables(ahora, restante)
        # Al terminar el montaje aún queda fabricar el bono. Reservar solo
        # hasta el fin de preparación adelantaría el siguiente bono.
        ritmo, _, origen = estimar(linea, teoricos, medias, montajes)
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
            item["libre_desde"] = sumar_laborables(ahora, restante + produccion)
        # Lo que acabamos de reservar tiene que verse: `continuar` lo convierte
        # en barra propia. La clave se consume ahí y no llega al frontend.
        item["_pendiente"] = {
            "minutos":   produccion,
            # Minutos-hombre y cuántos los reparten, para poder explicar en el
            # tooltip por qué la barra mide menos que el trabajo que lleva.
            "min_hombre": produccion * gasto["montando"] if produccion is not None else None,
            "a_la_vez":   gasto["montando"] if gasto else 1,
            "min_pieza": ritmo,
            "origen":    origen,
            "objetivo":  objetivo,
            "hechas":    min(gasto["piezas"], objetivo) if gasto and objetivo else 0.0,
        }
        return

    min_pieza, setup, origen = estimar(linea, teoricos, medias, montajes)
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
        # Lo que falta, medido con el ritmo que de verdad lleva este bono y no
        # con el del escandallo. NO cambia la barra: `restante` sigue mandando
        # sobre `end`, porque mover eso movería también la cola, la ocupación y
        # el plan entero, y esa es una decisión de reglas de producción, no un
        # arreglo de pintado. Pero el tooltip ya puede decirlo: en 6372/30
        # quedan 133 piezas que el escandallo paga a 735 min y que al ritmo
        # real (8,364 min/pieza frente a 5,523) son 1112 — casi una jornada
        # más de la que dibuja la barra.
        item["min_restantes_teoricos"] = round(restante)
        if hechas / objetivo >= _AVANCE_MINIMO_PARA_EXTRAPOLAR:
            item["min_restantes_ritmo_real"] = round(pendientes * ritmo_real)
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
        minutos_barra   = minutos_laborables_entre(item["start"], ahora)
        consumido_antes = max(0.0, consumido - minutos_barra)
        resto           = item["min_estimados"] - consumido_antes
        item["fin_teorico"] = (item["start"] if resto <= 0
                               else sumar_laborables(item["start"], resto))
        # El presupuesto ya estaba gastado ANTES de abrir esta sesión, así que
        # la sesión entera es exceso y no solo el tramo que va de `fin_teorico`
        # a ahora. Sin esta marca, 6372/30 —3748 min gastados de un presupuesto
        # de 3314 antes de empezar la barra de las 11:16— enseñaba un 18% rojo
        # y un 82% con el color normal, que se lee como "el grueso va en plazo"
        # cuando no hay un solo minuto de esa barra dentro del teórico.
        item["presupuesto_agotado_antes"] = resto <= 0
        if consumido > item["min_estimados"]:
            item["min_exceso"] = round(consumido - item["min_estimados"])
            # Y cuánto de ese exceso ha ocurrido DENTRO de esta barra. Hacen
            # falta los dos: `min_exceso` es del bono entero y puede venir de
            # sesiones de otro día y de otra persona, así que escrito encima de
            # una barra no cuadra nunca. En 6447/180 el bono lleva 294 minutos
            # de más, pero 385 de los 407 consumidos se gastaron el 31 de
            # agosto y los hizo otro operario: la barra de hoy mide 23 minutos
            # y anunciaba "+4 h 54". El número de la barra es este; el del bono
            # se queda en el tooltip, que tiene sitio para explicarlo.
            item["min_exceso_barra"] = round(max(0.0, minutos_barra - max(0.0, resto)))

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
    item["min_hombre"]    = round(restante)
    item["a_la_vez"]      = gasto["operarios"]
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
    fin_estimado = sumar_laborables(ahora, restante_reloj)
    item["end"]          = fin_estimado
    item["fin_estimado"] = fin_estimado
    item["libre_desde"]  = fin_estimado

    # `progreso` es el relleno visual de la barra: qué parte de ella ya ha
    # transcurrido, para que lo sólido acabe justo en la línea de ahora.
    # El avance en piezas va aparte, en `progreso_piezas`.
    total = minutos_laborables_entre(item["start"], fin_estimado)
    if total > 0:
        transcurrido = minutos_laborables_entre(item["start"], ahora)
        item["progreso"] = round(max(0.0, min(1.0, transcurrido / total)) * 100)
