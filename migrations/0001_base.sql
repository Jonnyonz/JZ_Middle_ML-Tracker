-- Esquema base del middleware: administradores de la pagina, sesiones, limite de login, ajustes y
-- registro de acciones. Las cuentas de Mercado Libre y la cola de trabajo van en migraciones siguientes.

CREATE TABLE IF NOT EXISTS admin_users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username VARCHAR(50) UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Sesiones opacas de jztech_core.sessions (en la base solo el hash del token).
CREATE TABLE IF NOT EXISTS jztech_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS jztech_sessions_user_id_idx ON jztech_sessions (user_id);

-- Intentos de login por IP + usuario (mismo criterio que Tracker360).
CREATE TABLE IF NOT EXISTS login_limits (
    clave VARCHAR(255) PRIMARY KEY,
    attempts INT NOT NULL DEFAULT 0,
    blocked_until TIMESTAMPTZ
);

-- Ajustes que se cargan desde la pagina (conexion con Tracker, aplicacion de Mercado Libre).
CREATE TABLE IF NOT EXISTS settings (
    key VARCHAR(100) PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_log (
    id BIGSERIAL PRIMARY KEY,
    username VARCHAR(50) NOT NULL,
    action VARCHAR(60) NOT NULL,
    details TEXT,
    ip VARCHAR(64),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
