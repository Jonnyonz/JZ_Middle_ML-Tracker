-- Cola de avisos (notificaciones) de Mercado Libre. Cada aviso dice "cambio tal recurso de tal cuenta";
-- el procesamiento consulta el recurso en ML. Mientras un aviso espera, los repetidos del mismo recurso
-- se juntan en la misma fila (indice unico parcial sobre PENDIENTE).

CREATE TABLE IF NOT EXISTS ml_notifications (
    id BIGSERIAL PRIMARY KEY,
    topic VARCHAR(40) NOT NULL,
    resource VARCHAR(200) NOT NULL,
    user_id BIGINT NOT NULL,
    status VARCHAR(12) NOT NULL DEFAULT 'PENDIENTE'
        CHECK (status IN ('PENDIENTE', 'PROCESANDO', 'HECHA', 'DESCARTADA', 'ERROR')),
    received_count INT NOT NULL DEFAULT 1,
    attempts INT NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error TEXT,
    result TEXT,
    received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    taken_at TIMESTAMPTZ,
    processed_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS ml_notifications_pendiente_uq
    ON ml_notifications (topic, resource, user_id) WHERE status = 'PENDIENTE';
CREATE INDEX IF NOT EXISTS ml_notifications_cola_idx ON ml_notifications (next_attempt_at) WHERE status = 'PENDIENTE';
CREATE INDEX IF NOT EXISTS ml_notifications_estado_idx ON ml_notifications (status, processed_at);
