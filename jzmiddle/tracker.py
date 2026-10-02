"""Cliente de la API de canales de Tracker360 (/api/v1/channel/*). La direccion y la clave del canal
se cargan desde la pagina (ajustes tracker_url y tracker_api_key)."""

import logging
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(15.0, connect=5.0)


class ErrorTracker(Exception):
    """Tracker no responde, rechaza la clave o devuelve un error. El mensaje es apto para mostrar."""


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
        raise ErrorTracker("No se pudo conectar con Tracker en esa dirección.")
    if r.status_code == 401:
        raise ErrorTracker("Tracker rechazó la clave del canal (inválida o inactiva).")
    return r


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
