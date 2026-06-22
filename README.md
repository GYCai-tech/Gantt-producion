# Planificador de Producción (GYC)

Gantt de producción en tiempo real para GÓMEZ Y CRESPO: muestra qué está
trabajando cada operario/máquina ahora mismo, lo ya completado y una cola de
"programado" calculada al vuelo a partir de los datos del ERP replicados en
PostgreSQL. No hay edición manual del plan: todo se deriva de fichajes y
asignaciones reales.

## Arranque rápido

```bash
# entorno local
pip install -r requirements.txt
cp .env.example .env   # rellenar credenciales de PostgreSQL
uvicorn app.main:app --reload --port 8077

# con Docker
docker compose up -d --build   # expone 8001 -> 8000 dentro del contenedor
```

La home (`/`) es el Gantt. `/historico-produccion` es una segunda página
(ver estado más abajo, **actualmente rota**: llama a endpoints que no existen).

## Arquitectura

```
app/
  main.py            # crea la FastAPI app, monta /static, incluye los routers
  db.py              # engine SQLAlchemy (singleton) hacia PostgreSQL (psycopg2)
  routers/
    pages.py         # rutas HTML (Jinja2): "/" y "/historico-produccion"
    api.py           # API JSON: /api/grupos, /api/items, /api/refrescar...
templates/           # Jinja2: base.html, index.html, historico_prod.html
static/
  css/app.css        # estilos del Gantt
  js/app.js          # todo el motor de render del Gantt (sin build step, vanilla JS)
migrations/          # SQL versionado a mano, se ejecuta a mano contra la BD analítica
Dockerfile, docker-compose.yml, .github/workflows/docker-ci.yml
```

No hay ORM ni capa de modelos: cada endpoint de `api.py` escribe su propio SQL
con `sqlalchemy.text()` contra vistas/tablas ya preparadas por un ETL externo
(ver más abajo). No hay frontend con build (no React/Vite): `static/js/app.js`
es vanilla JS que pinta el Gantt a mano sobre `<div>`s posicionados con
`left`/`top` en píxeles.

### Backend (`app/`)

- **`app/db.py`**: una única función `get_engine()` que crea (y cachea en una
  global de módulo) el engine de SQLAlchemy a partir de las variables
  `PG_HOST/PG_DB/PG_USER/PG_PASS/PG_PORT` del `.env`.
- **`app/main.py`**: instancia FastAPI, registra un `JSONResponse` personalizado
  (`_JSONResponse`/`_Encoder`) para serializar `Decimal` y `date/datetime` sin
  que FastAPI reviente, monta `/static` y añade los dos routers.
- **`app/routers/pages.py`**: solo sirve plantillas, sin lógica.
- **`app/routers/api.py`**: el núcleo de la app. Expone:
  - `GET /api/grupos?vista=empleado|maquina` — las filas del Gantt (operarios
    visibles o máquinas), leyendo `core.dim_empleados` / `core.dim_maquinas`.
  - `GET /api/items?vista=...&desde=...&hasta=...` — las barras del Gantt.
    Combina en una sola respuesta:
    - bonos **en curso** (fichaje activo ahora),
    - bonos/sesiones **ya trabajados** (con datos reales de `core.fact_fichajes`),
    - bonos **programados** (cola futura, calculada con un scheduler propio).
  - `POST /api/refrescar` y `GET /api/refrescar/{flow_run_id}` — disparan y
    consultan bajo demanda un flujo de Prefect (el ETL que repuebla PostgreSQL
    desde el ERP). Requieren `PREFECT_API_URL`/`PREFECT_DEPLOYMENT_ID` en `.env`;
    si no están configurados, el botón "Actualizar" del frontend simplemente
    falla con un 503 controlado.

#### El scheduler de "programado"

`_planificar_programados()` es un *list scheduling* con cola de prioridad
(heap) que calcula inicio/fin de cada bono pendiente respetando dos cosas a
la vez:

1. la cola del recurso (un operario/máquina no puede hacer dos bonos a la vez,
   `next_start[recurso]` avanza con cada bono que se le asigna), y
2. las dependencias bono→bono reales (`core.dependencias_bono`, pobladas por
   el ETL cruzando qué bono *produce* una pieza que otro bono *consume*).

Máquinas y empleados se planifican **en la misma pasada** (namespaced con
prefijos `maq:`/`emp:`) aunque el Gantt solo pinte una vista a la vez, porque
una dependencia puede cruzar de máquina a operario o viceversa.

`add_work_minutes()` es el reloj de jornada laboral: avanza minutos solo
dentro de 7:00–11:00 y 11:15–16:00, saltando fines de semana, para que las
duraciones estimadas no incluyan horas fuera de turno.

`min_estimados` casi nunca viene relleno por el ERP, así que se reconstruye
con una media histórica de minutos/pieza (artículo+operación, con respaldo a
solo operación) multiplicada por la cantidad objetivo del bono.

### Frontend (`static/js/app.js`)

Una IIFE (`App`) sin dependencias ni build step:

- Pide `/api/grupos` (filas) y `/api/items` (barras) y los pinta a mano.
- El eje X solo representa **horas de trabajo** (7–16, descanso 11:00–11:15):
  `workX(fecha)` convierte una fecha real a píxeles saltándose huecos fuera
  de jornada.
- Carriles: cuando varias barras del mismo operario/máquina se solapan en el
  tiempo, un *greedy interval scheduling* las reparte en carriles paralelos
  dentro de la misma fila (ver comentario en `renderRows`: el orden de
  procesado tiene que ser cronológico por inicio, no por tipo, o un bono
  "real" actual desplaza de carril a sesiones pasadas del mismo bono que no
  se solapan).
- Auto-refresco: recarga `/api/items` cada 5 minutos y el reloj cada 30s; al
  entrar, si pasó más de `REFRESH_COOLDOWN_MIN` desde el último refresco,
  dispara `/api/refrescar` automáticamente.
- Tipos de barra (`it.tipo`) y su significado: `real` (en curso ahora),
  `trabajado` (sesión de fichaje cerrada, bono completado), `parcial`
  (sesión cerrada pero el bono sigue abierto — el operario fichó salida para
  un descanso/cambio de turno, no porque terminara), `programado` (cola
  futura, sin horas reales todavía).

`static/css/app.css` son los estilos del Gantt (colores por estado, layout de
barras/carriles). `static/css/bonos.css` y `static/js/bonos.js` se eliminaron
en la limpieza de este repo: pertenecían a una vista de tabla de "bonos
activos" anterior, no estaban enlazados desde ningún template y llamaban a un
endpoint (`/api/bonos-activos`) que ya no existe en el backend.

### Base de datos

La app **no escribe** en el ERP ni gestiona su propio modelo de datos de
negocio: todo el dato viene de un ETL externo (Prefect) que puebla una base
PostgreSQL analítica (`gyc_analytics`) con esquemas `core`/`analytics`. Este
repo solo aporta, en `migrations/`, los objetos que la propia app necesita
encima de esos esquemas:

| Archivo | Qué crea | Usado por la app hoy |
|---|---|---|
| `001_planning.sql` | `planning.programacion` — tabla para planificación manual por orden | No: el scheduler actual (`api.py`) calcula la cola en memoria en cada request, no persiste nada aquí. Parece un esquema de una iteración anterior (planificación manual) que quedó sin retirar. |
| `002_programacion_bono.sql` | Migra `planning.programacion` de unicidad por orden a `(idorden, idbono)` | Igual que arriba: sin uso actual conocido. |
| `003_asignaciones_empleado.sql` | Vista `analytics.v_asignaciones_empleado` | **Sí** — la consulta `GET /api/items?vista=empleado` y el filtro de operarios visibles en `GET /api/grupos`. |
| `004_dependencias_bono.sql` | Tabla `core.dependencias_bono` (poblada por el ETL, no por la app) | **Sí** — la lee `_cargar_dependencias()` para el scheduler. |

Antes de borrar `planning.programacion` en la BD real, confirmar con quien
mantiene el ETL que nada más la usa.

### Infra / despliegue

- **Dockerfile**: imagen `python:3.12-slim`, usuario no-root, healthcheck a
  `/`, arranca con `uvicorn app.main:app`.
- **docker-compose.yml**: un solo servicio, puerto host `8001` → contenedor
  `8000`, lee `.env`, con `extra_hosts: host.docker.internal` para llegar al
  PostgreSQL/Prefect que corren en la red local fuera del contenedor.
- **`.github/workflows/docker-ci.yml`**: en cada push a `main`, construye la
  imagen Docker (job `build-and-test`, runner de GitHub) y si pasa, despliega
  en un runner self-hosted con `git pull --ff-only && docker compose up -d --build`
  en `/home/goyco/apps/control_producion`. No hay tests automatizados reales
  (el job de "test" solo verifica que el build de Docker no rompe).

### Variables de entorno (`.env`, ver `.env.example`)

- `PG_HOST/PG_DB/PG_USER/PG_PASS/PG_PORT` — conexión a la BD analítica.
- `PREFECT_API_URL/PREFECT_DEPLOYMENT_ID/PREFECT_API_KEY` — opcionales, solo
  para el botón "Actualizar" (disparar el ETL bajo demanda).

## Estado conocido / deuda técnica

- **`/historico-produccion` está roto**: la plantilla (`templates/historico_prod.html`)
  llama a `GET /api/recursos?tipo=empleado`, `GET /api/historico/bonos` y
  `GET /api/historico/actividad-diaria`, pero ninguno de esos endpoints existe
  en `app/routers/api.py`. Es una página a medio terminar (o cuyos endpoints
  se borraron sin retirar el frontend) — antes de tocarla, decidir si se acaba
  de implementar el backend que falta o se retira la página.
- `planning.programacion` (migraciones 001/002) no lo usa ningún endpoint
  actual; ver tabla de arriba.

## Limpieza realizada en este repo

- `.venv/` (entorno virtual completo, ~3180 archivos) estaba versionado en
  git por error. Se dejó de rastrear y se añadió a `.gitignore`. **No se ha
  reescrito el historial de git**: los commits antiguos que lo incluían
  siguen ahí; si se quiere reducir el tamaño del repo de verdad haría falta
  una limpieza de historia aparte (p. ej. `git filter-repo`), que no se ha
  hecho porque reescribe SHAs y requiere coordinarlo con quien tenga clones.
- `__pycache__/`/`*.pyc` dejaron de rastrearse y se añadieron a `.gitignore`.
- Se borraron (no formaban parte del funcionamiento de la app):
  `scratch/` (scripts de debug puntuales contra órdenes concretas + logs de
  servidor), `test.py` (script sin relación con el proyecto), `Organizacion-proyectos/`
  (un informe HTML de Asana vs Jira ajeno a este repo), una `Nueva carpeta`
  vacía, y `static/js/bonos.js` + `static/css/bonos.css` (código muerto, ver
  más arriba).
