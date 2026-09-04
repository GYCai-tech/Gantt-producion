-- 006 · Media PONDERADA por piezas en la duración estimada de los bonos
-- ---------------------------------------------------------------------------
-- La estimación de min/pieza salía de avg(min_reales / cantidad_objetivo): la
-- media de los RATIOS de cada bono. Eso da el mismo peso a un bono de 1 pieza
-- que a uno de 500, y como en el bono corto casi todo lo que se mide es la
-- preparación de la máquina, el ratio se dispara y arrastra la media.
--
-- Lo correcto es la media PONDERADA por piezas: sum(minutos) / sum(piezas).
-- Es el mismo criterio que ya aplica Coste-MP (desglose.py, CTE tiempo_op),
-- donde está documentado y medido: allí la media simple inflaba un 15,9% sobre
-- 4.476 artículos.
--
-- Medido aquí sobre core.fact_bonos (4.804 pares artículo+operación):
--   · la media simple infla un 19,5% de media
--   · 864 pares se van más de un 5% por encima
--   · el peor caso (artículo 30809078, "pestañear y plegar") sale x65
--
-- Se redefine la vista COMPLETA porque CREATE OR REPLACE VIEW no permite
-- tocar sólo una expresión. Se parte de la definición VIVA, no de la de la
-- migración 005: estado_color se añadió a la vista fuera de migraciones y
-- copiar 005 lo habría borrado, rompiendo el semáforo del ERP en el Gantt.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW analytics.v_asignaciones_empleado AS
WITH hist_art_op AS (   -- min/pieza por artículo + operación
        SELECT idarticulo::text AS idarticulo,
               LOWER(operacion) AS operacion,
               sum(min_reales) / NULLIF(sum(cantidad_objetivo), 0) AS mpp
        FROM core.fact_bonos
        WHERE estado_orden = 2 AND cantidad_objetivo > 0 AND min_reales > 0
        GROUP BY idarticulo, LOWER(operacion)
     ),
     hist_op AS (       -- respaldo: min/pieza sólo por operación
        SELECT LOWER(operacion) AS operacion,
               sum(min_reales) / NULLIF(sum(cantidad_objetivo), 0) AS mpp
        FROM core.fact_bonos
        WHERE estado_orden = 2 AND cantidad_objetivo > 0 AND min_reales > 0
        GROUP BY LOWER(operacion)
     )
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
    -- Duración estimada (min) a partir del HISTÓRICO: artículo+op → op → NULL.
    -- Es sólo el respaldo: cuando el escandallo del ERP declara un tiempo
    -- teórico para el bono, el endpoint /api/items lo pisa (_aplicar_estandar).
    round(COALESCE(
        NULLIF(hao.mpp, 0) * NULLIF(a.cantidad_objetivo, 0),
        NULLIF(ho.mpp,  0) * NULLIF(a.cantidad_objetivo, 0)
    )) AS min_estimados,
    a.fecha_orden,
    a.ordenar,
    a.estado_color
FROM core.fact_asignaciones_empleado a
LEFT JOIN core.fact_bonos b
       ON b.idorden = a.idorden AND b.idbono = a.idbono
LEFT JOIN hist_art_op hao
       ON hao.idarticulo = a.idarticulo AND hao.operacion = LOWER(a.operacion)
LEFT JOIN hist_op ho
       ON ho.operacion = LOWER(a.operacion)
WHERE a.situacion <> 'ANULADO';

COMMENT ON VIEW analytics.v_asignaciones_empleado IS
    'Asignaciones de bonos por empleado con fase (TRABAJADO/EN_CURSO/PROGRAMADO), '
    'tiempos reales del bono, orden manual (ordenar), semáforo del ERP (estado_color) '
    'y duración estimada por media ponderada de piezas. Alimenta el Gantt del Planificador.';
