"""Avisos (notificaciones) de Mercado Libre: recepcion, cola y procesamiento en segundo plano.

ML manda un POST a <PUBLIC_URL>/ml/notifications por cada cambio (venta, envio, publicacion) y espera un
200 en menos de 500 ms; si no lo recibe, reintenta. Los avisos NO vienen firmados: solo se valida que
sean de nuestra aplicacion, de una cuenta conectada, de un tema que usamos y con un recurso bien formado,
y se encolan. El contenido nunca se cree: al procesarlo se consulta el recurso en ML con el token de la
cuenta (lo que ML no confirma, se descarta).

Mientras un aviso espera en la cola, los repetidos del mismo recurso se juntan (consultar el recurso trae
siempre su estado actual). Si llega otro mientras se esta procesando, queda uno nuevo pendiente.
El paso 3 registra en MANEJADORES lo que se hace con cada tema (pedidos en Tracker, etc.)."""

import asyncio
import ipaddress
import json
import logging
import re
from typing import Awaitable, Callable, Dict

import asyncpg
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from jzmiddle import config, ml
from jzmiddle.auth import client_ip, registrar, require_admin
from jzmiddle.db import get_conn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ml/avisos", tags=["Avisos ML"])
publico = APIRouter(tags=["Avisos ML"])

# Temas que hay que activar en la aplicacion de ML y forma de su recurso.
TEMAS = {
    "orders_v2": re.compile(r"/orders/\d{1,20}"),
    "shipments": re.compile(r"/shipments/\d{1,20}"),
    "items": re.compile(r"/items/[A-Z]{3}\d{1,20}"),
}
# Parametros de la consulta del recurso: sin include_attributes=all ML no manda el SKU de las variantes.
PARAMETROS = {"items": {"include_attributes": "all"}}
TAMANO_MAXIMO = 8192
LOTE = 20
COLGADO_MINUTOS = 10
RETENCION_DIAS = 30
_REEMPLAZADO = "Reemplazado por un aviso más nuevo del mismo recurso."

# tema -> async (conn, aviso, datos del recurso en ML) -> texto del resultado
MANEJADORES: Dict[str, Callable[[asyncpg.Connection, asyncpg.Record, dict], Awaitable[str]]] = {}


class ErrorAviso(Exception):
    """Lo levanta un manejador cuando hay que arreglar algo a mano (un SKU que falta, un pedido ya
    despachado): el aviso queda en ERROR con este mensaje y se reintenta desde la pagina."""


class Reintentar(Exception):
    """Lo levanta un manejador ante un problema pasajero (Tracker o ML caidos): se reintenta solo."""

_despertar = asyncio.Event()


def despertar() -> None:
    _despertar.set()


def _ip_permitida(ip: str) -> bool:
    if not config.ML_NOTIFICACIONES_IPS:
        return True
    try:
        direccion = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return any(direccion in red for red in config.ML_NOTIFICACIONES_IPS)


# --- Recepcion ---
@publico.post("/ml/notifications")
async def recibir(request: Request, conn: asyncpg.Connection = Depends(get_conn)):
    """Responde rapido: valida y encola. Lo que no es para nosotros se contesta 200 igual (ML no tiene
    que reintentarlo) y no se guarda."""
    ip = client_ip(request)
    if not _ip_permitida(ip):
        logger.warning(f"[AVISOS] Aviso rechazado: la IP {ip} no es de Mercado Libre.")
        return JSONResponse({"detail": "Origen no permitido."}, status_code=403)
    crudo = await request.body()
    if len(crudo) > TAMANO_MAXIMO:
        return JSONResponse({"detail": "Aviso demasiado grande."}, status_code=413)
    try:
        aviso = json.loads(crudo)
    except ValueError:
        return JSONResponse({"detail": "Aviso inválido."}, status_code=400)
    if not isinstance(aviso, dict):
        return JSONResponse({"detail": "Aviso inválido."}, status_code=400)
    topic, resource = aviso.get("topic"), aviso.get("resource")
    user_id, application_id = aviso.get("user_id"), aviso.get("application_id")

    motivo = None
    patron = TEMAS.get(topic) if isinstance(topic, str) else None
    if patron is None:
        motivo = f"tema {str(topic)[:40]!r} no usado"
    elif not isinstance(resource, str) or not patron.fullmatch(resource):
        motivo = f"recurso {str(resource)[:80]!r} invalido para {topic}"
    elif not isinstance(user_id, int) or isinstance(user_id, bool):
        motivo = "user_id invalido"
    elif str(application_id) != (await conn.fetchval("SELECT value FROM settings WHERE key = 'ml_client_id'") or ""):
        motivo = f"aplicacion {str(application_id)[:30]} no es la configurada"
    elif not await conn.fetchval("SELECT 1 FROM ml_accounts WHERE user_id = $1", user_id):
        motivo = f"cuenta {user_id} no conectada"
    if motivo:
        logger.info(f"[AVISOS] Aviso ignorado: {motivo}.")
        return {"ok": True}

    await conn.execute("""
        INSERT INTO ml_notifications (topic, resource, user_id) VALUES ($1, $2, $3)
        ON CONFLICT (topic, resource, user_id) WHERE status = 'PENDIENTE'
        DO UPDATE SET received_count = ml_notifications.received_count + 1, last_received_at = now()
    """, topic, resource, user_id)
    despertar()
    return {"ok": True}


# --- Cola ---
async def _volver_a_pendiente(conn: asyncpg.Connection, condicion: str, *args) -> int:
    """Vuelve a la cola los avisos que cumplen `condicion` (sobre el alias n), uno por recurso. Si ya hay
    uno pendiente del mismo recurso, los demas se descartan: ese ya va a traer el estado actual."""
    sin_pendiente = """NOT EXISTS (
        SELECT 1 FROM ml_notifications p WHERE p.status = 'PENDIENTE'
          AND p.topic = n.topic AND p.resource = n.resource AND p.user_id = n.user_id)"""
    try:
        async with conn.transaction():
            r = await conn.execute(f"""
                WITH elegidas AS (
                    SELECT DISTINCT ON (topic, resource, user_id) id FROM ml_notifications n
                    WHERE {condicion} AND {sin_pendiente}
                    ORDER BY topic, resource, user_id, id DESC
                )
                UPDATE ml_notifications SET status = 'PENDIENTE', attempts = 0, next_attempt_at = now(), taken_at = NULL
                WHERE id IN (SELECT id FROM elegidas)
            """, *args)
            # Los que siguen cumpliendo la condicion tienen ya otro pendiente del mismo recurso.
            await conn.execute(f"""
                UPDATE ml_notifications n SET status = 'DESCARTADA', processed_at = now(),
                    result = '{_REEMPLAZADO}'
                WHERE {condicion}
            """, *args)
    except asyncpg.UniqueViolationError:
        return 0   # justo llego un aviso nuevo del mismo recurso: se resuelve en la vuelta siguiente
    return int(r.split()[-1])


async def reactivar_cuenta(conn: asyncpg.Connection, user_id: int) -> int:
    """Al reconectar una cuenta, sus avisos con error vuelven a la cola."""
    n = await _volver_a_pendiente(conn, "n.status = 'ERROR' AND n.user_id = $1", user_id)
    if n:
        despertar()
    return n


async def _terminar(conn: asyncpg.Connection, aviso_id: int, estado: str, texto: str) -> None:
    columna = "last_error" if estado == "ERROR" else "result"
    await conn.execute(f"UPDATE ml_notifications SET status = $2, {columna} = $3, processed_at = now() WHERE id = $1",
                       aviso_id, estado, texto[:1000])


async def _reprogramar(conn: asyncpg.Connection, aviso: asyncpg.Record, error: str) -> None:
    """Reintento con espera creciente (se duplica en cada intento, hasta 1 hora)."""
    if aviso["attempts"] >= config.ML_COLA_MAX_INTENTOS:
        await _terminar(conn, aviso["id"], "ERROR", f"Se agotaron los reintentos. Último error: {error}")
        return
    espera = min(config.ML_COLA_REINTENTO_SEGUNDOS * 2 ** (aviso["attempts"] - 1), 3600)
    try:
        r = await conn.execute("""
            UPDATE ml_notifications n SET status = 'PENDIENTE', last_error = $3,
                next_attempt_at = now() + make_interval(secs => $2)
            WHERE id = $1 AND NOT EXISTS (
                SELECT 1 FROM ml_notifications p WHERE p.status = 'PENDIENTE'
                  AND p.topic = n.topic AND p.resource = n.resource AND p.user_id = n.user_id)
        """, aviso["id"], float(espera), error[:1000])
    except asyncpg.UniqueViolationError:
        r = "UPDATE 0"
    if r == "UPDATE 0":
        await _terminar(conn, aviso["id"], "DESCARTADA", _REEMPLAZADO)


async def _procesar(conn: asyncpg.Connection, aviso: asyncpg.Record) -> None:
    if not await conn.fetchval("SELECT 1 FROM ml_accounts WHERE user_id = $1", aviso["user_id"]):
        await _terminar(conn, aviso["id"], "DESCARTADA", "La cuenta ya no está conectada.")
        return
    try:
        r = await ml.llamar(conn, aviso["user_id"], "GET", aviso["resource"], params=PARAMETROS.get(aviso["topic"]))
    except ml.ErrorML as e:
        if e.reconectar:
            await _terminar(conn, aviso["id"], "ERROR", str(e))
        else:
            await _reprogramar(conn, aviso, str(e))
        return
    if r.status_code in (403, 404):
        await _terminar(conn, aviso["id"], "DESCARTADA",
                        f"Mercado Libre respondió {r.status_code}: el recurso no existe o no es de la cuenta.")
        return
    if r.status_code == 429 or r.status_code >= 500:
        await _reprogramar(conn, aviso, f"Mercado Libre respondió {r.status_code}.")
        return
    if r.status_code != 200:
        await _terminar(conn, aviso["id"], "ERROR", f"Mercado Libre respondió {r.status_code} al consultar el recurso.")
        return
    manejador = MANEJADORES.get(aviso["topic"])
    try:
        resultado = await manejador(conn, aviso, r.json()) if manejador else "Consultado en Mercado Libre."
    except ErrorAviso as e:
        await _terminar(conn, aviso["id"], "ERROR", str(e))
        return
    except Reintentar as e:
        await _reprogramar(conn, aviso, str(e))
        return
    except ml.ErrorML as e:
        if e.reconectar:
            await _terminar(conn, aviso["id"], "ERROR", str(e))
        else:
            await _reprogramar(conn, aviso, str(e))
        return
    except Exception as e:
        logger.exception(f"[AVISOS] Error procesando {aviso['topic']} {aviso['resource']}")
        await _reprogramar(conn, aviso, f"Error al procesar: {e!r}")
        return
    await _terminar(conn, aviso["id"], "HECHA", resultado)


async def procesar_lote(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn:
        tomados = await conn.fetch("""
            UPDATE ml_notifications SET status = 'PROCESANDO', attempts = attempts + 1, taken_at = now()
            WHERE id IN (
                SELECT id FROM ml_notifications WHERE status = 'PENDIENTE' AND next_attempt_at <= now()
                ORDER BY id LIMIT $1 FOR UPDATE SKIP LOCKED)
            RETURNING id, topic, resource, user_id, attempts
        """, LOTE)
    for aviso in sorted(tomados, key=lambda a: a["id"]):
        async with pool.acquire() as conn:
            await _procesar(conn, aviso)
    return len(tomados)


async def mantenimiento(pool: asyncpg.Pool) -> None:
    """Recupera los que quedaron colgados en PROCESANDO (el servicio se corto) y borra lo viejo."""
    async with pool.acquire() as conn:
        n = await _volver_a_pendiente(
            conn, "n.status = 'PROCESANDO' AND n.taken_at < now() - make_interval(mins => $1)", COLGADO_MINUTOS)
        if n:
            logger.warning(f"[AVISOS] {n} aviso(s) colgados volvieron a la cola.")
        await conn.execute("""
            DELETE FROM ml_notifications
            WHERE status IN ('HECHA', 'DESCARTADA') AND processed_at < now() - make_interval(days => $1)
        """, RETENCION_DIAS)


async def cola_en_segundo_plano(pool_de) -> None:
    """Tarea de fondo: procesa la cola al llegar un aviso o cada ML_COLA_SEGUNDOS (reintentos)."""
    while True:
        try:
            await asyncio.wait_for(_despertar.wait(), timeout=max(1, config.ML_COLA_SEGUNDOS))
        except asyncio.TimeoutError:
            pass
        _despertar.clear()
        pool = pool_de()
        if pool is None:
            continue
        try:
            await mantenimiento(pool)
            while await procesar_lote(pool):
                pass
        except Exception as e:   # la tarea de fondo no se corta por un error puntual
            logger.error(f"[AVISOS] Cola: {e!r}")


# --- Estado para la pagina ---
@router.get("/estado")
async def estado(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_conn)):
    conteo = {r["status"]: r["n"] for r in await conn.fetch("SELECT status, count(*) AS n FROM ml_notifications GROUP BY status")}
    fechas = await conn.fetchrow("SELECT max(last_received_at) AS recibida, max(processed_at) AS procesada FROM ml_notifications")
    recientes = await conn.fetch("""
        SELECT n.topic, n.resource, n.user_id, a.nickname, n.status, n.attempts, n.received_count,
               n.last_error, n.result, n.last_received_at, n.processed_at
        FROM ml_notifications n LEFT JOIN ml_accounts a ON a.user_id = n.user_id
        ORDER BY GREATEST(n.last_received_at, COALESCE(n.processed_at, n.last_received_at)) DESC, n.id DESC
        LIMIT 15
    """)

    def fecha(v):
        return v.isoformat() if v else None
    return {
        "pendientes": conteo.get("PENDIENTE", 0) + conteo.get("PROCESANDO", 0),
        "errores": conteo.get("ERROR", 0),
        "hechas": conteo.get("HECHA", 0),
        "descartadas": conteo.get("DESCARTADA", 0),
        "ultima_recibida": fecha(fechas["recibida"]),
        "ultima_procesada": fecha(fechas["procesada"]),
        "filtro_ips": bool(config.ML_NOTIFICACIONES_IPS),
        "temas": list(TEMAS),
        "recientes": [{**dict(r), "last_received_at": fecha(r["last_received_at"]), "processed_at": fecha(r["processed_at"])}
                      for r in recientes],
    }


@router.post("/reintentar")
async def reintentar(request: Request, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_conn)):
    """Vuelve a la cola los avisos con error (por ejemplo, despues de arreglar la aplicacion de ML)."""
    n = await _volver_a_pendiente(conn, "n.status = 'ERROR'")
    if n:
        despertar()
    await registrar(conn, admin["username"], "ML_AVISOS_REINTENTAR", f"{n} aviso(s) con error vuelven a la cola.", client_ip(request))
    return {"reintentados": n}
