# Producción del día (GYC)

Gantt de una sola pantalla: qué líneas de bono activas hay en el ERP en un
día concreto, quién las hizo, en qué máquina y durante cuánto tiempo.

No hay planificación, ni estimaciones, ni cola de "programado": **cada barra
es una línea real del ERP**, de su `Hinicial` a su `Hfinal`. Lo que no está en
el ERP, no se pinta.

> La versión anterior del proyecto (Gantt con scheduler propio, Histórico de
> Producción y Consultor de Bonos) está íntegra en la rama
> [`legacy/planificador-v1`](../../tree/legacy/planificador-v1).

## Arranque rápido

```bash
pip install -r requirements.txt
cp .env.example .env   # rellenar usuario/contraseña de SQL Server
uvicorn app.main:app --reload --port 8077

# con Docker
docker compose up -d --build   # expone 8001 y 70 -> 8000 del contenedor
```

Requiere el **ODBC Driver 18 for SQL Server** (el Dockerfile ya lo instala).

## La consulta

Toda la app se sostiene sobre una consulta, traducida a T-SQL desde el Access
original y encapsulada en `GET /api/lineas`:

```sql
SELECT obl.IdOrden, obl.IdBono, obl.IdLinea, obl.IdOperacion, obl.IdEmpleado,
       ed.Nombre, ed.Apellidos, obl.Fecha, obl.Hinicial, obl.Hfinal,
       obl.Matricula, a_maq.Descrip, obs.Cantidad, obs.IdArticulo, a_sal.Descrip
FROM Ordenes_Bonos_Lineas obl
    JOIN Articulos_Maquinas am     ON obl.Matricula  = am.IdArticulo
    JOIN Articulos a_maq           ON am.IdArticulo  = a_maq.IdArticulo
    JOIN Ordenes_Bonos_Salidas obs ON obl.IdOrden    = obs.IdOrden
                                  AND obl.IdBono     = obs.IdBono
    JOIN Articulos a_sal           ON obs.IdArticulo = a_sal.IdArticulo
    JOIN Empleados_Datos ed        ON obl.IdEmpleado = ed.IdEmpleado
WHERE obl.IdEstado = 1
  AND CAST(obl.Fecha AS date) = :dia
ORDER BY obl.Fecha
```

Dos columnas se añaden sobre el Access original, y por un motivo concreto:

- **`Hinicial` / `Hfinal`** — sin ellas no hay barra que pintar. `Fecha` es el
  instante en que el ERP grabó la línea, no su duración.
- **`Apellidos`** — las filas del Gantt son personas y `Nombre` a secas repite
  demasiado (hay varios "José").

Y un detalle del `JOIN`: si un bono declara más de un artículo de salida, la
consulta duplica la línea (medido: 3 bonos de 16.386). Como en el Gantt eso
saldría como dos barras idénticas superpuestas, `api.py` deduplica por
`(orden, bono, línea)`, que es la identidad real de una barra.

## Arquitectura

```
app/
  main.py            # FastAPI: monta /static, serializa Decimal/datetime
  db.py              # engine SQLAlchemy perezoso hacia SQL Server (solo lectura)
  routers/
    pages.py         # una ruta HTML: "/"
    api.py           # /api/grupos, /api/items, /api/lineas, /api/refrescar
templates/           # base.html + index.html (Jinja2)
static/css/app.css   # estilos del Gantt
static/js/app.js     # el motor de render, vanilla JS sin build step
```

**El ERP es solo lectura.** La app nunca escribe en `GOMEZYCRESPO`.

Se lee del ERP en vivo y no de la réplica analítica `gyc_analytics` porque
`Ordenes_Bonos_Lineas` —el detalle línea a línea de cada bono— no está
replicado en PostgreSQL. De paso, se evitan los desfases del ETL.

### El frontend no cambió

`static/js/app.js`, `static/css/app.css` e `index.html` son los mismos de la
v1: motor de Gantt propio (vanilla JS, sin librerías), con eje en horas de
trabajo 07:00–15:00, descanso 11:00–11:15, carriles para bonos solapados,
toggle Operarios/Máquinas, zoom Día/3 días/Semana, píldoras de área, filtro
de carga, buscador, tooltip y modal de detalle. Lo único que cambió es **de
dónde salen las filas y las barras**. (En `base.html` se quitaron los dos
enlaces a las páginas eliminadas.)

### Los endpoints que consume

**`GET /api/grupos?vista=empleado|maquina`** — las filas.

El censo completo de quien alguna vez ha tenido una línea de bono: 29
operarios, 103 máquinas. **No depende de la ventana visible a propósito**: el
frontend carga los grupos una sola vez, así que si la lista dependiera de
fechas, al navegar a otro día habría barras sin fila a la que colgarse y
desaparecerían sin aviso. El área de un operario sale de las máquinas que ha
usado en los últimos 90 días, no de su departamento del ERP: es lo que agrupa
de verdad en planta.

**`GET /api/items?vista=&desde=&hasta=`** — las barras.

| origen                       | tipo         | se ve como                            |
|------------------------------|--------------|---------------------------------------|
| línea del ERP sin `Hfinal`   | `real`       | En curso (verde, late)                |
| línea del ERP con `Hfinal`   | `trabajado`  | Completada (gris)                     |
| bono asignado y sin arrancar | `programado` | Disponible (verde) / Bloqueada (rojo) |

Las dos primeras son trabajo real fichado. La tercera es la **cola** (ver más
abajo): no se inventa nada de cuándo se hará, solo se encadena lo que el ERP ya
tiene asignado a esa persona.

### Cuánto le queda a un bono

Una barra abierta no se corta en "ahora": se estira hasta su **fin estimado**,
con la parte transcurrida en relleno sólido y lo que queda tenue.

Lo que falta se calcula **en piezas, no en minutos**:

```
(cantidad objetivo − piezas declaradas) × min/pieza
```

Restar minutos —"presupuesto del bono menos lo ya gastado"— era el primer
intento y estaba mal: un bono puede **cambiar de manos**, y entonces la barra
de quien lo tiene ahora heredaba el tiempo que gastó otro. Medido en 6372/30:
de sus 3.400 minutos, 1.434 eran de un compañero que lo dejó cuatro días
antes. En piezas eso no pasa: da igual quién hizo las anteriores.

El `min/pieza` sale de esta cadena, en este orden:

1. **Tiempo teórico** — escandallo del ERP: `SUM(Trabajos_ManoObra.Duracion)`
   (viene en días, ×1440).
2. **Media de los registros** — calculada aquí desde los bonos ya cerrados de
   los últimos 18 meses, mínimo 3 bonos por clave: **artículo → trabajo →
   máquina**. Cuenta **solo minutos de producción** (`IdOperacion = 0`).
3. **Nada** — barra ámbar con `⚠`, acaba en "ahora", y suma al contador
   *"N sin tiempo"* de la cabecera, que filtra al pincharlo.

**La preparación va por su cuenta**, no atada a la rama que gane el ritmo:
`TiempoMontaje + TiempoDesMontaje` del bono si el ERP los declara, y si no, lo
que suele tardarse en montar esa máquina. Solo se cobra si el bono aún no ha
gastado ni un minuto — si ya arrancó, esa preparación está pagada.

Antes iba pegada al escandallo, y como el escandallo (10 bonos abiertos) y la
preparación (18) casi no se solapan —**solo 1 bono tiene ambas**—, 17 de los 18
tiempos de montaje declarados se tiraban. Y el ritmo incluía los minutos de
montaje, que al repartirse entre las piezas inflaba los lotes cortos: en un bono
de 3 piezas con 22 min de montaje y 6 de fabricación salía un min/pieza de 9,3
cuando el real es 1,9.

Efecto de arreglar las dos cosas, medido sobre la misma cola:

| Lote | Bonos | Antes | Ahora | |
|---|---|---|---|---|
| 1-5 piezas | 46 | **2 min** | 12 min | +637 % |
| 6-50 | 55 | 28 min | 42 min | +50 % |
| 51-500 | 77 | 89 min | 94 min | +5 % |
| +500 | 27 | 799 min | 811 min | +1 % |
| **Total** | | 742 h | 751 h | **+1 %** |

El total apenas se mueve: lo que cambia es **el reparto**. La app venía diciendo
que un bono de 3 piezas se hace en 2 minutos, cuando no da ni para preparar la
máquina.

**Retraso.** Se compara el ritmo real (`minutos gastados / piezas hechas`) con
el esperado. Por encima de un **15 %** la barra pasa a ámbar como *En riesgo*,
pero **se sigue dibujando lo que falta**: ir tarde no borra el trabajo
pendiente.

**Tope a las 15:00.** Ninguna barra se proyecta más allá del fin de jornada. Lo
que quede pendiente sigue en `min_restantes`, pero estirar la barra hasta la
madrugada prometía trabajo cuando ya no hay nadie en planta.

### Montaje de utillaje: preparar no es fabricar

`Ordenes_Bonos_Lineas.IdOperacion` dice de qué tipo es el fichaje, según la
tabla `Operaciones`: **0 = Funcionamiento normal, 1 = Montaje utillaje,
2 = Desmontaje**. Cada línea tiene un solo tipo (54.619 de 54.619), así que el
montaje llega como una línea aparte de la producción.

No es una etiqueta cosmética. En 18 meses de bonos cerrados:

| Lote | Bonos | Montaje | Producción | **% montaje** |
|---|---|---|---|---|
| 1-5 piezas | 302 | 22,1 min | 5,8 min | **86,4 %** |
| 6-50 | 1.568 | 26,1 min | 25,1 min | **55,8 %** |
| 51-500 | 3.378 | 32,7 min | 85,2 min | 31,1 % |
| +500 | 2.408 | 60,8 min | 374,3 min | 17,8 % |

En total son 5.127 h de montaje frente a 28.915 de producción — el 15 %. Y las
4.753 líneas de montaje declaran **cero piezas**, sin excepción.

De ahí salen dos cosas:

- **Un montaje abierto no se estima por piezas**, porque no produce ninguna. Se
  proyecta con lo que suele tardarse en montar esa máquina (`_SQL_MONTAJE`, por
  matrícula con respaldo a trabajo y una media global de 31 min). Varía mucho:
  la 107 monta en 11 min y la 001 en 113. Sin esto, la barra de un operario
  preparando la 001 se estiraba hasta las 20:08 con el tiempo de fabricar el
  bono entero.
- **Montaje y producción se pintan como una sola barra**, con el tramo de
  preparación marcado dentro (rayado vertical y un corte donde acaba). Solo se
  funden si van pegadas (≤ 2 min), que es el 86 % de los casos: 4.091 de 4.749.
  Si el montaje fue otro día son trabajos separados de verdad y se quedan como
  dos barras, la de montaje con el glifo `⚙`.

### La jornada son 480 minutos, no 540

`JORNADA_FIN` estuvo en las 16:00 y la jornada real acaba a las **15:00**.
Medido sobre 6 meses de fichajes: el último cierre del día es a las 15:01 en 38
días, 15:02 en 17, 15:00 en 12 y 15:03 en 10 — 77 de 110. Por minuto, las 15:00
concentran 506 cierres y las 16:00 solo 98.

Con 16:00 la app creía que el día tenía 540 minutos: un **12,5 % de capacidad
inflada** en toda proyección. `JORNADA_FIN` (`api.py`) y `WORK_FIN` (`app.js`)
tienen que coincidir, o las barras se pintan en el píxel equivocado.

### La cola: lo que cada operario tiene por delante

Detrás de lo que está haciendo ahora, el Gantt pinta en punteado los bonos que
tiene **asignados y aún sin empezar**, uno detrás de otro.

La asignación operario↔bono vive en **`Pers_EmpleadosOrdenBono`** (Orden, Bono
→ IdEmpleado), y se lee por la vista del ERP **`persV_DatosAsociadoEmpleado`**,
que ya trae resuelto todo lo que hace falta para una barra: máquina, área,
artículo, piezas objetivo, piezas hechas y la posición manual
(`Conf_OrdenesBonos.ordenar`).

> Ojo: `Ordenes_Bonos.IdEmpleado` —donde parecería que debe estar la
> asignación— está a **NULL en los 562 bonos abiertos**. No es ahí.

Medido: 19.517 asignaciones, 25 empleados, **238 de los 562 bonos abiertos**
con operario. Se toman solo los de `IdEstado = 0` (sin arrancar); los de estado
1 ya salen como barras reales de su propio fichaje.

**Cómo se encadena.** Cada bono empieza cuando el recurso queda libre —después
de la barra en curso— y dura lo que falta por fabricar
(`pendientes × min/pieza`, la misma cadena de siempre). Todo dentro de la
jornada **07:00–15:00 y saltando fines de semana**, y se corta en cuanto la
cola se sale de la ventana visible.

### El semáforo: la cola se reordena sola

El orden **no** es la secuencia manual a secas. Manda primero el semáforo del
ERP, y solo dentro de cada grupo ordena la secuencia (`Conf_OrdenesBonos.ordenar`;
los que no la tienen, detrás por número de orden).

El motivo es de taller: **el 40 % de la cola asignada está en rojo** (86 filas
de 215). Un operario que no puede seguir el orden establecido —falta material,
lo tiene cogido otro— abre otro bono por su cuenta. Un plan que ignore eso es
ficción. Poniendo delante lo que sí puede hacer, la cola **se reordena sola**:
cuando llega el material el bono pasa a verde en el ERP y sube de posición sin
que nadie replanifique nada. La secuencia manual no se pierde, sigue mandando
*dentro* de lo viable.

El color sale de **`dbo.persFTrazaordenesOperariosColor(idorden, idbono, idempleado)`**,
la misma función que alimenta el semáforo del programa de producción. Se invoca
con `OUTER APPLY` sobre nuestra consulta, **no** por la vista
`PersVTrazaordenesOperarios`, porque esa vista reescribe `ordenar = 0` como
**999** y ese valor se colaría como posición real, adelantando los bonos sin
secuencia a los que sí la tienen.

| RGB devuelto | color | significa                              |
|--------------|-------|----------------------------------------|
| `000204051`  | verde | ese operario puede trabajarlo ahora    |
| `255051051`  | rojo  | lo tiene asignado pero no puede        |
| `153255255`  | azul  | lo está trabajando (no llega a la cola)|

> **Ojo, "bloqueado" son dos cosas distintas y disjuntas.** El rojo del semáforo
> (86 filas) **no** es `Ordenes_Bonos.IdEstado = 3` (429 bonos): el solapamiento
> medido es **cero**. Los de `IdEstado = 3` no aparecen en ninguna parte, porque
> `persV_DatosAsociadoEmpleado` filtra `IdEstado NOT IN ('-1','3','2')`. Decisión
> tomada: no se muestran.

**Coste y caché.** La función se evalúa fila a fila: la consulta pasa de 12 ms a
~360 ms, y `/api/items` de ~58 ms a ~730 ms. Por eso se cachea **120 s**, igual
que las estimaciones; si el ERP falla se reutiliza la caché caducada, porque es
mejor ordenar con colores de hace unos minutos que servir una cola que el
operario no puede seguir.

**Efecto secundario conocido:** al mandar el rojo al final, se sale de la ventana
visible. En vista de operarios se pintan 4 de 77 bloqueadas. Para el operario es
lo correcto —solo ve lo que puede hacer—; si algún día hace falta verlas todas,
lo suyo sería que no consumieran tiempo de cola en vez de intercalarlas.

La jornada se cuenta entera (480 min) sin descontar el descanso de 11:00–11:15
a propósito: el eje del Gantt tampoco lo comprime, lo pinta como una banda.
Descontarlo desalinearía las barras del eje.

Dos cosas heredadas del ERP que se ven en pantalla: **un bono puede tener más
de un operario** (197 con uno, 21 con dos, 4 con tres, 2 con cuatro), así que
aparece en las dos filas; y un bono en cola **sin tiempo estimado** recibe un
bloque nominal de 60 min para que no adelante a los que van detrás, marcado con
el aviso.

### Nombres que engañan en el ERP

| campo | qué es de verdad |
|---|---|
| `Ordenes_Bonos.CantidadTotal` | cantidad **objetivo** del bono |
| `Ordenes_Bonos_Salidas.Cantidad` | cantidad **objetivo** (la del Access original) |
| `Ordenes_Bonos_Salidas.CantidadTotal` | lo **ya fabricado** — al revés que la de arriba |
| `Trabajos_Fases.TpoMuerto` | el tiempo de **montaje** (relleno en 4 filas de 6.502) |
| `Ordenes_Bonos_Lineas.Fecha` | cuándo se **grabó** la línea, no cuándo se trabajó |

### El gran pero: casi nadie declara piezas

**Solo 11 de 565 bonos abiertos tienen piezas declaradas** (2 %). Sin ese dato
no hay forma de saber lo avanzado, así que el cálculo cae al criterio de
minutos y cada barra dice en `base_estimacion` cuál se usó:

| `base_estimacion` | cuándo | qué hace |
|---|---|---|
| `piezas` | el bono declara piezas | `pendientes × min/pieza`, y compara ritmos |
| `minutos` | no las declara | `presupuesto − gastado`, como respaldo |

Mientras producción no declare piezas al cerrar cada línea, esto no puede ser
mejor. Y ojo: en el bono que sí las declara, las 360 piezas están en 3 de sus
26 líneas — la declaración es esporádica, así que "pendientes" es un techo.

**Cobertura del `min/pieza`**, medida el 2026-09-04 sobre los 570 bonos
abiertos:

| fuente | cobertura |
|---|---|
| Escandallo (`Trabajos_ManoObra`) | 9 bonos — **1,6 %** |
| Campos `MediaCon`/`MediaBonoCon`… de `Ordenes_Bonos` | **0–1,8 %** (vacíos) |
| Media calculada del histórico de líneas | los 13 bonos del día |

Los campos de media que ya trae el ERP están vacíos, por eso la media no se
lee: se calcula. Dos cosas más que conviene saber:

- La **media por máquina** es gruesa (una máquina hace piezas muy distintas),
  pero es el último escalón antes del aviso.
- Con la cadena llegando hasta máquina **el aviso casi nunca salta**: ningún
  caso en los últimos 25 días laborables.

**Líneas fantasma.** El ERP tiene líneas abiertas que nadie cerró hace meses.
Contarlas hasta hoy disparaba el consumo (medido: 3.383 min en un bono de unas
horas) e inflaba el recuento de operarios activos. Una línea abierta solo
cuenta si empezó en las últimas 24 h.

**Un dato que el ERP no tiene.** `Ordenes_Bonos.IdEmpleado` —el operario al que
está asignado el bono— está a `NULL` en los 565 bonos abiertos, y
`VOrdenes_Bonos_Lineas_Emp.PorcentajeTrabajo` a 0. La asignación existe de
hecho (cada bono lo ficha un solo operario, y `Operarios` = 1) pero no como
dato, así que la cantidad del bono **es** la cantidad de su operario.

**`POST /api/refrescar`** (+ `GET /api/refrescar/{id}`) — ya no hay ETL que
lanzar, los datos son del ERP en vivo. Responden `COMPLETED` al momento para
que el botón "Actualizar" siga funcionando: recargar y ya.

**`GET /api/lineas?dia=YYYY-MM-DD`** — la consulta en crudo. No la usa el
Gantt, pero es el sitio donde mirar qué está devolviendo el ERP.

### Zona horaria

El frontend manda la ventana como `days[0].toISOString()`: medianoche
**local** escrita en UTC. Por eso el contenedor fija `TZ=Europe/Madrid` y
`_dia_local()` la devuelve a hora local antes de quedarse con el día. Sin
esas dos cosas se pierde un día entero.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Es un smoke test: comprueba que la app importa y publica las rutas que llama
el frontend, sin tocar el ERP. Las consultas se validan contra el ERP real, a
mano.
