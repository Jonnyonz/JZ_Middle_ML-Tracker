"""Configuracion que se carga desde la pagina: conexion con Tracker360 y aplicacion de Mercado Libre.
Las claves se guardan en la base y nunca se devuelven: la pagina solo ve si estan cargadas."""

import json
import logging
from typing import Optional

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from jzmiddle import config, tracker
from jzmiddle.auth import client_ip, registrar, require_admin
from jzmiddle.db import get_conn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/config", tags=["Config"])


async def leer(conn: asyncpg.Connection, clave: str, defecto: str = "") -> str:
    return await conn.fetchval("SELECT value FROM settings WHERE key = $1", clave) or defecto


async def guardar(conn: asyncpg.Connection, clave: str, valor: str) -> None:
    await conn.execute("""
        INSERT INTO settings (key, value, updated_at) VALUES ($1, $2, now())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
    """, clave, valor)


class TrackerInput(BaseModel):
    url: str
    api_key: Optional[str] = None   # vacio = conservar la cargada


class MLInput(BaseModel):
    client_id: str
    client_secret: Optional[str] = None   # vacio = conservar la cargada


async def _estado(conn: asyncpg.Connection) -> dict:
    tracker_url = await leer(conn, "tracker_url")
    canal = json.loads(await leer(conn, "tracker_canal", "null"))
    ml_id = await leer(conn, "ml_client_id")
    cuentas = await conn.fetch("SELECT nickname, user_id, status, client_id FROM ml_accounts ORDER BY connected_at")
    return {
        "tracker": {"url": tracker_url, "api_key_set": bool(await leer(conn, "tracker_api_key")), "canal": canal},
        "ml": {"client_id": ml_id, "client_secret_set": bool(await leer(conn, "ml_client_secret")),
               "redirect_uri": f"{config.PUBLIC_URL}/ml/callback" if config.PUBLIC_URL else "",
               "notifications_url": f"{config.PUBLIC_URL}/ml/notifications" if config.PUBLIC_URL else ""},
        "public_url": config.PUBLIC_URL,
        "pendientes": [p for p, falta in (
            ("Cargar la dirección pública (PUBLIC_URL) en la configuración del servidor.", not config.PUBLIC_URL),
            ("La dirección pública tiene que ser https:// (Mercado Libre lo exige).",
             bool(config.PUBLIC_URL) and not config.PUBLIC_URL.startswith("https://")),
            ("Conectar con Tracker360 y probar la conexión.", canal is None),
            ("Cargar la aplicación de Mercado Libre (Client ID y Client Secret).",
             not (ml_id and await leer(conn, "ml_client_secret"))),
            ("Conectar al menos una cuenta de Mercado Libre.", not cuentas),
        ) if falta] + [
            f"La cuenta {c['nickname'] or c['user_id']} necesita reconectarse con Mercado Libre." for c in cuentas
            if c["status"] != "ACTIVA" or c["client_id"] != ml_id],
    }


@router.get("")
async def ver_configuracion(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_conn)):
    return await _estado(conn)


@router.put("/tracker")
async def guardar_tracker(data: TrackerInput, request: Request, admin: dict = Depends(require_admin),
                          conn: asyncpg.Connection = Depends(get_conn)):
    try:
        url = tracker.validar_url(data.url)
    except ValueError as e:
        raise HTTPException(400, str(e))
    clave = (data.api_key or "").strip()
    if not clave and not await leer(conn, "tracker_api_key"):
        raise HTTPException(400, "Falta la clave del canal (la da Tracker al crear el canal de venta).")
    async with conn.transaction():
        await guardar(conn, "tracker_url", url)
        if clave:
            await guardar(conn, "tracker_api_key", clave)
        # Cambio la conexion: hay que volver a probarla.
        await conn.execute("DELETE FROM settings WHERE key = 'tracker_canal'")
        await registrar(conn, admin["username"], "CONFIG_TRACKER", f"Conexión con Tracker: {url}" + (" (clave nueva)" if clave else ""), client_ip(request))
    return await _estado(conn)


@router.post("/tracker/test")
async def probar_tracker(request: Request, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_conn)):
    url, clave = await leer(conn, "tracker_url"), await leer(conn, "tracker_api_key")
    if not url or not clave:
        raise HTTPException(400, "Primero cargá la dirección y la clave de Tracker.")
    try:
        canal = await tracker.canal_actual(url, clave)
    except tracker.ErrorTracker as e:
        await conn.execute("DELETE FROM settings WHERE key = 'tracker_canal'")
        raise HTTPException(400, str(e))
    resumen = {"code": canal.get("code"), "name": canal.get("name"), "stock_mode": canal.get("stock_mode")}
    await guardar(conn, "tracker_canal", json.dumps(resumen))
    await registrar(conn, admin["username"], "CONFIG_TRACKER_OK", f"Conexión con Tracker probada: canal {resumen['code']}.", client_ip(request))
    return {"ok": True, "canal": resumen}


@router.put("/ml")
async def guardar_ml(data: MLInput, request: Request, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_conn)):
    client_id = data.client_id.strip()
    if not client_id.isdigit():
        raise HTTPException(400, "El Client ID de Mercado Libre es un número (el App ID de la aplicación).")
    secreto = (data.client_secret or "").strip()
    if not secreto and not await leer(conn, "ml_client_secret"):
        raise HTTPException(400, "Falta el Client Secret de la aplicación.")
    async with conn.transaction():
        await guardar(conn, "ml_client_id", client_id)
        if secreto:
            await guardar(conn, "ml_client_secret", secreto)
        await registrar(conn, admin["username"], "CONFIG_ML", f"Aplicación de Mercado Libre: {client_id}" + (" (secreto nuevo)" if secreto else ""), client_ip(request))
    return await _estado(conn)
