-- Publicaciones de Mercado Libre de las cuentas conectadas (una fila por publicacion sin variantes o por
-- variante, variation_id = 0 si no tiene) y SKU con stock por mandar a ML.

CREATE TABLE IF NOT EXISTS ml_publicaciones (
    item_id VARCHAR(30) NOT NULL,
    variation_id BIGINT NOT NULL DEFAULT 0,
    user_id BIGINT NOT NULL,
    sku VARCHAR(100),                         -- NULL: la publicacion no tiene SKU
    title TEXT NOT NULL DEFAULT '',
    status VARCHAR(20) NOT NULL DEFAULT '',
    sub_status TEXT NOT NULL DEFAULT '',
    logistic_type VARCHAR(30),
    ml_quantity INT,                          -- available_quantity en ML (ultima lectura o escritura)
    -- NULL = se sincroniza bien; SIN_SKU, SKU_NO_EN_TRACKER, FULL o ERROR = se informa en la pagina
    problema VARCHAR(20),
    last_error TEXT,
    sent_at TIMESTAMPTZ,
    seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (item_id, variation_id)
);
CREATE INDEX IF NOT EXISTS ml_publicaciones_sku_idx ON ml_publicaciones (UPPER(sku));
CREATE INDEX IF NOT EXISTS ml_publicaciones_user_idx ON ml_publicaciones (user_id);

CREATE TABLE IF NOT EXISTS ml_stock_pendiente (
    sku VARCHAR(100) PRIMARY KEY,             -- en mayusculas
    attempts INT NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
