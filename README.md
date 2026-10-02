# JZ Middle ML-Tracker

Conecta las cuentas de **Mercado Libre** de un cliente con su **Tracker360** (WMS de JZTech Suite).

- **Tracker360 es el dueño del stock y de la preparación.** Los pedidos de Mercado Libre entran a
  Tracker como pedidos de su canal de ventas, se preparan y despachan ahí, y el stock que se publica en
  Mercado Libre sale de Tracker según la configuración del cliente (con o sin lo comprometido).
- **Un middleware por cliente**, instalado en el mismo servidor que su Tracker. Le habla a Tracker por
  su API de canales (`/api/v1/channel/*`, con la clave del canal) y a Mercado Libre por su API oficial.
- **Se configura desde una página web** (cuentas, conexión con Tracker, aplicación de Mercado Libre):
  no hay que tocar código ni archivos por cada cliente.

> Estado: en desarrollo (versión 0.1.0). Por ahora tiene la base del servicio: página de ingreso,
> administrador, sesiones y salud. La conexión con Mercado Libre se agrega en las versiones siguientes
> (ver `CHANGELOG.md`).

## Cómo funciona

```
Mercado Libre  --notificaciones-->  JZ Middle  --pedidos, etiquetas-->  Tracker360
               <--stock-----------             <--eventos, stock-------
```

- El middleware **lee** a Tracker (eventos por cursor y stock), en lugar de que Tracker le mande
  webhooks a la red interna.
- Lo que llega de Mercado Libre por notificaciones se repasa además cada pocos minutos, por si alguna
  se perdió.

## Requisitos

- Python 3.11 o superior (Debian 12 en adelante, Ubuntu 24.04) y PostgreSQL 15 o superior.
- Una dirección pública con HTTPS para el middleware (por ejemplo `https://ml.cliente.com`, detrás de
  Caddy): Mercado Libre vuelve a ella al autorizar una cuenta y le manda las notificaciones.
- Hardware mínimo: corre con 1 proceso; no necesita compilador (solo wheels precompiladas).

## Instalación con Docker

```bash
git clone https://github.com/Jonnyonz/JZ_Middle_ML-Tracker.git
cd JZ_Middle_ML-Tracker
cp .env.example .env      # completar POSTGRES_PASSWORD, SETUP_TOKEN y PUBLIC_URL
docker compose up -d --build
```

El servicio queda en `127.0.0.1:8040`. Caddy (o el proxy del servidor) lo publica por HTTPS:

```
ml.cliente.com {
    reverse_proxy 127.0.0.1:8040
}
```

Entrar a `https://ml.cliente.com`, crear el administrador con el `SETUP_TOKEN` del `.env` y seguir
la configuración desde la página.

La instalación nativa (sin Docker, con systemd) se agrega con `install-native.sh`.

## Dependencias

Son las del set común de la suite (`requirements.in`: solo dependencias directas con versión exacta;
`requirements.txt`: lockfile con hashes generado desde Python 3.11). Se instalan siempre con:

```bash
pip install --require-hashes --only-binary=:all: -r requirements.txt
```

## Estructura

```
jzmiddle/         # servicio (FastAPI): main.py, config.py, db.py, auth.py
migrations/       # esquema versionado: NNNN_descripcion.sql (jztech_core.migrations)
frontend/         # página de configuración (HTML, CSS y JS sin código inline)
tools/            # api_snapshot.py: contrato de la API para detectar cambios
docs/             # api-snapshot.json
```

## Licencia

AGPL-3.0 (ver `LICENSE`). Contribuciones: ver `CONTRIBUTING.md`.
