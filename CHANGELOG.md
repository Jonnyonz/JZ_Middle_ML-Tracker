# Notas de los parches

Cambios de JZ Middle ML-Tracker, del más nuevo al más viejo. Cada entrada corresponde a un push a `main`.

## 2026-10-02 - 0.6.0

### Agregado
- Tracker manda el stock a Mercado Libre: a todas las publicaciones activas (y a las pausadas por falta de
  stock) de las cuentas conectadas cuyo SKU coincide con uno de Tracker, incluidas las variantes. Se manda
  el disponible según el modo del canal en Tracker (disponible, o disponible menos lo comprometido).
- Se actualiza apenas cambia el stock en Tracker, cuando alguien edita una publicación a mano en Mercado
  Libre o publica una nueva, y con un repaso completo cada hora (o con el botón "Repasar todo ahora").
  Solo se escribe en Mercado Libre cuando el número cambia; si Mercado Libre no responde, se reintenta.
- Sección "Stock en Mercado Libre" en la página: cuántas publicaciones se sincronizan y la lista de las
  que no se tocan, con el motivo (sin SKU, SKU que no está en Tracker, Full o error). Las pausadas a mano
  por el vendedor tampoco se tocan.

### Seguridad
- En las publicaciones con variantes se mandan siempre todas las variantes (Mercado Libre borra las que
  no vienen en el cambio).

## 2026-10-02 - 0.5.0

### Agregado
- Las ventas de Mercado Libre entran solas a Tracker como pedidos cuando se acredita el pago, con el
  comprador, la dirección de envío, la cuenta y el tipo de envío. Un carrito (varios productos comprados
  juntos) es un solo pedido. Los SKU tienen que ser los mismos en Mercado Libre y en Tracker.
- La etiqueta de envío de Mercado Libre se baja apenas está lista y se manda a Tracker, que la imprime al
  empacar.
- Si la venta se cancela en Mercado Libre, el pedido se cancela en Tracker. Si Tracker ya lo despachó,
  queda marcado para revisarlo a mano.
- Las ventas Full no se cargan en Tracker (salen del depósito de Mercado Libre).
- Si falta un SKU en Tracker o la publicación no tiene SKU, la venta queda con el error a la vista; una
  vez corregido, se reintenta desde la página. Si Tracker no responde, se reintenta solo.

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
