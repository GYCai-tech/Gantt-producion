-- 003 · Vista de asignaciones por empleado para el Gantt
-- ---------------------------------------------------------------------------
-- Enriquece core.fact_asignaciones_empleado (1 fila por empleado × orden × bono)
-- para pintar en el Gantt las TRES fases de cada operario:
--   · TRABAJADO  (COMPLETADO)            → barra pasada con inicio/fin reales
--   · EN_CURSO   (EN_CURSO / ACTIVADO)   → ya lo cubre v_estado_bonos_activos
--   · PROGRAMADO (PENDIENTE / BLOQUEADO) → cola futura (sin horas reales)
--
-- `min_estimados`: como fact_bonos.min_estimados crudo es 0, se estima con la
-- media histórica de min/pieza (artículo+operación; si falta, sólo operación)
-- por la cantidad objetivo. Misma lógica que analytics.v_tiempos_orden_bono,
-- con respaldo a nivel de operación para cubrir los bonos aún sin histórico.
-- ---------------------------------------------------------------------------

CREATE OR REPLACE VIEW analytics.v_asignaciones_empleado AS
WITH hist_art_op AS (   -- min/pieza por artículo + operación (igual que v_tiempos_orden_bono)
        SELECT idarticulo::text AS idarticulo,
               operacion,
               avg(min_reales / NULLIF(cantidad_objetivo, 0)) AS mpp
        FROM core.fact_bonos
        WHERE estado_orden = 2 AND cantidad_objetivo > 0 AND min_reales > 0
        GROUP BY idarticulo, operacion
     ),
     hist_op AS (       -- respaldo: min/pieza sólo por operación
        SELECT operacion,
               avg(min_reales / NULLIF(cantidad_objetivo, 0)) AS mpp
        FROM core.fact_bonos
        WHERE estado_orden = 2 AND cantidad_objetivo > 0 AND min_reales > 0
        GROUP BY operacion
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
    -- Duración estimada (min): artículo+op → op → NULL (el endpoint aplica fallback).
    round(COALESCE(
        NULLIF(hao.mpp, 0) * NULLIF(a.cantidad_objetivo, 0),
        NULLIF(ho.mpp,  0) * NULLIF(a.cantidad_objetivo, 0)
    )) AS min_estimados
FROM core.fact_asignaciones_empleado a
LEFT JOIN core.fact_bonos b
       ON b.idorden = a.idorden AND b.idbono = a.idbono
LEFT JOIN hist_art_op hao
       ON hao.idarticulo = a.idarticulo AND hao.operacion = a.operacion
LEFT JOIN hist_op ho
       ON ho.operacion = a.operacion
WHERE a.situacion <> 'ANULADO';

COMMENT ON VIEW analytics.v_asignaciones_empleado IS
    'Asignaciones de bonos por empleado con fase (TRABAJADO/EN_CURSO/PROGRAMADO), '
    'tiempos reales del bono y duración estimada. Alimenta el Gantt del Planificador.';
