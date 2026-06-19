-- 004 · Dependencias reales bono→bono dentro de una misma orden
-- ---------------------------------------------------------------------------
-- Poblada por el ETL (gyc-etl/ETL-data, pipeline-produccion) cruzando, dentro
-- de cada orden, qué artículo PRODUCE cada bono (Fases_Salidas) contra qué
-- artículos CONSUME cada bono (Fases_Entradas), vía Ordenes_Bonos +
-- Trabajos_Fases. Ver query fuente:
--   gyc-etl/ETL-data: queries/produccion/dependencias_bono.sql
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS core.dependencias_bono (
    idorden                INTEGER         NOT NULL,
    idbono_dependiente     INTEGER         NOT NULL,
    idbono_requerido       INTEGER         NOT NULL,
    idarticulo             TEXT            NOT NULL,
    cargado_en             TIMESTAMP       DEFAULT NOW(),

    PRIMARY KEY (idorden, idbono_dependiente, idbono_requerido)
);

CREATE INDEX IF NOT EXISTS idx_dep_bono_requerido
    ON core.dependencias_bono (idorden, idbono_requerido);

COMMENT ON TABLE core.dependencias_bono IS
    'Dependencias reales bono->bono dentro de una misma orden: idbono_dependiente '
    'necesita que termine idbono_requerido porque consume (entrada de fase) el '
    'articulo que este produce (salida de fase). idarticulo es el articulo que '
    'conecta ambos bonos. Solo cubre ordenes activas/en espera (IdEstado IN (0,1)); '
    'se recalcula por completo (TRUNCATE + INSERT) cada 10 min junto con '
    'pipeline-produccion.';
