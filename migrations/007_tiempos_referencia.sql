-- 007 · Tiempos de referencia con PREPARACIÓN: duración = setup + min/pieza × cantidad
-- ---------------------------------------------------------------------------
-- El modelo anterior era `min/pieza × cantidad`, sin término de preparación.
-- Eso no es un dato que falte: es la FÓRMULA equivocada. Medido sobre los
-- bonos ya cerrados con montaje declarado en el ERP, la preparación es:
--
--     bono de   1 pieza →  25 min de  38 = 65% del total
--     bono de   5 piezas →  26 min de  34 = 75%
--     bono de  10 piezas →  23 min de  68 = 34%
--     bono de 100 piezas →  41 min de 136 = 30%
--
-- Un bono corto es casi todo preparación. Meter eso dentro de un "min/pieza"
-- es lo que disparaba la dispersión del histórico (±59% de media) y lo que
-- hacía que la media simple de ratios inflara (ver migración 006).
--
-- Ajustar `min_reales = setup + mpp × cantidad` por mínimos cuadrados sobre el
-- histórico gana al modelo de una sola tasa en el 81% de los pares artículo+
-- operación con datos suficientes (151 de 187) y en 8 de las 9 operaciones
-- principales, bajando el error medio por bono un 18%.
--
-- Ejemplo real: artículo 11401039 "empaquetar" → 91,2 min + 1,544 min/pieza.
-- El error medio de estimación baja de 32,6 a 8,1 minutos por bono.
--
-- Esta vista es además el sitio único donde consultar el tiempo de referencia
-- de un proceso: la usan tanto /api/items (vista máquina) como
-- v_asignaciones_empleado (vista operarios), que antes duplicaban el cálculo
-- en cinco sitios distintos.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW analytics.v_tiempos_referencia AS
WITH base AS (
        -- Sólo bonos de órdenes CERRADAS: un bono en curso tiene minutos
        -- incompletos y hundiría la tasa.
        SELECT idarticulo::text  AS idarticulo,
               LOWER(operacion)  AS operacion,
               cantidad_objetivo,
               min_reales
        FROM core.fact_bonos
        WHERE estado_orden = 2
          AND cantidad_objetivo > 0
          AND min_reales > 0
     ),
     -- Ajuste por artículo + operación: lo más específico que hay.
     ajuste_art AS (
        SELECT idarticulo, operacion,
               count(*)                                            AS n,
               count(DISTINCT cantidad_objetivo)                   AS n_cantidades,
               regr_intercept(min_reales, cantidad_objetivo)       AS setup,
               regr_slope(min_reales, cantidad_objetivo)           AS mpp,
               sum(min_reales) / NULLIF(sum(cantidad_objetivo), 0) AS mpp_medio
        FROM base
        GROUP BY idarticulo, operacion
     ),
     -- Respaldo por operación, para artículos que nunca se han fabricado.
     ajuste_op AS (
        SELECT operacion,
               count(*)                                            AS n,
               count(DISTINCT cantidad_objetivo)                   AS n_cantidades,
               regr_intercept(min_reales, cantidad_objetivo)       AS setup,
               regr_slope(min_reales, cantidad_objetivo)           AS mpp,
               sum(min_reales) / NULLIF(sum(cantidad_objetivo), 0) AS mpp_medio
        FROM base
        GROUP BY operacion
     )
-- Nivel ARTÍCULO + OPERACIÓN
SELECT
    a.idarticulo,
    a.operacion,
    -- GUARDARRAÍLES del ajuste. Sin ellos, 108 de los 295 pares con datos dan
    -- un ajuste sin sentido físico (setup negativo o tasa <= 0): con pocos
    -- bonos, o con todos de la misma cantidad, la recta se apoya en nada.
    -- Cuando el ajuste no es de fiar se cae a la media ponderada de siempre,
    -- que no es mejor pero tampoco inventa una preparación negativa.
    CASE WHEN a.n >= 6 AND a.n_cantidades >= 3 AND a.setup >= 0 AND a.mpp > 0
         THEN round(a.setup::numeric, 2) ELSE 0 END          AS setup_min,
    CASE WHEN a.n >= 6 AND a.n_cantidades >= 3 AND a.setup >= 0 AND a.mpp > 0
         THEN round(a.mpp::numeric, 6)
         ELSE round(a.mpp_medio::numeric, 6) END             AS min_pieza,
    CASE WHEN a.n >= 6 AND a.n_cantidades >= 3 AND a.setup >= 0 AND a.mpp > 0
         THEN 'ajuste_articulo' ELSE 'media_articulo' END    AS origen,
    a.n                                                      AS n_bonos
FROM ajuste_art a
WHERE a.mpp_medio > 0

UNION ALL

-- Nivel OPERACIÓN: idarticulo NULL marca la fila de respaldo genérico.
SELECT
    NULL::text,
    o.operacion,
    CASE WHEN o.n >= 6 AND o.n_cantidades >= 3 AND o.setup >= 0 AND o.mpp > 0
         THEN round(o.setup::numeric, 2) ELSE 0 END,
    CASE WHEN o.n >= 6 AND o.n_cantidades >= 3 AND o.setup >= 0 AND o.mpp > 0
         THEN round(o.mpp::numeric, 6)
         ELSE round(o.mpp_medio::numeric, 6) END,
    CASE WHEN o.n >= 6 AND o.n_cantidades >= 3 AND o.setup >= 0 AND o.mpp > 0
         THEN 'ajuste_operacion' ELSE 'media_operacion' END,
    o.n
FROM ajuste_op o
WHERE o.mpp_medio > 0;

COMMENT ON VIEW analytics.v_tiempos_referencia IS
    'Tiempo de referencia por proceso: duracion = setup_min + min_pieza * cantidad. '
    'Ajustado por minimos cuadrados sobre el historico de bonos cerrados; cae a la '
    'media ponderada cuando el ajuste no supera los guardarrailes. Las filas con '
    'idarticulo NULL son el respaldo por operacion. Fuente unica para el Gantt.';


-- ---------------------------------------------------------------------------
-- La vista de operarios pasa a consumir los tiempos de referencia en vez de
-- calcular su propia media (que además era la media simple de ratios hasta la
-- migración 006). Se preserva estado_color, que se añadió fuera de migraciones.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW analytics.v_asignaciones_empleado AS
SELECT
    a.idempleado,
    a.nombre_empleado,
    a.departamento,
    a.idorden,
    a.idbono,
    a.operacion,
    a.idarticulo,
    a.articulo,
    a.cantidad_pedida,
    a.cantidad_objetivo,
    a.piezas_producidas,
    a.minutos_reales,
    a.estado_bono,
    a.estado_orden,
    a.situacion,
    a.fecha_prevista_fin,
    a.fecha_asignacion,
    a.fichaje_activo_desde,
    b.fecha_inicio_real,
    b.fecha_fin_real,
    CASE
        WHEN a.situacion = 'COMPLETADO'                THEN 'TRABAJADO'
        WHEN a.situacion IN ('EN_CURSO', 'ACTIVADO')   THEN 'EN_CURSO'
        WHEN a.situacion IN ('PENDIENTE', 'BLOQUEADO') THEN 'PROGRAMADO'
        ELSE 'OTRO'
    END AS fase,
    -- setup + min/pieza × cantidad. El COALESCE elige la fuente ENTERA
    -- (artículo o respaldo de operación), nunca mezcla el setup de una con la
    -- tasa de la otra. Sigue siendo sólo el respaldo: si el bono tiene tiempo
    -- teórico en el escandallo del ERP, /api/items lo pisa (_aplicar_estandar).
    round(COALESCE(
        ta.setup_min  + ta.min_pieza  * NULLIF(a.cantidad_objetivo, 0),
        tref_op.setup_min + tref_op.min_pieza * NULLIF(a.cantidad_objetivo, 0)
    )) AS min_estimados,
    a.fecha_orden,
    a.ordenar,
    a.estado_color,
    -- Al final a propósito: CREATE OR REPLACE VIEW sólo deja AÑADIR columnas
    -- por la cola, nunca insertarlas en medio ni renombrarlas.
    COALESCE(ta.origen, tref_op.origen) AS origen_referencia
FROM core.fact_asignaciones_empleado a
LEFT JOIN core.fact_bonos b
       ON b.idorden = a.idorden AND b.idbono = a.idbono
LEFT JOIN analytics.v_tiempos_referencia ta
       ON ta.idarticulo = a.idarticulo AND ta.operacion = LOWER(a.operacion)
LEFT JOIN analytics.v_tiempos_referencia tref_op
       ON tref_op.idarticulo IS NULL AND tref_op.operacion = LOWER(a.operacion)
WHERE a.situacion <> 'ANULADO';

COMMENT ON VIEW analytics.v_asignaciones_empleado IS
    'Asignaciones de bonos por empleado con fase (TRABAJADO/EN_CURSO/PROGRAMADO), '
    'tiempos reales del bono, orden manual (ordenar), semaforo del ERP (estado_color) '
    'y duracion estimada como setup + min/pieza * cantidad (analytics.v_tiempos_referencia). '
    'Alimenta el Gantt del Planificador.';
