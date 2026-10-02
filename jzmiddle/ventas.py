"""Ventas de Mercado Libre -> pedidos de Tracker (paso 3): alta, etiqueta de envio y cancelacion.

Se engancha en la cola de avisos (avisos.MANEJADORES) para orders_v2 y shipments. Cada aviso trae el
estado ACTUAL del recurso consultado en ML, asi que procesar dos veces lo mismo no cambia nada: Tracker
es idempotente por referencia externa y aca se recuerda que se hizo con cada venta (ml_ventas).

- Una venta es una orden suelta o un carrito (pack: varias ordenes, un solo envio). En Tracker es UN
  pedido con referencia = numero de carrito o de orden.
- Se carga cuando todas sus ordenes activas estan pagadas (status "paid").
- SKU: el seller_sku de la publicacion o variante (o seller_custom_field). Tiene que existir igual en Tracker.
- Full (logistic_type fulfillment) sale del deposito de ML: va a Tracker como pedido informativo (Tracker
  lo registra con estado FULL: no mueve ni compromete stock ni entra al picking) y sin etiqueta.
- Flex (self_service: el vendedor entrega en el dia) va con urgent=true: Tracker lo prepara primero.
- Etiqueta: cuando el envio esta ready_to_ship se baja en ZPL (ML la manda dentro de un ZIP) y se sube a
  Tracker, que la imprime al empacar.
- Cancelada en ML -> se cancela en Tracker. Si Tracker ya la despacho, queda en error para verla a mano."""

import io
import logging
from typing import List, Optional
import zipfile

import asyncpg

from jzmiddle import avisos, ml, tracker

logger = logging.getLogger(__name__)

INACTIVAS = {"cancelled", "invalid"}
URGENTES = {"SELF_SERVICE"}   # Flex


# --- Lecturas en ML ---
async def _ml_json(conn: asyncpg.Connection, user_id: int, ruta: str, **kwargs) -> dict:
    r = await ml.llamar(conn, user_id, "GET", ruta, **kwargs)
    if r.status_code == 429 or r.status_code >= 500:
        raise avisos.Reintentar(f"Mercado Libre respondió {r.status_code} al consultar {ruta}.")
    if r.status_code != 200:
        raise avisos.ErrorAviso(f"Mercado Libre respondió {r.status_code} al consultar {ruta}.")
    return r.json()


async def _envio(conn: asyncpg.Connection, user_id: int, shipment_id) -> Optional[dict]:
    if not shipment_id:
        return None
    return await _ml_json(conn, user_id, f"/shipments/{int(shipment_id)}", headers={"x-format-new": "true"})


def _tipo_logistica(envio: Optional[dict]) -> Optional[str]:
    """Tipo de logistica de ML (cross_docking, drop_off, xd_drop_off, self_service = Flex, fulfillment =
    Full...). Acepta el formato nuevo de /shipments (logistic.type) y el viejo (logistic_type, mode)."""
    if not envio:
        return None
    logistica = envio.get("logistic") or {}
    tipo = logistica.get("type") or envio.get("logistic_type") or logistica.get("mode") or envio.get("mode")
    return str(tipo).upper() if tipo else None


def _destinatario(envio: Optional[dict], orden: dict) -> tuple:
    """(nombre, direccion) del envio; si no hay envio, el comprador de la orden."""
    dir_ = {}
    nombre = ""
    if envio:
        dir_ = envio.get("receiver_address") or ((envio.get("destination") or {}).get("shipping_address")) or {}
        nombre = dir_.get("receiver_name") or (envio.get("destination") or {}).get("receiver_name") or ""
    if not nombre:
        comprador = orden.get("buyer") or {}
        nombre = " ".join(p for p in (comprador.get("first_name"), comprador.get("last_name")) if p) or comprador.get("nickname") or ""
    partes = [dir_.get("address_line"), (dir_.get("city") or {}).get("name"), (dir_.get("state") or {}).get("name")]
    direccion = ", ".join(p for p in partes if p)
    if dir_.get("zip_code"):
        direccion += f" ({dir_['zip_code']})"
    return nombre.strip(), direccion.strip()


def _lineas(ordenes: List[dict]) -> List[dict]:
    lineas = []
    for o in ordenes:
        for it in o.get("order_items") or []:
            item = it.get("item") or {}
            sku = (item.get("seller_sku") or item.get("seller_custom_field") or "").strip()
            if not sku:
                raise avisos.ErrorAviso(
                    f"La publicación {item.get('id')} ({item.get('title') or 'sin título'}) no tiene SKU en Mercado Libre: "
                    "cargale el mismo SKU que en Tracker y reintentá.")
            lineas.append({"sku": sku, "quantity": it.get("quantity") or 0})
    if not lineas:
        raise avisos.ErrorAviso("La venta no tiene artículos.")
    return lineas


def _zpl(contenido: bytes) -> str:
    """ML manda la etiqueta zpl2 dentro de un ZIP (un .txt con el ZPL)."""
    if contenido[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(contenido)) as z:
            nombres = [n for n in z.namelist() if not n.endswith("/")]
            if not nombres:
                raise avisos.ErrorAviso("Mercado Libre mandó la etiqueta vacía.")
            contenido = z.read(nombres[0])
    return contenido.decode("utf-8", errors="replace")


# --- Ventas ---
async def _guardar(conn: asyncpg.Connection, ref: str, user_id: int, pack_id, ordenes: List[dict], estado: str,
                   envio: Optional[dict] = None, tracker_number: Optional[str] = None) -> None:
    await conn.execute("""
        INSERT INTO ml_ventas (ref, user_id, pack_id, order_ids, shipment_id, logistic_type, ml_status, estado, tracker_number)
        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
        ON CONFLICT (ref) DO UPDATE SET
            order_ids = EXCLUDED.order_ids, ml_status = EXCLUDED.ml_status, estado = EXCLUDED.estado,
            shipment_id = COALESCE(EXCLUDED.shipment_id, ml_ventas.shipment_id),
            logistic_type = COALESCE(EXCLUDED.logistic_type, ml_ventas.logistic_type),
            tracker_number = COALESCE(EXCLUDED.tracker_number, ml_ventas.tracker_number), updated_at = now()
    """, ref, user_id, int(pack_id) if pack_id else None, [int(o["id"]) for o in ordenes],
        int(envio["id"]) if envio and envio.get("id") else None, _tipo_logistica(envio),
        ",".join(sorted({str(o.get("status")) for o in ordenes}))[:30], estado, tracker_number)


def _de_tracker(e: tracker.ErrorTracker) -> Exception:
    return avisos.Reintentar(str(e)) if e.reintentable else avisos.ErrorAviso(f"Tracker: {e}")


async def _etiqueta(conn: asyncpg.Connection, venta: asyncpg.Record, envio: Optional[dict]) -> str:
    """Sube la etiqueta de ML a Tracker si el envio ya la tiene (ready_to_ship) y no se subio antes."""
    if venta["label_sent_at"] or not envio or envio.get("status") != "ready_to_ship":
        return ""
    r = await ml.llamar(conn, venta["user_id"], "GET", "/shipment_labels",
                        params={"shipment_ids": str(int(envio["id"])), "response_type": "zpl2"})
    if r.status_code != 200:
        logger.info(f"[VENTAS] Etiqueta de {envio['id']} no disponible: {r.status_code}")
        return f" Etiqueta todavía no disponible (Mercado Libre respondió {r.status_code})."
    try:
        await tracker.subir_etiqueta(conn, venta["ref"], _zpl(r.content))
    except tracker.ErrorTracker as e:
        raise _de_tracker(e)
    await conn.execute("UPDATE ml_ventas SET label_sent_at = now(), updated_at = now() WHERE ref = $1", venta["ref"])
    return " Etiqueta de envío enviada a Tracker."


async def _cancelar(conn: asyncpg.Connection, ref: str, user_id: int, pack_id, ordenes: List[dict], venta) -> str:
    if not venta or not venta["tracker_number"] or venta["estado"] == "CANCELADA":
        ya = bool(venta) and venta["estado"] == "CANCELADA"
        await _guardar(conn, ref, user_id, pack_id, ordenes, "CANCELADA")
        return "Venta cancelada (ya estaba registrada)." if ya else "Venta cancelada antes de cargarse en Tracker."
    try:
        r = await tracker.cancelar_pedido(conn, ref)
    except tracker.ErrorTracker as e:
        if e.reintentable:
            raise avisos.Reintentar(str(e))
        raise avisos.ErrorAviso(
            f"La venta {ref} se canceló en Mercado Libre pero Tracker no pudo cancelar el pedido "
            f"{venta['tracker_number']} ({e}): revisalo a mano.")
    await _guardar(conn, ref, user_id, pack_id, ordenes, "CANCELADA")
    if r is None:
        return f"Venta cancelada (el pedido {venta['tracker_number']} ya no estaba en Tracker)."
    return f"Venta cancelada: pedido {venta['tracker_number']} cancelado en Tracker."


async def sincronizar_venta(conn: asyncpg.Connection, user_id: int, orden: dict) -> str:
    """Lleva a Tracker el estado actual de la venta de esta orden."""
    pack_id = orden.get("pack_id")
    if pack_id:
        carrito = await _ml_json(conn, user_id, f"/packs/{int(pack_id)}")
        ids = [int(o["id"]) for o in carrito.get("orders") or []] or [int(orden["id"])]
        ordenes = [orden if i == int(orden["id"]) else await _ml_json(conn, user_id, f"/orders/{i}") for i in ids]
        ref = str(int(pack_id))
    else:
        ordenes, ref = [orden], str(int(orden["id"]))
    venta = await conn.fetchrow("SELECT * FROM ml_ventas WHERE ref = $1", ref)

    activas = [o for o in ordenes if o.get("status") not in INACTIVAS]
    if not activas:
        return await _cancelar(conn, ref, user_id, pack_id, ordenes, venta)
    estados = ", ".join(sorted({str(o.get("status")) for o in activas}))
    if any(o.get("status") != "paid" for o in activas):
        if venta and venta["tracker_number"]:
            return f"Pedido {venta['tracker_number']} ya cargado (estado en Mercado Libre: {estados})."
        await _guardar(conn, ref, user_id, pack_id, ordenes, "ESPERANDO_PAGO")
        return f"Esperando el pago (estado en Mercado Libre: {estados})."

    envio = await _envio(conn, user_id, (activas[0].get("shipping") or {}).get("id"))
    tipo = _tipo_logistica(envio)
    es_full = tipo == "FULFILLMENT"
    estado = "FULL" if es_full else "CARGADA"

    if venta and venta["tracker_number"] and venta["estado"] in ("CARGADA", "FULL"):
        numero = venta["tracker_number"]
        texto = f"Pedido {numero} ya cargado en Tracker."
    else:
        nombre, direccion = _destinatario(envio, activas[0])
        cuenta = await conn.fetchval("SELECT nickname FROM ml_accounts WHERE user_id = $1", user_id)
        pedido = {"external_ref": ref, "account": cuenta or str(user_id),
                  "buyer": {"name": nombre or None, "address": direccion or None},
                  "shipping": {"type": tipo, "shipment_ref": str(envio["id"]) if envio else None},
                  "urgent": tipo in URGENTES,
                  "lines": _lineas(activas)}
        try:
            r = await tracker.crear_pedido(conn, pedido)
        except tracker.ErrorTracker as e:
            raise _de_tracker(e)
        numero = r["document_number"]
        texto = f"Pedido {numero} cargado en Tracker." if r.get("created") else f"Pedido {numero} ya estaba en Tracker."
        if es_full:
            texto = texto[:-1] + " como venta Full (sale del depósito de Mercado Libre: no mueve stock de Tracker)."
        elif tipo in URGENTES:
            texto = texto[:-1] + " con prioridad (Flex: se entrega en el día)."
    await _guardar(conn, ref, user_id, pack_id, activas, estado, envio, numero)
    if es_full:
        return texto   # la etiqueta y el envio los maneja Mercado Libre
    venta = await conn.fetchrow("SELECT * FROM ml_ventas WHERE ref = $1", ref)
    return texto + await _etiqueta(conn, venta, envio)


# --- Manejadores de avisos ---
async def manejar_orden(conn: asyncpg.Connection, aviso: asyncpg.Record, orden: dict) -> str:
    return await sincronizar_venta(conn, aviso["user_id"], orden)


async def manejar_envio(conn: asyncpg.Connection, aviso: asyncpg.Record, envio: dict) -> str:
    venta = await conn.fetchrow("SELECT * FROM ml_ventas WHERE shipment_id = $1", int(envio["id"]))
    if not venta:
        # El aviso del envio llego antes que el de la venta: se procesa la venta.
        order_id = envio.get("order_id")
        if not order_id:
            return "Envío sin venta asociada."
        return await sincronizar_venta(conn, aviso["user_id"], await _ml_json(conn, aviso["user_id"], f"/orders/{int(order_id)}"))
    if venta["estado"] != "CARGADA":
        return f"Envío de la venta {venta['ref']} ({venta['estado'].lower()}): nada que hacer."
    texto = await _etiqueta(conn, venta, envio)
    return (f"Envío del pedido {venta['tracker_number']} ({envio.get('status')})." + texto).strip()


def registrar() -> None:
    avisos.MANEJADORES["orders_v2"] = manejar_orden
    avisos.MANEJADORES["shipments"] = manejar_envio
