"""Administradores de la pagina: alta inicial con SETUP_TOKEN, login con limite de intentos por IP +
usuario, sesiones opacas y CSRF de jztech_core (mismos criterios que Tracker360)."""

from datetime import datetime, timedelta, timezone
import logging
import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from jztech_core import sessions as core_sessions
from jztech_core.csrf import enforce_csrf, generate_csrf_token
from jztech_core.net import real_ip
from jztech_core.passwords import hash_password, needs_rehash, verify_password
from jztech_core.setup_flow import verify_setup_token
from pydantic import BaseModel

from jzmiddle import config
from jzmiddle.db import ConexionComoPool, get_conn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["Auth"])

CSRF_COOKIE = "csrf_token"
_CSRF_SUJETO = "jzmiddle-csrf"


def client_ip(request: Request) -> str:
    return real_ip(request, config.TRUSTED_PROXIES) if request.client else "Unknown"


async def registrar(conn: asyncpg.Connection, usuario: str, accion: str, detalle: str, ip: str = "") -> None:
    await conn.execute("INSERT INTO audit_log (username, action, details, ip) VALUES ($1, $2, $3, $4)",
                       usuario[:50], accion[:60], detalle, ip[:64])


def _ttl() -> timedelta:
    return timedelta(hours=max(1, config.SESSION_HOURS))


async def _iniciar_sesion(conn: asyncpg.Connection, response: Response, user_id) -> None:
    await conn.execute("DELETE FROM jztech_sessions WHERE expires_at <= now()")
    token = await core_sessions.create_session(ConexionComoPool(conn), str(user_id), _ttl())
    core_sessions.set_session_cookie(response, token, _ttl(), secure=config.COOKIES_SECURE)
    response.set_cookie(key=CSRF_COOKIE, value=generate_csrf_token(token, _CSRF_SUJETO), httponly=False,
                        secure=config.COOKIES_SECURE, samesite="strict", max_age=int(_ttl().total_seconds()))


async def require_admin(request: Request, conn: asyncpg.Connection = Depends(get_conn)) -> dict:
    """Sesion valida de un administrador; en POST/PUT/PATCH/DELETE exige ademas X-CSRF-Token."""
    token = request.cookies.get(core_sessions.SESSION_COOKIE_NAME)
    user_id = await core_sessions.verify_session(ConexionComoPool(conn), token) if token else None
    if not user_id:
        raise HTTPException(401, "Sesión expirada.")
    try:
        uid = uuid.UUID(user_id)
    except ValueError:
        raise HTTPException(401, "Sesión expirada.")
    user = await conn.fetchrow("SELECT id, username FROM admin_users WHERE id = $1", uid)
    if not user:
        raise HTTPException(401, "Sesión expirada.")
    await enforce_csrf(request, token, _CSRF_SUJETO)
    return dict(user)


# --- Limite de intentos de login (clave IP + usuario, se cuenta antes de verificar la clave) ---
async def _reservar_intento(conn: asyncpg.Connection, clave: str) -> None:
    fila = await conn.fetchrow("""
        INSERT INTO login_limits (clave, attempts) VALUES ($1, 1)
        ON CONFLICT (clave) DO UPDATE SET
            attempts = CASE WHEN login_limits.blocked_until <= now() THEN 1 ELSE login_limits.attempts + 1 END,
            blocked_until = CASE WHEN login_limits.blocked_until <= now() THEN NULL ELSE login_limits.blocked_until END
        RETURNING attempts, blocked_until
    """, clave)
    ahora = datetime.now(timezone.utc)
    bloqueo = fila["blocked_until"]
    if not bloqueo and fila["attempts"] > config.MAX_LOGIN_ATTEMPTS:
        bloqueo = ahora + timedelta(minutes=config.LOCKOUT_MINUTES)
        await conn.execute("UPDATE login_limits SET blocked_until = $2 WHERE clave = $1 AND blocked_until IS NULL", clave, bloqueo)
    if bloqueo and ahora < bloqueo:
        minutos = int((bloqueo - ahora).total_seconds() / 60) + 1
        raise HTTPException(429, f"Demasiados intentos fallidos. Bloqueado por {minutos} min.")


class SetupInput(BaseModel):
    token: str
    username: str
    password: str


class LoginInput(BaseModel):
    username: str
    password: str


@router.get("/setup/status")
async def setup_status(conn: asyncpg.Connection = Depends(get_conn)):
    return {"needs_setup": (await conn.fetchval("SELECT COUNT(*) FROM admin_users")) == 0}


@router.post("/setup/admin")
async def setup_admin(data: SetupInput, request: Request, response: Response, conn: asyncpg.Connection = Depends(get_conn)):
    if not verify_setup_token(config.SETUP_TOKEN, data.token):
        raise HTTPException(403, "Token de instalación inválido.")
    if await conn.fetchval("SELECT COUNT(*) FROM admin_users"):
        raise HTTPException(403, "La configuración inicial ya fue completada.")
    usuario = data.username.strip().lower()
    if not usuario or len(usuario) > 50:
        raise HTTPException(400, "Usuario inválido.")
    if len(data.password) < 10:
        raise HTTPException(400, "La contraseña debe tener al menos 10 caracteres.")
    hashed = hash_password(data.password)
    async with conn.transaction():
        # Dos altas simultaneas: la segunda espera y ve la primera.
        await conn.execute("SELECT pg_advisory_xact_lock(hashtext('jzmiddle_setup_admin'))")
        if await conn.fetchval("SELECT COUNT(*) FROM admin_users"):
            raise HTTPException(403, "La configuración inicial ya fue completada.")
        uid = await conn.fetchval("INSERT INTO admin_users (username, password_hash) VALUES ($1, $2) RETURNING id", usuario, hashed)
        await registrar(conn, usuario, "SETUP_ADMIN", "Administrador inicial creado.", client_ip(request))
    await _iniciar_sesion(conn, response, uid)
    return {"username": usuario}


@router.post("/login")
async def login(data: LoginInput, request: Request, response: Response, conn: asyncpg.Connection = Depends(get_conn)):
    usuario = data.username.strip().lower()
    ip = client_ip(request)
    user = await conn.fetchrow("SELECT id, username, password_hash FROM admin_users WHERE username = $1", usuario)
    clave_limite = f"{ip}|{user['id'] if user else usuario}"[:255]
    await _reservar_intento(conn, clave_limite)
    if not user or not verify_password(data.password, user["password_hash"]):
        await registrar(conn, usuario or "?", "LOGIN_FAILED", "Intento de acceso fallido.", ip)
        raise HTTPException(401, "Usuario o contraseña incorrectos.")
    await conn.execute("DELETE FROM login_limits WHERE clave = $1", clave_limite)
    if needs_rehash(user["password_hash"]):
        await conn.execute("UPDATE admin_users SET password_hash = $1 WHERE id = $2", hash_password(data.password), user["id"])
    await _iniciar_sesion(conn, response, user["id"])
    await registrar(conn, user["username"], "LOGIN", "Inicio de sesión.", ip)
    return {"username": user["username"]}


@router.post("/logout")
async def logout(request: Request, response: Response, conn: asyncpg.Connection = Depends(get_conn)):
    token = request.cookies.get(core_sessions.SESSION_COOKIE_NAME)
    if token:
        await core_sessions.revoke_session(ConexionComoPool(conn), token)
    core_sessions.clear_session_cookie(response, secure=config.COOKIES_SECURE)
    response.delete_cookie(CSRF_COOKIE, secure=config.COOKIES_SECURE, samesite="strict")
    return {"message": "Sesión cerrada."}


@router.get("/me")
async def me(admin: dict = Depends(require_admin)):
    return {"username": admin["username"]}
