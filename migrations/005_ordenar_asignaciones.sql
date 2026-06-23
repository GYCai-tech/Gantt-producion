-- 005 · Orden manual de bonos (Conf_OrdenesBonos.ordenar del ERP) en la cola del Gantt
-- ---------------------------------------------------------------------------
-- ordenar: posición manual de un bono DENTRO de su propia orden, fijada a mano
-- por el responsable de planta desde la propia aplicación del ERP (no es un
-- índice global -- se repite entre órdenes distintas). Hasta ahora solo vivía
-- en core.fact_bonos (ver 007_orden_manual_bonos.sql del ETL), que excluye
-- bonos sin fichaje todavía -- el mismo hueco que el bug de bonos bloqueados
-- en el Consultor de Bonos. Las columnas nuevas llegan via las consultas ERP
-- que ya alimentan fact_asignaciones_maquina/empleado (no dependen de
-- fichaje), así que cubren también los bonos en cola que nunca se ficharon.
-- ---------------------------------------------------------------------------

ALTER TABLE core.fact_asignaciones_maquina
    ADD COLUMN IF NOT EXISTS ordenar SMALLINT;

ALTER TABLE core.fact_asignaciones_empleado
    ADD COLUMN IF NOT EXISTS ordenar SMALLINT;

CREATE OR REPLACE VIEW analytics.v_asignaciones_empleado AS
WITH hist_art_op AS (   -- min/pieza por artículo + operación (igual que v_tiempos_orden_bono)
        SELECT idarticulo::text AS idarticulo,
               LOWER(operacion) AS operacion,
               avg(min_reales / NULLIF(cantidad_objetivo, 0)) AS mpp
        FROM core.fact_bonos
        WHERE estado_orden = 2 AND cantidad_objetivo > 0 AND min_reales > 0
        GROUP BY idarticulo, LOWER(operacion)
     ),
     hist_op AS (       -- respaldo: min/pieza sólo por operación
        SELECT LOWER(operacion) AS operacion,
               avg(min_reales / NULLIF(cantidad_objetivo, 0)) AS mpp
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
    -- Duración estimada (min): artículo+op → op → NULL (el endpoint aplica fallback).
    round(COALESCE(
        NULLIF(hao.mpp, 0) * NULLIF(a.cantidad_objetivo, 0),
        NULLIF(ho.mpp,  0) * NULLIF(a.cantidad_objetivo, 0)
    )) AS min_estimados,
    a.fecha_orden,
    a.ordenar
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
    'tiempos reales del bono, orden manual (ordenar) y duración estimada. '
    'Alimenta el Gantt del Planificador.';
