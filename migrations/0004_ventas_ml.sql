-- Ventas de Mercado Libre y su pedido en Tracker. ref = numero de carrito (pack) o de orden de ML: es la
-- referencia externa del pedido en Tracker. Un carrito es UN pedido con un solo envio.

CREATE TABLE IF NOT EXISTS ml_ventas (
    ref VARCHAR(40) PRIMARY KEY,
    user_id BIGINT NOT NULL,                  -- cuenta de ML
    pack_id BIGINT,
    order_ids BIGINT[] NOT NULL,
    shipment_id BIGINT,
    logistic_type VARCHAR(40),
    ml_status VARCHAR(30),
    estado VARCHAR(20) NOT NULL CHECK (estado IN ('ESPERANDO_PAGO', 'CARGADA', 'CANCELADA', 'FULL')),
    tracker_number VARCHAR(40),               -- numero del pedido en Tracker
    label_sent_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ml_ventas_shipment_idx ON ml_ventas (shipment_id);
