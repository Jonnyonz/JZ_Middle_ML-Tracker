from jztech_core.logging_setup import configure_logging, install_generic_error_handler

configure_logging()

import asyncio
from contextlib import asynccontextmanager
import logging
from pathlib import Path

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from jztech_core.security_headers import SecurityHeadersMiddleware

from jzmiddle import __version__, ajustes, auth, avisos, cuentas, db, ml, stock, ventas, version

logger = logging.getLogger(__name__)

FRONTEND = Path(__file__).resolve().parent.parent / "frontend"

# CSP estricta desde el primer dia: sin JavaScript ni estilos inline (todo en archivos de frontend/).
CSP = "; ".join([
    "default-src 'self'",
    "script-src 'self'",
    "style-src 'self'",
    "img-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


@asynccontextmanager
async def lifespan(app: FastAPI):
    await db.iniciar()
    logger.info(f"JZ Middle ML-Tracker {__version__} iniciado.")
    tareas = [asyncio.create_task(ml.renovacion_en_segundo_plano(lambda: db.DB.pool)),
              asyncio.create_task(avisos.cola_en_segundo_plano(lambda: db.DB.pool)),
              asyncio.create_task(stock.stock_en_segundo_plano(lambda: db.DB.pool))]
    yield
    for tarea in tareas:
        tarea.cancel()
    await db.cerrar()


# Sin documentacion interactiva publica: el mapa de la API no se expone.
app = FastAPI(title="JZ Middle ML-Tracker", version=__version__, lifespan=lifespan,
              docs_url=None, redoc_url=None, openapi_url=None)
install_generic_error_handler(app, "jzmiddle", field="detail")
app.add_middleware(SecurityHeadersMiddleware, csp=CSP, permissions_policy="geolocation=(), microphone=(), camera=()")

app.include_router(auth.router)
app.include_router(ajustes.router)
app.include_router(cuentas.router)
app.include_router(cuentas.callback_router)
app.include_router(avisos.router)
app.include_router(avisos.publico)
app.include_router(stock.router)
app.include_router(version.router)
ventas.registrar()   # ventas de ML -> pedidos de Tracker (avisos orders_v2 y shipments)
stock.registrar()    # publicaciones nuevas o editadas en ML (avisos items) -> stock de Tracker


@app.get("/api/health")
async def health(conn=Depends(db.get_conn)):
    """Para el chequeo del instalador y del actualizador: la app responde y la base tambien."""
    version_esquema = await conn.fetchval("SELECT COALESCE(MAX(version), 0) FROM schema_version")
    return {"status": "ok", "version": __version__, "schema_version": version_esquema}


@app.get("/")
async def pagina_inicio():
    return FileResponse(FRONTEND / "index.html")


@app.get("/panel")
async def pagina_panel():
    return FileResponse(FRONTEND / "panel.html")


app.mount("/static", StaticFiles(directory=FRONTEND / "static"), name="static")
