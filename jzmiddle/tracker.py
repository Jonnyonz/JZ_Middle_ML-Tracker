"""Cliente de la API de canales de Tracker360 (/api/v1/channel/*). La direccion y la clave del canal
se cargan desde la pagina (ajustes tracker_url y tracker_api_key)."""

import logging
from typing import Optional
from urllib.parse import quote, urlparse

import asyncpg
import httpx

logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class ErrorTracker(Exception):
    """Tracker no responde, rechaza la clave o devuelve un error. El mensaje es apto para mostrar.
    reintentable=True: es un problema pasajero (Tracker caido o reiniciando), conviene reintentar."""

    def __init__(self, mensaje: str, reintentable: bool = False):
        super().__init__(mensaje)
        self.reintentable = reintentable


def validar_url(url: str) -> str:
    url = (url or "").strip().rstrip("/")
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ValueError("La dirección de Tracker tiene que empezar con http:// o https://.")
    return url


async def llamar(url_base: str, clave: str, metodo: str, ruta: str, **kwargs) -> httpx.Response:
    try:
        async with httpx.AsyncClient(base_url=url_base, timeout=TIMEOUT, follow_redirects=False,
                                     headers={"Authorization": f"Bearer {clave}"}) as c:
            r = await c.request(metodo, ruta, **kwargs)
    except httpx.HTTPError as e:
        logger.warning(f"[TRACKER] {metodo} {ruta}: {e!r}")
        raise ErrorTracker("No se pudo conectar con Tracker en esa dirección.", reintentable=True)
    if r.status_code == 401:
        raise ErrorTracker("Tracker rechazó la clave del canal (inválida o inactiva).")
    if r.status_code >= 500:
        raise ErrorTracker(f"Tracker respondió {r.status_code}.", reintentable=True)
    return r


def _detalle(r: httpx.Response) -> str:
    try:
        d = r.json().get("detail")
    except (ValueError, AttributeError):
        d = None
    return d if isinstance(d, str) else f"Tracker respondió {r.status_code}."


async def canal_actual(url_base: str, clave: str) -> dict:
    """Datos del canal (codigo, nombre, modo de stock): sirve para probar la conexion."""
    r = await llamar(url_base, clave, "GET", "/api/v1/channel/me")
    if r.status_code != 200:
        raise ErrorTracker(f"Tracker respondió {r.status_code}: ¿la dirección es la de Tracker360?")
    try:
        datos = r.json()
    except ValueError:
        raise ErrorTracker("La dirección no responde como Tracker360.")
    if not isinstance(datos, dict) or "code" not in datos:
        raise ErrorTracker("La dirección no responde como Tracker360.")
    return datos


# --- Con la conexion cargada en la pagina ---
async def _conexion(conn: asyncpg.Connection) -> tuple:
    url = await conn.fetchval("SELECT value FROM settings WHERE key = 'tracker_url'")
    clave = await conn.fetchval("SELECT value FROM settings WHERE key = 'tracker_api_key'")
    if not url or not clave:
        raise ErrorTracker("Falta configurar la conexión con Tracker en la página.")
    return url, clave


def _ruta_pedido(ref: str) -> str:
    return "/api/v1/channel/orders/" + quote(ref, safe="")


async def crear_pedido(conn: asyncpg.Connection, pedido: dict) -> dict:
    """Alta idempotente (por external_ref): si ya existe, Tracker devuelve el mismo con created=false."""
    url, clave = await _conexion(conn)
    r = await llamar(url, clave, "POST", "/api/v1/channel/orders", json=pedido)
    if r.status_code != 200:
        raise ErrorTracker(_detalle(r))
    return r.json()


async def cancelar_pedido(conn: asyncpg.Connection, ref: str) -> Optional[dict]:
    """None si el pedido no existe en Tracker. ErrorTracker si no se puede cancelar (ya despachado)."""
    url, clave = await _conexion(conn)
    r = await llamar(url, clave, "POST", _ruta_pedido(ref) + "/cancel")
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise ErrorTracker(_detalle(r))
    return r.json()


async def subir_etiqueta(conn: asyncpg.Connection, ref: str, zpl: str) -> dict:
    """Etiqueta ZPL del canal: Tracker la imprime al empacar (o en el momento si ya se despacho)."""
    url, clave = await _conexion(conn)
    r = await llamar(url, clave, "PUT", _ruta_pedido(ref) + "/label", json={"zpl": zpl})
    if r.status_code != 200:
        raise ErrorTracker(_detalle(r))
    return r.json()


async def eventos(conn: asyncpg.Connection, despues: int, limite: int = 500) -> dict:
    """Eventos del canal con id mayor a `despues` ({events: [...], next_after})."""
    url, clave = await _conexion(conn)
    r = await llamar(url, clave, "GET", "/api/v1/channel/events", params={"after": despues, "limit": limite})
    if r.status_code != 200:
        raise ErrorTracker(_detalle(r))
    return r.json()


async def stock(conn: asyncpg.Connection, skus: list) -> dict:
    """{SKU en mayusculas: disponible para el canal}. Los SKU que no existen en Tracker no aparecen."""
    url, clave = await _conexion(conn)
    r = await llamar(url, clave, "GET", "/api/v1/channel/stock", params={"skus": ",".join(skus)})
    if r.status_code != 200:
        raise ErrorTracker(_detalle(r))
    return {f["sku"].upper(): float(f["available"]) for f in r.json().get("items", [])}


async def informar_publicaciones(conn: asyncpg.Connection, publicaciones: list) -> bool:
    """Lista completa de publicaciones para el modulo Mercado Libre de Tracker (reemplaza la anterior).
    False si este Tracker todavia no tiene esa pantalla (version anterior al 2026-10-02)."""
    url, clave = await _conexion(conn)
    r = await llamar(url, clave, "PUT", "/api/v1/channel/listings", json={"listings": publicaciones})
    if r.status_code in (404, 405):
        return False
    if r.status_code != 200:
        raise ErrorTracker(_detalle(r))
    return True

