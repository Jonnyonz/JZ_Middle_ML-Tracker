// Panel de configuracion. Cada seccion se carga por separado.
'use strict';

async function cargarEstado() {
    const salud = await api('/api/health');
    document.getElementById('estado-version').textContent = salud.version;
    document.getElementById('estado-esquema').textContent = String(salud.schema_version);
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
    cargarEstado().catch(e => mostrarMensaje(e.message, 'error'));
});
