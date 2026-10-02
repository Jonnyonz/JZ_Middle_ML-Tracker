# Notas de los parches

Cambios de JZ Middle ML-Tracker, del más nuevo al más viejo. Cada entrada corresponde a un push a `main`.

## 2026-10-02 - 0.4.0

### Agregado
- Recepción de los avisos de Mercado Libre (ventas, envíos y publicaciones) en la dirección de
  notificaciones. Se contestan al instante, se guardan en una cola y se procesan en orden, consultando
  cada cambio en Mercado Libre. Los avisos repetidos del mismo cambio se juntan.
- Si Mercado Libre no responde o pide esperar, se reintenta solo con esperas cada vez más largas. Los que
  fallan quedan marcados y se pueden reintentar desde la página; los de una cuenta que se reconecta
  vuelven a la cola solos.
- Sección "Avisos de Mercado Libre" en la página: cuántos esperan, cuántos fallaron, el último recibido y
  los más recientes con su resultado. La sección de la aplicación muestra los temas a activar.

### Seguridad
- Los avisos de Mercado Libre no vienen firmados: no se cree en su contenido. Solo se aceptan los de la
  aplicación configurada y de cuentas conectadas, y cada cambio se confirma consultándolo en Mercado
  Libre con el permiso de la cuenta. Opcionalmente se aceptan solo desde las IPs de Mercado Libre
  (ML_NOTIFICACIONES_IPS).

## 2026-10-02 - 0.3.0

### Agregado
- Cuentas de Mercado Libre: botón "Conectar cuenta" que lleva a Mercado Libre a autorizar la cuenta y
  vuelve a la página. Se pueden conectar varias cuentas; reconectar una no la duplica. Cada cuenta muestra
  su estado, hasta cuándo vale el permiso y los avisos (permiso revocado, aplicación cambiada, falta el
  permiso offline_access), con botones para probarla y para desconectarla.
- Los permisos de cada cuenta se renuevan solos antes de vencer, sin que nadie tenga que entrar.

### Seguridad
- La autorización usa PKCE y un código de un solo uso que vence a los 10 minutos. Los permisos de las
  cuentas nunca se muestran en la página.

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
