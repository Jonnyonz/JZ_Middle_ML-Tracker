# Notas de los parches

Cambios de JZ Middle ML-Tracker, del más nuevo al más viejo. Cada entrada corresponde a un push a `main`.

## 2026-10-02 - 0.2.0

### Agregado
- Configuración desde la página: conexión con Tracker360 (dirección y clave del canal de venta, con
  un botón para probarla) y aplicación de Mercado Libre (Client ID y Client Secret). La página muestra
  las dos direcciones que hay que cargar en la aplicación de Mercado Libre y una lista de lo que falta
  configurar. Las claves se guardan en el servidor y nunca se vuelven a mostrar.

## 2026-10-02 - 0.1.0

### Agregado
- Base del servicio: página de ingreso con la configuración inicial (creación del administrador con
  el token de instalación), inicio y cierre de sesión, límite de intentos por usuario y conexión, y
  una página de configuración con el estado del servicio.
- Instalación con Docker (base de datos propia en su contenedor, servicio solo en localhost para que
  lo publique Caddy por HTTPS).
- Seguridad desde el arranque: sesiones del lado del servidor, protección CSRF, la página no ejecuta
  código escrito dentro de ella (política de contenido estricta) y no publica el mapa de la API.
- Dependencias del set común de la suite, con versiones fijas y verificadas por hash.
