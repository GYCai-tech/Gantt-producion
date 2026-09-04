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
trabajo 07:00–16:00, descanso 11:00–11:15, carriles para bonos solapados,
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

| línea del ERP        | tipo         | se ve como            |
|----------------------|--------------|-----------------------|
| sin `Hfinal`         | `real`       | En curso (verde, late)|
| con `Hfinal`         | `trabajado`  | Completada (gris)     |

No hay `programado`: el ERP no dice nada de trabajo futuro y no se inventa,
así que el contador "en espera" del resumen marca siempre 0.

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
