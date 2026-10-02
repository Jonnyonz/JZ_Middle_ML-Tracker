"""Stock de Tracker -> Mercado Libre (paso 4). Tracker es el dueno del stock: a TODAS las publicaciones
activas (y a las pausadas por falta de stock) cuyo SKU coincide con uno de Tracker se les manda el
disponible que Tracker informa para el canal (segun su modo: disponible o disponible menos comprometido).

- Indice (ml_publicaciones): una fila por publicacion o por variante, con su SKU (atributo SELLER_SKU o
  seller_custom_field). Se arma recorriendo las publicaciones de cada cuenta (conciliacion) y se mantiene
  con los avisos "items" de ML (publicacion nueva o editada a mano).
- Que SKU revisar (ml_stock_pendiente): los stock.changed de Tracker (/api/v1/channel/events, con cursor),
  los avisos items y la conciliacion periodica (todo el indice).
- Escritura: solo esta tarea escribe en ML y solo si el numero cambia. Con variantes se relee la
  publicacion y se mandan TODAS sus variantes (ML borra las que no vienen en el PUT).
- Se informan (no se tocan): sin SKU, SKU que no esta en Tracker, Full (el stock lo maneja ML) y errores.
  Las pausadas por el vendedor tampoco se tocan.
"""

import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import logging
import math
from typing import Dict, List, Optional

import asyncpg
from fastapi import APIRouter, Depends, Request

from jzmiddle import avisos, config, ml, tracker
from jzmiddle.auth import client_ip, registrar as auditar, require_admin
from jzmiddle.db import get_conn

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ml/stock", tags=["Stock ML"])

MAXIMO_ML = 99999
LOTE_SKU = 100
LOTE_ITEMS = 20          # multiget de ML: hasta 20 publicaciones por consulta
EVENTOS_POR_PAGINA = 500
VIGENTE = "(status = 'active' OR (status = 'paused' AND sub_status LIKE '%out_of_stock%'))"

_despertar = asyncio.Event()
_conciliar = asyncio.Event()


class _Pasajero(Exception):
    """ML o Tracker no responden por un rato: se reintenta mas tarde."""


class _ErrorStock(Exception):
    """ML rechazo el cambio: queda informado en la publicacion."""


def despertar() -> None:
    _despertar.set()


def pedir_conciliacion() -> None:
    _conciliar.set()
    _despertar.set()


async def _ajuste(conn: asyncpg.Connection, clave: str) -> str:
    return await conn.fetchval("SELECT value FROM settings WHERE key = $1", clave) or ""


async def _guardar_ajuste(conn: asyncpg.Connection, clave: str, valor: str) -> None:
    await conn.execute("""
        INSERT INTO settings (key, value, updated_at) VALUES ($1, $2, now())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = now()
    """, clave, valor)


# --- Publicaciones ---
def sku_de(entidad: dict) -> Optional[str]:
    for a in entidad.get("attributes") or []:
        if a.get("id") == "SELLER_SKU" and (a.get("value_name") or "").strip():
            return a["value_name"].strip()
    return (entidad.get("seller_custom_field") or "").strip() or None


def sincronizable(status: str, sub_status: str) -> bool:
    return status == "active" or (status == "paused" and "out_of_stock" in (sub_status or ""))


def _filas(item: dict) -> List[dict]:
    base = {"item_id": str(item["id"]), "title": (item.get("title") or "")[:300], "status": item.get("status") or "",
            "sub_status": ",".join(item.get("sub_status") or []),
            "logistic_type": (item.get("shipping") or {}).get("logistic_type")}
    variantes = item.get("variations") or []
    if variantes:
        return [{**base, "variation_id": int(v["id"]), "sku": sku_de(v), "ml_quantity": v.get("available_quantity")}
                for v in variantes]
    return [{**base, "variation_id": 0, "sku": sku_de(item), "ml_quantity": item.get("available_quantity")}]


def _problema_del_indice(f: dict) -> Optional[str]:
    if f["logistic_type"] == "fulfillment":
        return "FULL"
    if not f["sku"]:
        return "SIN_SKU"
    return None


async def indexar_item(conn: asyncpg.Connection, user_id: int, item: dict) -> List[str]:
    """Guarda la publicacion (y sus variantes) en el indice. Devuelve los SKU a revisar."""
    filas = _filas(item)
    async with conn.transaction():
        await conn.execute("DELETE FROM ml_publicaciones WHERE item_id = $1 AND NOT (variation_id = ANY($2::bigint[]))",
                           filas[0]["item_id"], [f["variation_id"] for f in filas])
        for f in filas:
            await conn.execute("""
                INSERT INTO ml_publicaciones (item_id, variation_id, user_id, sku, title, status, sub_status,
                                              logistic_type, ml_quantity, problema)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                ON CONFLICT (item_id, variation_id) DO UPDATE SET
                    user_id = EXCLUDED.user_id, sku = EXCLUDED.sku, title = EXCLUDED.title, status = EXCLUDED.status,
                    sub_status = EXCLUDED.sub_status, logistic_type = EXCLUDED.logistic_type,
                    ml_quantity = EXCLUDED.ml_quantity, seen_at = now(),
                    problema = CASE WHEN EXCLUDED.problema IS NOT NULL THEN EXCLUDED.problema
                                    WHEN ml_publicaciones.problema IN ('SIN_SKU', 'FULL') THEN NULL
                                    ELSE ml_publicaciones.problema END
            """, f["item_id"], f["variation_id"], user_id, f["sku"], f["title"], f["status"], f["sub_status"],
                f["logistic_type"], f["ml_quantity"], _problema_del_indice(f))
    return sorted({f["sku"].upper() for f in filas
                   if not _problema_del_indice(f) and sincronizable(f["status"], f["sub_status"])})


async def marcar(conn: asyncpg.Connection, skus) -> None:
    """SKU cuyo stock hay que revisar en ML."""
    lista = sorted({str(s).strip().upper() for s in skus if s and str(s).strip()})
    if lista:
        await conn.execute("INSERT INTO ml_stock_pendiente (sku) SELECT unnest($1::text[]) ON CONFLICT (sku) DO NOTHING", lista)


async def _ml_get(conn: asyncpg.Connection, user_id: int, ruta: str, **kwargs):
    try:
        r = await ml.llamar(conn, user_id, "GET", ruta, **kwargs)
    except ml.ErrorML as e:
        raise _ErrorStock(str(e)) if e.reconectar else _Pasajero(str(e))
    if r.status_code == 429 or r.status_code >= 500:
        raise _Pasajero(f"Mercado Libre respondió {r.status_code} al consultar {ruta}.")
    if r.status_code != 200:
        raise _ErrorStock(f"Mercado Libre respondió {r.status_code} al consultar {ruta}.")
    return r.json()


async def _ids_de_cuenta(conn: asyncpg.Connection, user_id: int) -> List[str]:
    """Publicaciones activas y pausadas de la cuenta (busqueda scan: sin el tope de 1000 del offset)."""
    ids: List[str] = []
    for estado in ("active", "paused"):
        params = {"search_type": "scan", "status": estado, "limit": 100}
        for _ in range(2000):
            r = await _ml_get(conn, user_id, f"/users/{user_id}/items/search", params=params)
            resultados = r.get("results") or []
            ids.extend(str(i) for i in resultados)
            if not resultados or not r.get("scroll_id"):
                break
            params = {**params, "scroll_id": r["scroll_id"]}
    return list(dict.fromkeys(ids))


async def conciliar(pool: asyncpg.Pool) -> None:
    """Repasa todas las publicaciones de las cuentas y deja todos sus SKU para revisar."""
    async with pool.acquire() as conn:
        inicio = await conn.fetchval("SELECT now()")
        for c in await conn.fetch("SELECT user_id FROM ml_accounts WHERE status = 'ACTIVA'"):
            uid = c["user_id"]
            try:
                ids = await _ids_de_cuenta(conn, uid)
                for i in range(0, len(ids), LOTE_ITEMS):
                    lote = await _ml_get(conn, uid, "/items", params={"ids": ",".join(ids[i:i + LOTE_ITEMS]),
                                                                      "include_attributes": "all"})
                    for e in lote:
                        if e.get("code") == 200 and isinstance(e.get("body"), dict):
                            await indexar_item(conn, uid, e["body"])
                # Lo que no aparecio se cerro o se borro.
                await conn.execute("DELETE FROM ml_publicaciones WHERE user_id = $1 AND seen_at < $2", uid, inicio)
            except (_Pasajero, _ErrorStock) as e:
                logger.warning(f"[STOCK] No se pudo repasar la cuenta {uid}: {e}")
        await conn.execute("DELETE FROM ml_publicaciones WHERE user_id NOT IN (SELECT user_id FROM ml_accounts)")
        skus = await conn.fetch(f"""
            SELECT DISTINCT UPPER(sku) AS sku FROM ml_publicaciones
            WHERE sku IS NOT NULL AND problema IS DISTINCT FROM 'FULL' AND {VIGENTE}
        """)
        await marcar(conn, [s["sku"] for s in skus])
        await _guardar_ajuste(conn, "stock_ultima_conciliacion", datetime.now(timezone.utc).isoformat())
        logger.info(f"[STOCK] Conciliacion: {len(skus)} SKU para revisar.")


async def leer_eventos(pool: asyncpg.Pool) -> int:
    """stock.changed de Tracker -> SKU para revisar. El cursor se guarda junto con los SKU."""
    leidos = 0
    async with pool.acquire() as conn:
        despues = int(await _ajuste(conn, "tracker_eventos_cursor") or 0)
        for _ in range(20):
            r = await tracker.eventos(conn, despues, EVENTOS_POR_PAGINA)
            eventos = r.get("events") or []
            if not eventos:
                break
            skus = [e["payload"].get("sku") for e in eventos
                    if e.get("type") == "stock.changed" and isinstance(e.get("payload"), dict)]
            async with conn.transaction():
                await marcar(conn, skus)
                despues = int(r.get("next_after") or eventos[-1]["id"])
                await _guardar_ajuste(conn, "tracker_eventos_cursor", str(despues))
            leidos += len(eventos)
            if len(eventos) < EVENTOS_POR_PAGINA:
                break
    return leidos


def _cantidad(disponible: float) -> int:
    return int(min(MAXIMO_ML, math.floor(max(0.0, disponible))))


async def _escribir(conn: asyncpg.Connection, user_id: int, item_id: str, objetivo: Dict[int, int]) -> None:
    """PUT del stock en ML. Con variantes se mandan todas las que tiene la publicacion ahora."""
    if list(objetivo) == [0]:
        cuerpo = {"available_quantity": objetivo[0]}
    else:
        actual = await _ml_get(conn, user_id, f"/items/{item_id}", params={"include_attributes": "all"})
        variantes = [int(v["id"]) for v in actual.get("variations") or []]
        if not variantes or not set(objetivo) & set(variantes):
            await marcar(conn, await indexar_item(conn, user_id, actual))   # cambio la publicacion: se reindexa
            return
        cuerpo = {"variations": [{"id": v, "available_quantity": objetivo[v]} if v in objetivo else {"id": v}
                                 for v in variantes]}
    try:
        r = await ml.llamar(conn, user_id, "PUT", f"/items/{item_id}", json=cuerpo)
    except ml.ErrorML as e:
        raise _ErrorStock(str(e)) if e.reconectar else _Pasajero(str(e))
    if r.status_code == 429 or r.status_code >= 500:
        raise _Pasajero(f"Mercado Libre respondió {r.status_code} al actualizar {item_id}.")
    if r.status_code != 200:
        try:
            detalle = r.json().get("message") or ""
        except (ValueError, AttributeError):
            detalle = ""
        raise _ErrorStock(f"Mercado Libre rechazó el stock ({r.status_code}){': ' + detalle if detalle else ''}.")
    nuevo = r.json()
    if isinstance(nuevo, dict) and nuevo.get("id"):
        await indexar_item(conn, user_id, nuevo)
    for vid, cant in objetivo.items():
        await conn.execute("""
            UPDATE ml_publicaciones SET ml_quantity = $3, sent_at = now(), problema = NULL, last_error = NULL
            WHERE item_id = $1 AND variation_id = $2
        """, item_id, vid, cant)


async def _posponer(conn: asyncpg.Connection, skus: List[str], error: str) -> None:
    if skus:
        await conn.execute("""
            UPDATE ml_stock_pendiente SET attempts = attempts + 1, last_error = $2,
                next_attempt_at = now() + make_interval(secs => LEAST(3600, $3 * power(2, attempts)))
            WHERE sku = ANY($1::text[])
        """, skus, error[:500], float(config.ML_COLA_REINTENTO_SEGUNDOS))


async def procesar_pendientes(pool: asyncpg.Pool) -> int:
    """Manda a ML el stock de un lote de SKU pendientes. Devuelve cuantos SKU quedaron resueltos."""
    async with pool.acquire() as conn:
        filas = await conn.fetch("SELECT sku FROM ml_stock_pendiente WHERE next_attempt_at <= now() ORDER BY created_at LIMIT $1", LOTE_SKU)
        skus = [f["sku"] for f in filas]
        if not skus:
            return 0
        try:
            disponible = await tracker.stock(conn, skus)
        except tracker.ErrorTracker as e:
            await _posponer(conn, skus, str(e))
            return 0
        await conn.execute("""
            UPDATE ml_publicaciones SET problema = 'SKU_NO_EN_TRACKER', last_error = NULL
            WHERE UPPER(sku) = ANY($1::text[]) AND problema IS DISTINCT FROM 'FULL'
        """, [s for s in skus if s not in disponible])
        publicaciones = await conn.fetch(f"""
            SELECT * FROM ml_publicaciones
            WHERE UPPER(sku) = ANY($1::text[]) AND problema IS DISTINCT FROM 'FULL' AND {VIGENTE}
              AND user_id IN (SELECT user_id FROM ml_accounts WHERE status = 'ACTIVA')
        """, [s for s in skus if s in disponible])
        por_item = defaultdict(list)
        for p in publicaciones:
            por_item[(p["user_id"], p["item_id"])].append(p)
        reintentar = set()
        for (uid, item_id), filas_item in por_item.items():
            objetivo = {f["variation_id"]: _cantidad(disponible[f["sku"].upper()]) for f in filas_item}
            if all(f["ml_quantity"] == objetivo[f["variation_id"]] for f in filas_item):
                await conn.execute("""UPDATE ml_publicaciones SET problema = NULL, last_error = NULL
                                      WHERE item_id = $1 AND problema IN ('SKU_NO_EN_TRACKER', 'ERROR')""", item_id)
                continue
            try:
                await _escribir(conn, uid, item_id, objetivo)
            except _Pasajero as e:
                logger.info(f"[STOCK] {item_id}: {e} (se reintenta)")
                reintentar |= {f["sku"].upper() for f in filas_item}
            except _ErrorStock as e:
                logger.warning(f"[STOCK] {item_id}: {e}")
                await conn.execute("UPDATE ml_publicaciones SET problema = 'ERROR', last_error = $2 WHERE item_id = $1",
                                   item_id, str(e)[:500])
        await conn.execute("DELETE FROM ml_stock_pendiente WHERE sku = ANY($1::text[])", [s for s in skus if s not in reintentar])
        await _posponer(conn, sorted(reintentar), "Mercado Libre no respondió: se reintenta.")
        return len(skus) - len(reintentar)


async def _listo(conn: asyncpg.Connection) -> bool:
    return bool(await _ajuste(conn, "tracker_url") and await _ajuste(conn, "tracker_api_key")
                and await conn.fetchval("SELECT 1 FROM ml_accounts WHERE status = 'ACTIVA' LIMIT 1"))


def _conciliacion_vencida(ultima: str) -> bool:
    if not ultima:
        return True
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(ultima) > timedelta(minutes=max(1, config.ML_CONCILIAR_MINUTOS))
    except ValueError:
        return True


async def stock_en_segundo_plano(pool_de) -> None:
    """Tarea de fondo: conciliacion periodica, cambios de Tracker y envio a ML."""
    while True:
        try:
            await asyncio.wait_for(_despertar.wait(), timeout=max(1, config.ML_STOCK_SEGUNDOS))
        except asyncio.TimeoutError:
            pass
        _despertar.clear()
        pool = pool_de()
        if pool is None:
            continue
        try:
            async with pool.acquire() as conn:
                if not await _listo(conn):
                    continue
                ultima = await _ajuste(conn, "stock_ultima_conciliacion")
            if _conciliar.is_set() or _conciliacion_vencida(ultima):
                _conciliar.clear()
                await conciliar(pool)
            await leer_eventos(pool)
            while await procesar_pendientes(pool):
                pass
        except tracker.ErrorTracker as e:
            logger.warning(f"[STOCK] Tracker: {e}")
        except Exception as e:   # la tarea de fondo no se corta por un error puntual
            logger.error(f"[STOCK] {e!r}")


# --- Aviso "items" de ML: publicacion nueva o editada ---
async def manejar_item(conn: asyncpg.Connection, aviso: asyncpg.Record, item: dict) -> str:
    skus = await indexar_item(conn, aviso["user_id"], item)
    if skus:
        await marcar(conn, skus)
        despertar()
    return f"Publicación {item.get('id')} al día en el índice" + (f": se revisa el stock de {', '.join(skus)}." if skus else ".")


def registrar() -> None:
    avisos.MANEJADORES["items"] = manejar_item


# --- Estado para la pagina ---
@router.get("/estado")
async def estado(admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_conn)):
    conteo = {r["problema"]: r["n"] for r in await conn.fetch(
        f"SELECT COALESCE(problema, 'OK') AS problema, count(*) AS n FROM ml_publicaciones WHERE {VIGENTE} GROUP BY 1")}
    lista = await conn.fetch(f"""
        SELECT p.item_id, p.variation_id, p.title, p.sku, p.problema, p.last_error, p.ml_quantity, a.nickname
        FROM ml_publicaciones p LEFT JOIN ml_accounts a ON a.user_id = p.user_id
        WHERE (p.status = 'active' OR (p.status = 'paused' AND p.sub_status LIKE '%out_of_stock%'))
          AND p.problema IS NOT NULL
        ORDER BY p.problema, p.item_id, p.variation_id LIMIT 100
    """)
    enviado = await conn.fetchval("SELECT max(sent_at) FROM ml_publicaciones")
    return {
        "sincronizadas": conteo.get("OK", 0),
        "sin_sku": conteo.get("SIN_SKU", 0),
        "sku_no_en_tracker": conteo.get("SKU_NO_EN_TRACKER", 0),
        "full": conteo.get("FULL", 0),
        "con_error": conteo.get("ERROR", 0),
        "pendientes": await conn.fetchval("SELECT count(*) FROM ml_stock_pendiente"),
        "ultima_conciliacion": await _ajuste(conn, "stock_ultima_conciliacion") or None,
        "ultimo_envio": enviado.isoformat() if enviado else None,
        "problemas": [dict(r) for r in lista],
    }


@router.post("/conciliar")
async def conciliar_ahora(request: Request, admin: dict = Depends(require_admin), conn: asyncpg.Connection = Depends(get_conn)):
    """Repasa ya todas las publicaciones (corre en segundo plano)."""
    pedir_conciliacion()
    await auditar(conn, admin["username"], "ML_STOCK_CONCILIAR", "Conciliación de stock pedida desde la página.", client_ip(request))
    return {"ok": True}
