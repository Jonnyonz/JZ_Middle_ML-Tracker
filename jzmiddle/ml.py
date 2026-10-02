"""Cliente de Mercado Libre: autorizacion de cuentas (OAuth 2.0 con PKCE), canje y renovacion de tokens
y llamadas a la API con el token de cada cuenta.

Reglas de ML que importan aca:
- El access token dura 6 horas. El refresh token es de UN SOLO USO: cada renovacion devuelve uno nuevo y
  el anterior deja de servir. Por eso la renovacion de una cuenta se hace con la fila bloqueada
  (SELECT ... FOR UPDATE): dos renovaciones a la vez gastarian el mismo refresh token y la cuenta
  quedaria desconectada.
- Sin el permiso offline_access en la aplicacion no hay refresh token: al vencer hay que reconectar.
- invalid_grant al renovar = el vendedor quito el permiso o el refresh token ya no sirve: reconectar.
"""

import asyncio
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import logging
import secrets
from typing import Optional
from urllib.parse import urlencode

import asyncpg
import httpx

from jzmiddle import config

logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(20.0, connect=5.0)
STATE_MINUTOS = 10
MARGEN_LLAMADA = timedelta(minutes=5)      # al usar el token: renovar si vence en menos de esto
MARGEN_FONDO = timedelta(minutes=30)       # la tarea de fondo renueva lo que vence en menos de esto


class ErrorML(Exception):
    """Error apto para mostrar. reconectar=True: la autorizacion de la cuenta ya no sirve."""

    def __init__(self, mensaje: str, reconectar: bool = False):
        super().__init__(mensaje)
        self.reconectar = reconectar


async def _ajuste(conn: asyncpg.Connection, clave: str) -> str:
    return await conn.fetchval("SELECT value FROM settings WHERE key = $1", clave) or ""


def redirect_uri() -> str:
    return f"{config.PUBLIC_URL}/ml/callback"


def _challenge(verifier: str) -> str:
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


# --- Autorizacion de una cuenta ---
async def iniciar_autorizacion(conn: asyncpg.Connection, username: str) -> str:
    """Direccion de la pantalla de autorizacion de ML (el navegador va ahi y ML vuelve a /ml/callback)."""
    client_id = await _ajuste(conn, "ml_client_id")
    if not client_id or not await _ajuste(conn, "ml_client_secret"):
        raise ErrorML("Primero cargá la aplicación de Mercado Libre (Client ID y Client Secret).")
    if not config.PUBLIC_URL:
        raise ErrorML("Falta la dirección pública (PUBLIC_URL) en la configuración del servidor.")
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    await conn.execute("DELETE FROM ml_oauth_states WHERE created_at < now() - make_interval(mins => $1)", STATE_MINUTOS)
    await conn.execute("INSERT INTO ml_oauth_states (state, code_verifier, client_id, username) VALUES ($1, $2, $3, $4)",
                       state, verifier, client_id, username)
    return f"{config.ML_AUTH_URL}/authorization?" + urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri(), "state": state,
        "code_challenge": _challenge(verifier), "code_challenge_method": "S256"})


async def tomar_state(conn: asyncpg.Connection, state: str) -> Optional[asyncpg.Record]:
    """El state se usa una sola vez y vence a los 10 minutos."""
    if not state:
        return None
    return await conn.fetchrow("""
        DELETE FROM ml_oauth_states WHERE state = $1 AND created_at > now() - make_interval(mins => $2)
        RETURNING code_verifier, client_id, username
    """, state, STATE_MINUTOS)


async def _pedir_token(datos: dict) -> dict:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.post(config.ML_API_URL + "/oauth/token", data=datos, headers={"Accept": "application/json"})
    except httpx.HTTPError as e:
        logger.warning(f"[ML] /oauth/token: {e!r}")
        raise ErrorML("No se pudo conectar con Mercado Libre.")
    try:
        cuerpo = r.json()
    except ValueError:
        cuerpo = {}
    if r.status_code == 200 and isinstance(cuerpo, dict) and cuerpo.get("access_token"):
        return cuerpo
    error = cuerpo.get("error") if isinstance(cuerpo, dict) else None
    logger.warning(f"[ML] /oauth/token {datos.get('grant_type')}: {r.status_code} {error}")
    if error == "invalid_grant":
        raise ErrorML("Mercado Libre rechazó la autorización (vencida o revocada): reconectá la cuenta.", reconectar=True)
    if error in ("invalid_client", "unauthorized_client"):
        raise ErrorML("Mercado Libre rechazó el Client ID o el Client Secret de la aplicación.")
    raise ErrorML(f"Mercado Libre respondió {r.status_code} al pedir el token.")


async def _usuario(token: str) -> dict:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as c:
            r = await c.get(config.ML_API_URL + "/users/me", headers={"Authorization": f"Bearer {token}"})
    except httpx.HTTPError as e:
        logger.warning(f"[ML] /users/me: {e!r}")
        raise ErrorML("No se pudo conectar con Mercado Libre.")
    if r.status_code != 200:
        raise ErrorML(f"Mercado Libre respondió {r.status_code} al pedir los datos de la cuenta.")
    return r.json()


def _vence(tokens: dict) -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=int(tokens.get("expires_in") or 21600))


async def completar_autorizacion(conn: asyncpg.Connection, code: str, fila_state: asyncpg.Record) -> dict:
    """Canjea el code por los tokens y guarda (o actualiza) la cuenta."""
    client_id = await _ajuste(conn, "ml_client_id")
    if client_id != fila_state["client_id"]:
        raise ErrorML("La aplicación de Mercado Libre cambió durante la autorización: volvé a conectar la cuenta.")
    tokens = await _pedir_token({"grant_type": "authorization_code", "client_id": client_id,
                                 "client_secret": await _ajuste(conn, "ml_client_secret"), "code": code,
                                 "redirect_uri": redirect_uri(), "code_verifier": fila_state["code_verifier"]})
    yo = await _usuario(tokens["access_token"])
    user_id = int(yo.get("id") or tokens["user_id"])
    await conn.execute("""
        INSERT INTO ml_accounts (user_id, nickname, site_id, client_id, access_token, refresh_token, expires_at, scope)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
        ON CONFLICT (user_id) DO UPDATE SET
            nickname = EXCLUDED.nickname, site_id = EXCLUDED.site_id, client_id = EXCLUDED.client_id,
            access_token = EXCLUDED.access_token, refresh_token = EXCLUDED.refresh_token,
            expires_at = EXCLUDED.expires_at, scope = EXCLUDED.scope, status = 'ACTIVA', last_error = NULL,
            connected_at = now(), refreshed_at = NULL
    """, user_id, str(yo.get("nickname") or "")[:100], str(yo.get("site_id") or "")[:10], client_id,
        tokens["access_token"], tokens.get("refresh_token"), _vence(tokens), str(tokens.get("scope") or ""))
    return {"user_id": user_id, "nickname": yo.get("nickname") or ""}


# --- Renovacion ---
async def renovar(conn: asyncpg.Connection, user_id: int, margen: timedelta = MARGEN_LLAMADA,
                  token_fallido: Optional[str] = None) -> str:
    """Devuelve un access token vigente. Lo renueva si vence dentro de `margen` o, con `token_fallido`
    (ML lo rechazo), si sigue siendo el guardado. Con la fila bloqueada: si otra tarea ya lo renovo
    mientras se esperaba el bloqueo, devuelve el nuevo sin gastar otro refresh token."""
    error: Optional[ErrorML] = None
    async with conn.transaction():
        fila = await conn.fetchrow("SELECT * FROM ml_accounts WHERE user_id = $1 FOR UPDATE", user_id)
        if not fila:
            raise ErrorML("La cuenta de Mercado Libre no está conectada.")
        if fila["status"] != "ACTIVA":
            raise ErrorML("La cuenta necesita reconectarse con Mercado Libre.", reconectar=True)
        if token_fallido is not None:
            if fila["access_token"] != token_fallido:
                return fila["access_token"]
        elif fila["expires_at"] - datetime.now(timezone.utc) > margen:
            return fila["access_token"]
        if not fila["refresh_token"]:
            error = ErrorML("La aplicación de Mercado Libre no tiene el permiso offline_access: reconectá la cuenta.", reconectar=True)
        else:
            try:
                tokens = await _pedir_token({"grant_type": "refresh_token", "client_id": fila["client_id"],
                                             "client_secret": await _ajuste(conn, "ml_client_secret"),
                                             "refresh_token": fila["refresh_token"]})
            except ErrorML as e:
                error = e
            else:
                await conn.execute("""
                    UPDATE ml_accounts SET access_token = $2, refresh_token = $3, expires_at = $4,
                        scope = COALESCE(NULLIF($5, ''), scope), refreshed_at = now(), last_error = NULL
                    WHERE user_id = $1
                """, user_id, tokens["access_token"], tokens.get("refresh_token") or fila["refresh_token"],
                    _vence(tokens), str(tokens.get("scope") or ""))
                logger.info(f"[ML] Token renovado de la cuenta {user_id}.")
                return tokens["access_token"]
    # Fuera de la transaccion: el error queda registrado aunque la funcion termine con excepcion.
    await conn.execute("""
        UPDATE ml_accounts SET last_error = $2, status = CASE WHEN $3 THEN 'RECONECTAR' ELSE status END
        WHERE user_id = $1
    """, user_id, str(error), error.reconectar)
    raise error


async def llamar(conn: asyncpg.Connection, user_id: int, metodo: str, ruta: str, **kwargs) -> httpx.Response:
    """Llamada a la API de ML con el token de la cuenta. Si ML lo rechaza (401), lo renueva y reintenta una vez."""
    token = await renovar(conn, user_id)
    extra = kwargs.pop("headers", None) or {}
    for intento in range(2):
        try:
            async with httpx.AsyncClient(base_url=config.ML_API_URL, timeout=TIMEOUT) as c:
                r = await c.request(metodo, ruta, headers={**extra, "Authorization": f"Bearer {token}"}, **kwargs)
        except httpx.HTTPError as e:
            logger.warning(f"[ML] {metodo} {ruta}: {e!r}")
            raise ErrorML("No se pudo conectar con Mercado Libre.")
        if r.status_code != 401 or intento == 1:
            return r
        token = await renovar(conn, user_id, token_fallido=token)
    return r


async def desconectar(conn: asyncpg.Connection, user_id: int) -> None:
    """Le pide a ML que revoque la autorizacion (si se puede) y borra la cuenta."""
    fila = await conn.fetchrow("SELECT client_id, access_token FROM ml_accounts WHERE user_id = $1", user_id)
    if not fila:
        return
    try:
        async with httpx.AsyncClient(base_url=config.ML_API_URL, timeout=TIMEOUT) as c:
            r = await c.delete(f"/users/{user_id}/applications/{fila['client_id']}",
                               headers={"Authorization": f"Bearer {fila['access_token']}"})
        if r.status_code >= 400:
            logger.info(f"[ML] Revocar la cuenta {user_id}: {r.status_code} (se borra igual).")
    except httpx.HTTPError as e:
        logger.info(f"[ML] Revocar la cuenta {user_id}: {e!r} (se borra igual).")
    await conn.execute("DELETE FROM ml_accounts WHERE user_id = $1", user_id)


async def renovar_proximas(pool: asyncpg.Pool) -> int:
    """Renueva los tokens que vencen pronto (asi el refresh token tambien se mantiene vivo)."""
    async with pool.acquire() as conn:
        cuentas = await conn.fetch("""
            SELECT user_id FROM ml_accounts
            WHERE status = 'ACTIVA' AND refresh_token IS NOT NULL AND expires_at < now() + $1::interval
        """, MARGEN_FONDO)
    renovadas = 0
    for c in cuentas:
        async with pool.acquire() as conn:
            try:
                await renovar(conn, c["user_id"], margen=MARGEN_FONDO)
                renovadas += 1
            except ErrorML as e:
                logger.warning(f"[ML] No se pudo renovar la cuenta {c['user_id']}: {e}")
    return renovadas


async def renovacion_en_segundo_plano(pool_de) -> None:
    """Tarea de fondo: cada ML_RENOVAR_SEGUNDOS renueva los tokens que estan por vencer."""
    while True:
        await asyncio.sleep(max(1, config.ML_RENOVAR_SEGUNDOS))
        pool = pool_de()
        if pool is None:
            continue
        try:
            await renovar_proximas(pool)
        except Exception as e:   # la tarea de fondo no se corta por un error puntual
            logger.error(f"[ML] Renovacion de tokens: {e!r}")
