"""Aviso de version nueva en la pagina: consulta la ultima version publicada en GitHub Releases
(UPDATER_GITHUB_REPO), como mucho cada 6 horas. Actualizar se hace en el servidor, no desde la pagina
(instalacion nativa: sudo jz-middle-actualizar)."""

import logging
import re
import time
from typing import Optional

import httpx
from fastapi import APIRouter, Depends

from jzmiddle import __version__, config
from jzmiddle.auth import require_admin

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/actualizacion", tags=["Actualizacion"])

CADA_SEGUNDOS = 6 * 3600
_cache = {"cuando": None, "ultima": None}


def _tupla(v: str) -> tuple:
    return tuple(int(x) for x in v.split("."))


async def _ultima_publicada() -> Optional[str]:
    if _cache["cuando"] is not None and time.monotonic() - _cache["cuando"] < CADA_SEGUNDOS:
        return _cache["ultima"]
    ultima = None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(10.0, connect=5.0)) as c:
            r = await c.get(f"{config.UPDATER_API_URL}/repos/{config.UPDATER_GITHUB_REPO}/releases/latest",
                            headers={"Accept": "application/vnd.github+json"})
        if r.status_code == 200:
            etiqueta = str(r.json().get("tag_name", "")).lstrip("v")
            if re.fullmatch(r"\d+\.\d+\.\d+", etiqueta):
                ultima = etiqueta
    except (httpx.HTTPError, ValueError) as e:
        logger.info(f"[VERSION] No se pudo consultar la ultima version: {e!r}")
    _cache.update(cuando=time.monotonic(), ultima=ultima)
    return ultima


@router.get("")
async def estado(admin: dict = Depends(require_admin)):
    ultima = await _ultima_publicada()
    return {
        "actual": __version__,
        "ultima": ultima,
        "hay_nueva": bool(ultima) and _tupla(ultima) > _tupla(__version__),
        "como": "sudo jz-middle-actualizar" if config.JZM_INSTALACION == "nativa"
                else "git pull && docker compose up -d --build",
    }
