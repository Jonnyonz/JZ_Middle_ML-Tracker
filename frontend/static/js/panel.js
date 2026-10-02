// Panel de configuracion. Cada seccion se carga por separado.
'use strict';

const NOMBRE_MODO = {
    DISPONIBLE: 'stock disponible',
    DISPONIBLE_MENOS_COMPROMETIDO: 'disponible menos lo comprometido en pedidos',
};

function pintarConfiguracion(c) {
    // Pendientes
    const lista = document.getElementById('lista-pendientes');
    lista.replaceChildren(...c.pendientes.map(p => {
        const li = document.createElement('li');
        li.textContent = p;
        return li;
    }));
    document.getElementById('config-completa').classList.toggle('oculto', c.pendientes.length > 0);

    // Tracker
    document.getElementById('tracker-url').value = c.tracker.url || '';
    document.getElementById('tracker-clave').value = '';
    document.getElementById('tracker-clave-estado').textContent = c.tracker.api_key_set
        ? 'Clave cargada. Dejá el campo vacío para conservarla.' : 'Todavía no hay clave cargada.';
    const canal = document.getElementById('tracker-canal');
    canal.textContent = c.tracker.canal
        ? `Conectado al canal ${c.tracker.canal.code} (${c.tracker.canal.name}): informa el ${NOMBRE_MODO[c.tracker.canal.stock_mode] || c.tracker.canal.stock_mode}.`
        : 'Sin probar: guardá la dirección y la clave y tocá "Probar conexión".';

    // Mercado Libre
    document.getElementById('ml-redirect').textContent = c.ml.redirect_uri || 'falta PUBLIC_URL';
    document.getElementById('ml-notif').textContent = c.ml.notifications_url || 'falta PUBLIC_URL';
    document.getElementById('ml-client-id').value = c.ml.client_id || '';
    document.getElementById('ml-client-secret').value = '';
    document.getElementById('ml-secreto-estado').textContent = c.ml.client_secret_set
        ? 'Client Secret cargado. Dejá el campo vacío para conservarlo.' : 'Todavía no hay Client Secret cargado.';
}

const RESULTADO_ML = {
    conectada: ['Cuenta de Mercado Libre conectada.', 'ok'],
    cancelada: ['La autorización se canceló en Mercado Libre.', 'error'],
    vencida: ['La autorización venció o ya se usó: volvé a tocar "Conectar cuenta".', 'error'],
    error: ['Mercado Libre no completó la autorización. Revisá el Client ID y el Client Secret.', 'error'],
};

function fechaHora(iso) {
    return new Date(iso).toLocaleString('es-AR', { dateStyle: 'short', timeStyle: 'short' });
}

function itemCuenta(c) {
    const li = document.createElement('li');
    li.className = 'cuenta';
    const datos = document.createElement('div');
    const nombre = document.createElement('div');
    nombre.className = 'cuenta-nombre';
    nombre.textContent = c.nickname || String(c.user_id);
    const activa = c.status === 'ACTIVA' && !c.otra_aplicacion;
    const etiqueta = document.createElement('span');
    etiqueta.className = 'etiqueta ' + (activa ? 'ok' : 'aviso');
    etiqueta.textContent = activa ? 'conectada' : 'reconectar';
    nombre.append(etiqueta);
    const detalle = document.createElement('div');
    detalle.className = 'cuenta-detalle';
    detalle.textContent = `ID ${c.user_id} · ${c.site_id || '-'} · conectada el ${fechaHora(c.connected_at)} · token hasta ${fechaHora(c.expires_at)}`;
    datos.append(nombre, detalle);
    const avisos = [];
    if (c.otra_aplicacion) avisos.push('Se autorizó con otra aplicación de Mercado Libre: volvé a conectarla.');
    if (!c.offline_access) avisos.push('La aplicación no tiene el permiso offline_access: la conexión se corta a las 6 horas.');
    if (c.last_error) avisos.push(c.last_error);
    avisos.forEach(t => {
        const p = document.createElement('div');
        p.className = 'cuenta-aviso';
        p.textContent = t;
        datos.append(p);
    });
    const acciones = document.createElement('div');
    acciones.className = 'acciones';
    const probar = document.createElement('button');
    probar.type = 'button';
    probar.className = 'secundario';
    probar.textContent = 'Probar';
    probar.addEventListener('click', conBoton(probar, async () => {
        try {
            const r = await api(`/api/ml/cuentas/${c.user_id}/probar`, { method: 'POST' });
            mostrarMensaje(`La cuenta ${r.nickname} responde bien.`);
        } finally { await recargarTodo(); }
    }));
    const quitar = document.createElement('button');
    quitar.type = 'button';
    quitar.className = 'secundario';
    quitar.textContent = 'Desconectar';
    quitar.addEventListener('click', conBoton(quitar, async () => {
        if (!window.confirm(`¿Desconectar la cuenta ${nombre.firstChild.textContent}? Deja de sincronizarse con Tracker.`)) return;
        await api(`/api/ml/cuentas/${c.user_id}`, { method: 'DELETE' });
        mostrarMensaje('Cuenta desconectada.');
        await recargarTodo();
    }));
    acciones.append(probar, quitar);
    li.append(datos, acciones);
    return li;
}

async function cargarCuentas() {
    const cuentas = await api('/api/ml/cuentas');
    document.getElementById('lista-cuentas').replaceChildren(...cuentas.map(itemCuenta));
    document.getElementById('sin-cuentas').classList.toggle('oculto', cuentas.length > 0);
}

const NOMBRE_ESTADO_AVISO = {
    PENDIENTE: 'en espera', PROCESANDO: 'procesando', HECHA: 'procesado', DESCARTADA: 'descartado', ERROR: 'error',
};

async function cargarAvisos() {
    const a = await api('/api/ml/avisos/estado');
    document.getElementById('avisos-pendientes').textContent = String(a.pendientes);
    document.getElementById('avisos-errores').textContent = String(a.errores);
    document.getElementById('avisos-recibido').textContent = a.ultima_recibida
        ? fechaHora(a.ultima_recibida) : 'todavía no llegó ninguno (llegan con la primera venta o cambio)';
    document.getElementById('avisos-procesado').textContent = a.ultima_procesada ? fechaHora(a.ultima_procesada) : '-';
    document.getElementById('avisos-filtro').textContent = a.filtro_ips
        ? 'solo se aceptan desde las IPs de Mercado Libre' : 'se aceptan de cualquier IP (ML_NOTIFICACIONES_IPS sin cargar)';
    document.getElementById('btn-reintentar-avisos').classList.toggle('oculto', a.errores === 0);
    document.getElementById('lista-avisos').replaceChildren(...a.recientes.map(r => {
        const li = document.createElement('li');
        const estado = document.createElement('span');
        estado.className = 'aviso-estado ' + r.status;
        estado.textContent = NOMBRE_ESTADO_AVISO[r.status] || r.status;
        const recurso = document.createElement('code');
        recurso.textContent = r.resource;
        const detalle = document.createElement('span');
        detalle.className = 'aviso-detalle';
        const partes = [r.nickname || String(r.user_id), fechaHora(r.processed_at || r.last_received_at)];
        if (r.received_count > 1) partes.push(`${r.received_count} avisos juntos`);
        if (r.last_error && r.status !== 'HECHA') partes.push(r.last_error);
        else if (r.result && r.status === 'DESCARTADA') partes.push(r.result);
        detalle.textContent = partes.join(' · ');
        li.append(estado, recurso, detalle);
        return li;
    }));
}

const NOMBRE_PROBLEMA = {
    SIN_SKU: 'sin SKU', SKU_NO_EN_TRACKER: 'SKU no está en Tracker', FULL: 'Full (stock de ML)', ERROR: 'error',
};

async function cargarStock() {
    const s = await api('/api/ml/stock/estado');
    const poner = (id, v) => { document.getElementById(id).textContent = String(v); };
    poner('stock-ok', s.sincronizadas);
    poner('stock-sin-sku', s.sin_sku);
    poner('stock-no-tracker', s.sku_no_en_tracker);
    poner('stock-full', s.full);
    poner('stock-error', s.con_error);
    poner('stock-pendientes', s.pendientes);
    poner('stock-conciliacion', s.ultima_conciliacion ? fechaHora(s.ultima_conciliacion) : 'todavía no');
    poner('stock-envio', s.ultimo_envio ? fechaHora(s.ultimo_envio) : '-');
    document.getElementById('lista-stock').replaceChildren(...s.problemas.map(p => {
        const li = document.createElement('li');
        const estado = document.createElement('span');
        estado.className = 'aviso-estado ' + (p.problema === 'ERROR' ? 'ERROR' : 'PENDIENTE');
        estado.textContent = NOMBRE_PROBLEMA[p.problema] || p.problema;
        const id = document.createElement('code');
        id.textContent = p.item_id + (p.variation_id ? ` / ${p.variation_id}` : '');
        const detalle = document.createElement('span');
        detalle.className = 'aviso-detalle';
        const enMl = p.problema === 'FULL' && p.ml_quantity !== null ? `${p.ml_quantity} u. en el depósito de Mercado Libre` : '';
        detalle.textContent = [p.title, p.sku ? `SKU ${p.sku}` : '', enMl, p.nickname || '', p.last_error || ''].filter(Boolean).join(' · ');
        li.append(estado, id, detalle);
        return li;
    }));
}

async function recargarTodo() {
    await Promise.all([cargarConfiguracion(), cargarCuentas(), cargarAvisos(), cargarStock()]);
}

async function cargarConfiguracion() {
    pintarConfiguracion(await api('/api/config'));
}

async function cargarEstado() {
    const salud = await api('/api/health');
    document.getElementById('estado-version').textContent = salud.version;
    document.getElementById('estado-esquema').textContent = String(salud.schema_version);
}

function conBoton(boton, fn) {
    return async (ev) => {
        if (ev) ev.preventDefault();
        boton.disabled = true;
        try { await fn(); } catch (e) { mostrarMensaje(e.message, 'error'); } finally { boton.disabled = false; }
    };
}

document.addEventListener('DOMContentLoaded', async () => {
    try {
        const yo = await api('/api/auth/me');
        document.getElementById('usuario').textContent = yo.username;
    } catch (e) {
        return;  // api() ya redirige al ingreso
    }
    document.getElementById('btn-salir').addEventListener('click', async () => {
        try { await api('/api/auth/logout', { method: 'POST' }); } finally { window.location.href = '/'; }
    });

    const formTracker = document.getElementById('form-tracker');
    formTracker.addEventListener('submit', conBoton(formTracker.querySelector('button[type=submit]'), async () => {
        pintarConfiguracion(await api('/api/config/tracker', { method: 'PUT', body: {
            url: document.getElementById('tracker-url').value,
            api_key: document.getElementById('tracker-clave').value,
        } }));
        mostrarMensaje('Conexión con Tracker guardada. Probala para confirmarla.');
    }));
    const btnProbar = document.getElementById('btn-probar-tracker');
    btnProbar.addEventListener('click', conBoton(btnProbar, async () => {
        const r = await api('/api/config/tracker/test', { method: 'POST' });
        await cargarConfiguracion();
        mostrarMensaje(`Conexión correcta con el canal ${r.canal.code}.`);
    }));

    const formML = document.getElementById('form-ml');
    formML.addEventListener('submit', conBoton(formML.querySelector('button[type=submit]'), async () => {
        pintarConfiguracion(await api('/api/config/ml', { method: 'PUT', body: {
            client_id: document.getElementById('ml-client-id').value,
            client_secret: document.getElementById('ml-client-secret').value,
        } }));
        mostrarMensaje('Aplicación de Mercado Libre guardada.'); await cargarCuentas();
    }));

    document.querySelectorAll('button.copiar').forEach(b => b.addEventListener('click', async () => {
        const texto = document.getElementById(b.dataset.copiar).textContent;
        try { await navigator.clipboard.writeText(texto); mostrarMensaje('Copiado.'); }
        catch (e) { mostrarMensaje('No se pudo copiar: seleccioná el texto a mano.', 'error'); }
    }));

    const btnAvisos = document.getElementById('btn-actualizar-avisos');
    btnAvisos.addEventListener('click', conBoton(btnAvisos, cargarAvisos));
    const btnReintentar = document.getElementById('btn-reintentar-avisos');
    btnReintentar.addEventListener('click', conBoton(btnReintentar, async () => {
        const r = await api('/api/ml/avisos/reintentar', { method: 'POST' });
        mostrarMensaje(`${r.reintentados} aviso(s) vuelven a la cola.`);
        await cargarAvisos();
    }));

    const btnStock = document.getElementById('btn-actualizar-stock');
    btnStock.addEventListener('click', conBoton(btnStock, cargarStock));
    const btnConciliar = document.getElementById('btn-conciliar-stock');
    btnConciliar.addEventListener('click', conBoton(btnConciliar, async () => {
        await api('/api/ml/stock/conciliar', { method: 'POST' });
        mostrarMensaje('Repaso pedido: en unos segundos tocá "Actualizar".');
    }));

    const btnConectar = document.getElementById('btn-conectar-cuenta');
    btnConectar.addEventListener('click', conBoton(btnConectar, async () => {
        const r = await api('/api/ml/cuentas/conectar', { method: 'POST' });
        window.location.href = r.url;
    }));

    // Vuelta desde Mercado Libre (/ml/callback lleva a /panel?ml=resultado).
    const resultado = RESULTADO_ML[new URLSearchParams(window.location.search).get('ml')];
    if (resultado) {
        mostrarMensaje(...resultado);
        history.replaceState(null, '', '/panel');
    }

    recargarTodo().catch(e => mostrarMensaje(e.message, 'error'));
    cargarEstado().catch(e => mostrarMensaje(e.message, 'error'));
});
