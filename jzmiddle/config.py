"""Configuracion de la instalacion (variables de entorno, archivo .env). Lo que cambia por cliente y se
carga desde la pagina (conexion con Tracker, aplicacion de Mercado Libre, cuentas) vive en la base."""

import ipaddress
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def _cargar_env(ruta: Path) -> None:
    """Carga KEY=VALOR de un .env sin pisar variables ya definidas (systemd o Docker mandan)."""
    if not ruta.is_file():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#") or "=" not in linea:
            continue
        clave, valor = linea.split("=", 1)
        os.environ.setdefault(clave.strip(), valor.strip().strip('"').strip("'"))


_cargar_env(Path(os.getenv("JZMIDDLE_ENV_FILE", Path(__file__).resolve().parent.parent / ".env")))

POSTGRES_HOST = os.getenv("POSTGRES_HOST", "127.0.0.1")
POSTGRES_PORT = int(os.getenv("POSTGRES_PORT", "5432"))
POSTGRES_DB = os.getenv("POSTGRES_DB", "jzmiddle_db")
POSTGRES_USER = os.getenv("POSTGRES_USER", "jzmiddle")
POSTGRES_PASSWORD = os.getenv("POSTGRES_PASSWORD", "")
DB_POOL_MAX = int(os.getenv("DB_POOL_MAX", "5"))

# Token de la configuracion inicial (alta del primer administrador). Lo genera el instalador.
SETUP_TOKEN = os.getenv("SETUP_TOKEN", "")

# Direccion publica con HTTPS de este servicio (detras de Caddy). Mercado Libre vuelve a
# <PUBLIC_URL>/ml/callback al autorizar una cuenta y manda las notificaciones a <PUBLIC_URL>/ml/notifications.
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")

# Proxies de confianza para la IP real (X-Forwarded-For). Por defecto loopback y redes de Docker.
TRUSTED_PROXIES = []
for _parte in os.getenv("TRUSTED_PROXIES", "127.0.0.1/32,::1/128,172.16.0.0/12").split(","):
    _parte = _parte.strip()
    if _parte:
        try:
            TRUSTED_PROXIES.append(ipaddress.ip_network(_parte, strict=False))
        except ValueError:
            logger.warning(f"TRUSTED_PROXIES: valor invalido ignorado: {_parte}")

# Cookies Secure: en produccion (HTTPS) siempre. Solo para pruebas locales por HTTP se puede apagar.
COOKIES_SECURE = os.getenv("COOKIES_SECURE", "true").strip().lower() != "false"

# Repo de GitHub donde se publican las versiones (aviso de version nueva en la pagina).
UPDATER_GITHUB_REPO = os.getenv("UPDATER_GITHUB_REPO", "Jonnyonz/JZ_Middle_ML-Tracker")

# Login: intentos fallidos antes de bloquear y minutos de bloqueo.
MAX_LOGIN_ATTEMPTS = int(os.getenv("MAX_LOGIN_ATTEMPTS", "5"))
LOCKOUT_MINUTES = int(os.getenv("LOCKOUT_MINUTES", "15"))
SESSION_HOURS = int(os.getenv("SESSION_HOURS", "8"))
