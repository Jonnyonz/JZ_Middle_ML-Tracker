-- Cuentas de Mercado Libre autorizadas (varias por cliente) y autorizaciones en curso (OAuth con PKCE).

CREATE TABLE IF NOT EXISTS ml_accounts (
    user_id BIGINT PRIMARY KEY,                 -- id de la cuenta en Mercado Libre
    nickname VARCHAR(100) NOT NULL DEFAULT '',
    site_id VARCHAR(10) NOT NULL DEFAULT '',
    client_id VARCHAR(30) NOT NULL,             -- aplicacion de ML con la que se autorizo
    access_token TEXT NOT NULL,
    refresh_token TEXT,                         -- de un solo uso: cada renovacion trae uno nuevo
    expires_at TIMESTAMPTZ NOT NULL,
    scope TEXT NOT NULL DEFAULT '',
    status VARCHAR(20) NOT NULL DEFAULT 'ACTIVA' CHECK (status IN ('ACTIVA', 'RECONECTAR')),
    last_error TEXT,
    connected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    refreshed_at TIMESTAMPTZ
);

-- state de cada autorizacion iniciada desde la pagina: un solo uso, vence a los 10 minutos.
CREATE TABLE IF NOT EXISTS ml_oauth_states (
    state VARCHAR(100) PRIMARY KEY,
    code_verifier VARCHAR(200) NOT NULL,
    client_id VARCHAR(30) NOT NULL,
    username VARCHAR(50) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
