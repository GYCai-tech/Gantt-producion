-- Esquema de planificación de producción
-- Ejecutar una sola vez contra gyc_analytics

CREATE SCHEMA IF NOT EXISTS planning;

CREATE TABLE IF NOT EXISTS planning.programacion (
    id              SERIAL          PRIMARY KEY,
    idorden         VARCHAR(50)     NOT NULL UNIQUE,
    recurso_tipo    VARCHAR(20)     NOT NULL CHECK (recurso_tipo IN ('maquina', 'empleado')),
    recurso_id      VARCHAR(50)     NOT NULL,
    start_planned   TIMESTAMP       NOT NULL,
    end_planned     TIMESTAMP       NOT NULL,
    notas           TEXT,
    creado_en       TIMESTAMP       DEFAULT NOW(),
    actualizado_en  TIMESTAMP       DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_programacion_recurso
    ON planning.programacion (recurso_tipo, recurso_id);

CREATE INDEX IF NOT EXISTS idx_programacion_rango
    ON planning.programacion (start_planned, end_planned);

COMMENT ON TABLE planning.programacion IS
    'Planificación manual de órdenes. Una fila por orden programada. '
    'idorden referencia core.fact_ordenes.idorden (cast a text).';
