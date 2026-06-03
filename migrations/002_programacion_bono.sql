-- Migración: la unidad de planificación pasa de ORDEN a BONO.
-- Añade idbono y cambia la unicidad a (idorden, idbono).
-- idbono = 0  →  planificación a nivel de orden (orden en espera sin bonos abiertos).

ALTER TABLE planning.programacion
    ADD COLUMN IF NOT EXISTS idbono INTEGER NOT NULL DEFAULT 0;

-- Sustituir la unicidad por orden con la unicidad por (orden, bono)
ALTER TABLE planning.programacion
    DROP CONSTRAINT IF EXISTS programacion_idorden_key;

DROP INDEX IF EXISTS planning.uq_programacion_orden_bono;
CREATE UNIQUE INDEX uq_programacion_orden_bono
    ON planning.programacion (idorden, idbono);

CREATE INDEX IF NOT EXISTS idx_programacion_bono
    ON planning.programacion (idorden, idbono);

COMMENT ON COLUMN planning.programacion.idbono IS
    'Bono planificado dentro de la orden. 0 = planificación a nivel de orden (sin bono concreto).';
