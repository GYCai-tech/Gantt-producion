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
    api.py           # una ruta JSON: "/api/lineas"
templates/index.html # la página entera
static/css/app.css   # estilos del Gantt
static/js/app.js     # el render, vanilla JS sin build step
```

**El ERP es solo lectura.** La app nunca escribe en `GOMEZYCRESPO`.

Se lee del ERP en vivo y no de la réplica analítica `gyc_analytics` porque
`Ordenes_Bonos_Lineas` —el detalle línea a línea de cada bono— no está
replicado en PostgreSQL. De paso, se evitan los desfases del ETL.

### `GET /api/lineas?dia=YYYY-MM-DD&estado=1`

Por defecto, hoy y estado 1. Devuelve `{dia, ahora, total, lineas[]}`. Cada
línea trae además tres campos derivados que consume el frontend:

| campo     | qué es                                                        |
|-----------|---------------------------------------------------------------|
| `empleado`| `Nombre + Apellidos`, la etiqueta de la fila                   |
| `inicio`  | `Hinicial`, o `Fecha` si el ERP no tiene hora de inicio        |
| `abierta` | `Hfinal IS NULL`: la línea sigue en curso                      |

### Frontend

- Una fila por operario; una barra por línea, posicionada en porcentaje sobre
  la ventana del día (no hay scroll horizontal ni zoom).
- La ventana es 07:00–16:00 y **se ensancha sola** si hay trabajo fuera.
- Si un operario tiene líneas solapadas, cada una baja a un carril libre y la
  fila crece; ninguna barra tapa a otra.
- Las líneas sin `Hfinal` van rayadas y con el borde latiendo: se estiran hasta
  ahora si el día es hoy, y hasta el final del día si es un día pasado (el ERP
  nunca las cerró y no sabemos cuándo acabaron; el tooltip lo dice).
- Color por matrícula: la misma máquina siempre del mismo color.
- Mirando hoy, refresca solo cada 60 s (y no lo hace con la pestaña en fondo).

Las horas del ERP llegan sin zona horaria (`2026-09-04T07:54:00`) y se parsean
a mano en JS en vez de con `new Date(s)`: son horas de fábrica y no deben
reinterpretarse como UTC.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

Es un smoke test: comprueba que la app importa y registra sus rutas sin tocar
el ERP. Las consultas se validan contra el ERP real, a mano.
