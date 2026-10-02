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
        mostrarMensaje('Aplicación de Mercado Libre guardada.');
    }));

    document.querySelectorAll('button.copiar').forEach(b => b.addEventListener('click', async () => {
        const texto = document.getElementById(b.dataset.copiar).textContent;
        try { await navigator.clipboard.writeText(texto); mostrarMensaje('Copiado.'); }
        catch (e) { mostrarMensaje('No se pudo copiar: seleccioná el texto a mano.', 'error'); }
    }));

    cargarConfiguracion().catch(e => mostrarMensaje(e.message, 'error'));
    cargarEstado().catch(e => mostrarMensaje(e.message, 'error'));
});
