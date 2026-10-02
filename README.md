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

## Instalación nativa (sin Docker)

Recomendada para el servidor del cliente. Debian 12, Debian 13 o Ubuntu 24.04 (Python 3.11 o más nuevo),
en el mismo servidor que Tracker360. El subdominio tiene que apuntar al servidor (puertos 80 y 443
abiertos) para que Caddy saque el certificado:

```bash
git clone https://github.com/Jonnyonz/JZ_Middle_ML-Tracker.git
cd JZ_Middle_ML-Tracker
sudo JZM_DOMAIN=ml.cliente.com ./install-native.sh
```

El instalador deja:

- el código y su entorno en `/opt/jzmiddle/releases/<versión>`, con `/opt/jzmiddle/current` apuntando a la
  versión en uso;
- la configuración en `/etc/jzmiddle/jzmiddle.env` (claves generadas; no se pisan si se vuelve a correr);
- la base y el rol `jzmiddle` propios en el PostgreSQL del servidor;
- el servicio `jzmiddle` (systemd, usuario sin login, solo en `127.0.0.1:8040`);
- Caddy con HTTPS para el subdominio.

Al final muestra el token para crear el administrador y la dirección de Tracker que hay que cargar en la
página (`http://127.0.0.1:<puerto de Tracker>`). Se puede volver a correr sin perder nada.

Opciones: `JZM_CADDY=0` (el servidor ya tiene otro proxy HTTPS: el instalador muestra qué configurar) y
`JZM_TLS_INTERNAL=1` (solo laboratorio: certificado de la CA local de Caddy, que Mercado Libre no acepta).
No instala compilador. Si hay una carpeta `wheelhouse/` al lado del script, instala sin internet.

## Actualizar

Instalación nativa:

```bash
sudo jz-middle-actualizar            # a la última versión publicada
sudo jz-middle-actualizar --buscar   # solo avisa si hay una nueva
sudo jz-middle-actualizar --volver   # vuelve a la versión anterior
```

Baja la versión de GitHub Releases y verifica su hash. Arma la versión nueva aparte; si algo falla en ese
paso, no se cambió nada. Después respalda la base en `/var/backups/jzmiddle`, cambia de versión (las
migraciones corren al arrancar) y verifica que responda. Si no responde, vuelve solo a la versión anterior
y deja la base como estaba. La página avisa cuando hay una versión nueva.

Con Docker: `git pull && docker compose up -d --build`.

Para publicar una versión (mantenimiento): subir la versión en `jzmiddle/__init__.py` y el CHANGELOG,
commit, `tools/empaquetar.sh` y crear el release `v<versión>` en GitHub con los dos archivos de `dist/`.

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
