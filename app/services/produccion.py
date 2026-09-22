"""Coordinación: qué se lee del ERP, en qué orden y con qué se calcula.

Es la única pieza que conoce a la vez `app.erp` (de dónde salen los datos) y
`app.calculos` (qué se hace con ellos). Ni los routers calculan ni los cálculos
leen: entre las dos cosas va esto.

**Por qué existe `calcular_items`.** El Gantt y la rejilla de cargas tienen que
enseñar el mismo plan —las mismas barras, la misma cola, la misma estimación—,
y antes eso se conseguía haciendo que `/api/plan` llamara a la función del
endpoint `/api/items`. Funcionaba, pero ataba una ruta a otra: cualquier cosa
que se añadiera a la capa HTTP de `/api/items` (una validación, una cabecera,
un `Depends`) se colaba en `/api/plan` sin que nadie lo pidiera, y al revés, un
cambio en `/api/plan` obligaba a leer el otro router para saber si rompía algo.

Ahora las dos rutas llaman aquí. Siguen compartiendo resultado —que era el
objetivo— pero ninguna depende de que la otra se ejecute.

**Errores.** Aquí NO se traduce nada a HTTP: las lecturas imprescindibles dejan
subir `ErpNoDisponible` y es el router quien decide que eso es un 503. Las
lecturas que degradan (paradas, ausencias, cachés caducadas) ya lo resuelven
dentro de `app.erp` y no llegan hasta aquí.
"""
import unicodedata
from datetime import date, datetime, timedelta

from app.calculos.calendario import JORNADA_FIN
from app.calculos.cola import ocupacion_actual, planificar_cola, sin_salida
from app.calculos.estimacion import OPERACION_MONTAJE, proyectar
from app.calculos.fusion import continuar, fundir_montaje, fundir_troceados
from app.erp import cache, consultas, lecturas


def alfabetico(texto: str) -> str:
    """Clave para ordenar nombres como se ordenan en castellano.

    `sorted()` a secas compara por código Unicode, y eso NO es orden
    alfabético: las mayúsculas van antes que todas las minúsculas y las
    vocales con tilde después de la Z.
    """
    sin_tildes = "".join(c for c in unicodedata.normalize("NFD", texto)
                         if unicodedata.category(c) != "Mn")
    return sin_tildes.casefold()


def dia_local(dt: datetime) -> date:
    """El frontend manda `days[0].toISOString()`: medianoche LOCAL escrita en
    UTC. Hay que devolverla a hora local antes de quedarse con el día o, en
    horario español, se pierde un día entero.

    Depende de la zona del proceso, así que el contenedor fija TZ=Europe/Madrid
    (ver docker-compose.yml); si no, dentro de Docker sería UTC."""
    return (dt.astimezone() if dt.tzinfo else dt).date()


# ─────────────────────────────────────────────────────────────────────
#  CENSO (las filas del Gantt y de la rejilla)
# ─────────────────────────────────────────────────────────────────────
def censo(vista: str) -> list[dict]:
    if vista == "maquina":
        return [{
            "id":     str(r["id"]).strip(),
            "nombre": (r["nombre"] or "").strip() or str(r["id"]).strip(),
            "sub":    f"Matrícula {str(r['id']).strip()}",
            "area":   (r["area"] or "").strip() or "Sin área",
        } for r in lecturas.leer_censo_maquinas()]

    # El área de un operario no es su departamento del ERP sino la de las
    # máquinas en las que trabaja: es lo que agrupa de verdad en planta. Solo
    # se mira la actividad reciente, para que quien cambió de sección no
    # arrastre para siempre las áreas de su puesto anterior.
    areas_por_empleado: dict[str, set] = {}
    for r in lecturas.leer_areas_empleado():
        area = (r["area"] or "").strip()
        if area:
            areas_por_empleado.setdefault(str(r["idempleado"]), set()).add(area)

    grupos = []
    for r in lecturas.leer_censo_empleados():
        gid = str(r["idempleado"])
        areas = sorted(areas_por_empleado.get(gid, ())) or ["Sin máquina"]
        grupos.append({
            "id":     gid,
            "nombre": lecturas.nombre_completo(r),
            "sub":    ", ".join(areas),
            "areas":  areas,
        })
    return sorted(grupos, key=lambda g: alfabetico(g["nombre"]))


# ─────────────────────────────────────────────────────────────────────
#  COLA (las barras "programado")
# ─────────────────────────────────────────────────────────────────────
def encolar(vista: str, ocupado_hasta: dict, hasta_dt: datetime,
            ahora: datetime, teoricos, medias, montajes,
            atencion: dict | None = None) -> list[dict]:
    """Proyecta el mismo plan en filas de operarios o de máquinas.

    Es la costura entre lectura y cálculo: lee la cola del ERP y se la pasa a
    `planificar_cola`, que ya no sabe de dónde vino.
    """
    plan = planificar_cola(lecturas.leer_cola(), ocupado_hasta, hasta_dt,
                           ahora, teoricos, medias, montajes, atencion)
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
            # (ver `proyectar`); la cola las pintaba en verde como si la
            # estimación fuera buena. Mismo origen, mismo aviso.
            sin_ritmo = sin_tiempo or tarea["origen"] == "media_maquina"
            items.append({
                "id": f"P-{b['idorden']}-{b['idbono']}-{b['matricula']}-{rid}",
                "recurso_id": rid,
                # La barra real ya traía la matrícula; la proyectada no, y el
                # Gantt la necesita para repartir los carriles por máquina:
                # `operacion` es el NOMBRE de la máquina y dos pueden
                # compartirlo (la 089 y la 104 son las dos "BATTENFELD ECO
                # 110/350 B6"), así que no sirve como identidad.
                "matricula": (b["matricula"] or "").strip(),
                "tipo": "programado",
                # El bloqueo gana a la falta de estimacion: que no se sepa
                # cuanto tarda no cambia el plan, que no se pueda empezar si.
                # 6708/10 esta rojo en el ERP para Jose Manuel y salia ambar
                # "sin datos fiables" solo porque su ritmo viene de la media de
                # la maquina. Sobre verde manda el aviso de estimacion: ahi lo
                # que hay que decir es que el dato no es de fiar. Y un bono a
                # medias no es trabajo "disponible": es fabricacion pendiente,
                # la misma etiqueta que ya usa el bono que se quedo montando.
                #  BLOQUEADA y PARADA no son lo mismo, y el orden de estas
                #  preguntas es lo que las separa:
                #
                #    · BLOQUEADA — el bono no se ha hecho todavia, esta en cola
                #      y el semaforo del ERP dice que ese operario no puede
                #      ponerse con el.
                #    · PARADA — hubo un fichaje activo y se paro. El trabajo
                #      empezo y quedo a medias.
                #
                #  Por eso haber arrancado gana al semaforo: 6610/60 plego 60
                #  de 120 laterales, cerro el fichaje a las 09:54 y se quedo
                #  sin material; el semaforo lo pone en rojo y salia como
                #  "Bloqueada", cuando lo que le paso es que se PARO. Un bono
                #  que ya tuvo a alguien trabajandolo nunca es "no empezado".
                "estado": ("parada" if tarea["arrancado"] and b["ultimo_fichaje"] else
                           "bloqueada" if semaforo == "bloqueada" else
                           "sin-estimar" if sin_ritmo else "disponible"),
                # Trabajo a medias que se retoma, y cuando se toco por ultima
                # vez: sin esto el tooltip no explica por que hay 396 piezas
                # pendientes de un bono de 1080 que nadie ha empezado hoy.
                "reanudado": tarea["arrancado"],
                "ultimo_fichaje": b["ultimo_fichaje"],
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
                # La máquina cicla sola: la barra dura lo que dura el bono,
                # pero al operario solo le cuesta `min_atencion`. Por eso su
                # fila puede tener dos barras encima a la vez sin que sea un
                # error de reparto.
                "desatendida": tarea["desatendida"],
                "min_atencion": round(tarea["min_atencion"]),
                "base_estimacion": "piezas",
            })
    return items


# ─────────────────────────────────────────────────────────────────────
#  ITEMS: el resultado de planificación que comparten Gantt y cargas
# ─────────────────────────────────────────────────────────────────────
def calcular_items(vista: str, desde=None, hasta=None) -> list[dict]:
    hoy = date.today()
    d0 = dia_local(desde) if desde else hoy
    # `hasta` llega como el día siguiente al último visible (fin exclusivo).
    d1 = dia_local(hasta - timedelta(seconds=1)) if hasta else hoy
    if d1 < d0:
        d1 = d0

    ahora = datetime.now()
    lineas = lecturas.leer_lineas(d0, d1)
    abiertas = lecturas.leer_abiertas(ahora) if d1 >= hoy else []
    # La ocupación de hoy debe seguir reservada al navegar a mañana. También
    # permite dibujar la continuación de un bono iniciado fuera de la ventana.
    por_id = {(l["idorden"], l["idbono"], l["idlinea"]): l for l in lineas}
    por_id.update({(l["idorden"], l["idbono"], l["idlinea"]): l for l in abiertas})
    lineas = list(por_id.values())
    teoricos, medias, montajes = cache.cargar_estimaciones()
    # Qué máquinas trabajan solas y cuánta atención piden. Vacío si no hay
    # ficha o no se pudo leer: entonces todo ata al operario, como siempre.
    atencion = cache.cargar_atencion()
    avance = lecturas.leer_avance([l for l in lineas if l["abierta"]], ahora)
    paradas = lecturas.leer_paradas()

    items = []
    for l in lineas:
        inicio  = l["inicio"]
        abierta = (l["abierta"] and d1 >= hoy
                   and timedelta(0) <= ahora - inicio <= timedelta(hours=consultas.HORAS_LINEA_VIVA))
        fin     = l["fin"] or ahora
        if l["abierta"] and not abierta:
            fin = min(ahora, max(inicio, inicio.replace(hour=JORNADA_FIN, minute=0, second=0, microsecond=0)))
        min_real = None if l["abierta"] else round((fin - inicio).total_seconds() / 60)

        # La línea abierta es trabajo EN CURSO; la cerrada, trabajo hecho.
        # No hay un tercer estado que inventar: el ERP no dice nada más.
        montaje = l.get("idoperacion") in OPERACION_MONTAJE
        item = {
            "id":         f"{l['idorden']}-{l['idbono']}-{l['idlinea']}",
            "recurso_id": str(l["idempleado"]) if vista == "empleado" else str(l["matricula"]).strip(),
            "idempleado": str(l["idempleado"]),
            "matricula": (l["matricula"] or "").strip(),
            "tipo":       "real" if abierta else "parcial" if l["abierta"] else "trabajado",
            "estado":     "plazo" if abierta else "parcial" if l["abierta"] else "completado",
            "en_curso":   abierta,
            # Preparar la máquina no es fabricar: son fichajes distintos y hay
            # que poder distinguirlos. Ver OPERACION_MONTAJE.
            "es_montaje":   montaje,
            "tipo_trabajo": "montaje" if montaje else "produccion",
            #  Parada anotada sobre ESTA línea de fichaje. Va aparte del
            #  `estado` a propósito: un bono puede ir en plazo y haber tenido
            #  una avería igualmente, así que son cosas distintas y se marcan
            #  distinto.
            "paro":       paradas.get((l["idorden"], l["idbono"], l["idlinea"])),
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
            proyectar(item, l, ahora, teoricos, medias, montajes, avance)

        items.append(item)

    # La cola solo tiene sentido si la ventana llega a hoy o más allá: en un
    # día pasado no había "programado", había lo que pasó.
    if d1 >= hoy:
        hasta_dt = datetime.combine(d1, datetime.min.time()).replace(hour=JORNADA_FIN)
        ids_abiertas = {f"{l['idorden']}-{l['idbono']}-{l['idlinea']}" for l in abiertas}
        ocupado_hasta = ocupacion_actual(
            [it for it in items if it["id"] in ids_abiertas], hasta_dt, ahora,
            atencion,
        )
        cola = encolar(vista, ocupado_hasta, hasta_dt, ahora, teoricos, medias,
                       montajes, atencion)
    else:
        cola = []

    inicio_ventana = datetime.combine(d0, datetime.min.time())
    fin_ventana = datetime.combine(d1 + timedelta(days=1), datetime.min.time())
    # `continuar` va sobre la lista ya fundida y siempre se llama: es lo que
    # pinta la reserva de un bono en montaje y, de paso, lo que limpia la clave
    # interna que `proyectar` deja en esas barras.
    visibles = fundir_montaje(fundir_troceados(items))
    return [it for it in visibles + continuar(visibles, ahora) + cola
            if it["end"] > inicio_ventana and it["start"] < fin_ventana]


# ─────────────────────────────────────────────────────────────────────
#  AVISOS DE FILA
# ─────────────────────────────────────────────────────────────────────
def calcular_avisos(vista: str, dia=None) -> dict:
    """Los avisos que no son de un bono sino del recurso entero.

    Una máquina no "se queda sin poder trabajar" —el semáforo es de la
    persona—, así que en la vista de máquinas no hay nada que avisar. Las
    ausencias son igual de personales: una máquina no se va al médico.
    """
    if vista != "empleado":
        return {"sin_salida": {}, "ausencias": {}}
    ahora = datetime.now()
    trabajando = {str(l["idempleado"]) for l in lecturas.leer_abiertas(ahora)}
    return {
        "sin_salida": sin_salida(lecturas.leer_cola(), trabajando),
        "ausencias":  lecturas.leer_ausencias(dia or date.today()),
    }
