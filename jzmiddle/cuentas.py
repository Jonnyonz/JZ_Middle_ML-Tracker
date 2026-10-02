"""Cuentas de Mercado Libre del cliente (varias): conectar con OAuth, ver su estado, probar y desconectar.

/ml/callback es a donde vuelve el navegador desde Mercado Libre. Las cookies de la pagina son
SameSite=Strict y no viajan en esa vuelta (viene de otro sitio): la autorizacion se valida con el state,
que solo pudo crear un administrador desde la pagina, es de un solo uso y vence a los 10 minutos.
Los tokens nunca se devuelven."""

import logging

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from jzmiddle import avisos, ml, stock
from jzmiddle.auth import client_ip, registrar, require_admin
from jzmiddle.db import get_conn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ml/cuentas", tags=["Cuentas ML"])
callback_router = APIRouter(tags=["Cuentas ML"])


async def listar(conn: asyncpg.Connection) -> list:
    client_id = await conn.fetchval("SELECT value FROM settings WHERE key = 'ml_client_id'") or ""
    filas = await conn.fetch("""
        SELECT user_id, nickname, site_id, client_id, expires_at, scope, status, last_error, connected_at, refreshed_at
        FROM ml_accounts ORDER BY connected_at
    """)
    return [{
        "user_id": f["user_id"], "nickname": f["nickname"], "site_id": f["site_id"], "status": f["status"],
        "last_error": f["last_error"], "expires_at": f["expires_at"].isoformat(),
        "connected_at": f["connected_at"].isoformat(),
        "refreshed_at": f["refreshed_at"].isoformat() if f["refreshed_at"] else None,
        "offline_access": "offline_access" in f["scope"].split(),
        "otra_aplicacion": f["client_id"] != client_id,
    } for f in filas]


@router.get("")
async def ver_cuentas(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_conn)):
    return await listar(conn)


@router.post("/conectar")
async def conectar(request: Request, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_conn)):
    """Devuelve la direccion de Mercado Libre a la que va el navegador para autorizar una cuenta."""
    try:
        url = await ml.iniciar_autorizacion(conn, admin["username"])
    except ml.ErrorML as e:
        raise HTTPException(400, str(e))
    return {"url": url}


@router.post("/{user_id}/probar")
async def probar(user_id: int, request: Request, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_conn)):
    """Pide los datos de la cuenta a ML con su token (lo renueva si hace falta)."""
    if not await conn.fetchval("SELECT 1 FROM ml_accounts WHERE user_id = $1", user_id):
        raise HTTPException(404, "La cuenta no está conectada.")
    try:
        r = await ml.llamar(conn, user_id, "GET", "/users/me")
    except ml.ErrorML as e:
        raise HTTPException(400, str(e))
    if r.status_code != 200:
        detalle = f"Mercado Libre respondió {r.status_code} con el token de la cuenta."
        await conn.execute("UPDATE ml_accounts SET last_error = $2 WHERE user_id = $1", user_id, detalle)
        raise HTTPException(400, detalle)
    yo = r.json()
    await conn.execute("UPDATE ml_accounts SET nickname = $2, last_error = NULL WHERE user_id = $1",
                       user_id, str(yo.get("nickname") or "")[:100])
    return {"ok": True, "nickname": yo.get("nickname")}


@router.delete("/{user_id}")
async def desconectar(user_id: int, request: Request, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_conn)):
    nick = await conn.fetchval("SELECT nickname FROM ml_accounts WHERE user_id = $1", user_id)
    if nick is None:
        raise HTTPException(404, "La cuenta no está conectada.")
    await ml.desconectar(conn, user_id)
    await registrar(conn, admin["username"], "ML_CUENTA_DESCONECTADA", f"Cuenta {nick} ({user_id})", client_ip(request))
    return {"ok": True}


@callback_router.get("/ml/callback")
async def callback(request: Request, code: str = "", state: str = "", error: str = "",
                   conn: asyncpg.Connection = Depends(get_conn)):
    """Vuelta desde Mercado Libre: termina la autorizacion y lleva a la pagina con el resultado."""
    def al_panel(resultado: str) -> RedirectResponse:
        return RedirectResponse(f"/panel?ml={resultado}", status_code=303)

    fila = await ml.tomar_state(conn, state[:100])
    if fila is None:
        return al_panel("vencida")
    if error or not code:
        return al_panel("cancelada")
    try:
        cuenta = await ml.completar_autorizacion(conn, code, fila)
    except ml.ErrorML as e:
        logger.warning(f"[ML] Autorizacion de cuenta fallida: {e}")
        await registrar(conn, fila["username"], "ML_CUENTA_ERROR", str(e), client_ip(request))
        return al_panel("error")
    await registrar(conn, fila["username"], "ML_CUENTA_CONECTADA", f"Cuenta {cuenta['nickname']} ({cuenta['user_id']})", client_ip(request))
    await avisos.reactivar_cuenta(conn, cuenta["user_id"])
    stock.pedir_conciliacion()   # sus publicaciones entran al indice y reciben el stock de Tracker
    return al_panel("conectada")
