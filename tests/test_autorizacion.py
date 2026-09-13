"""Pruebas de autorización por rol (admin vs fiscalizador) y validación de formularios."""

import pytest


# ── Rutas exclusivas del administrador ──────────────────────────────────────
RUTAS_ADMIN = [
    "/administrador",
    "/guardias",
    "/asistencias",
    "/compensaciones",
    "/feriados",
    "/vacaciones",
    "/admin/fiscalizadores",
    "/admin/informes",
]

# ── Rutas exclusivas del fiscalizador ───────────────────────────────────────
RUTAS_FISCALIZADOR = [
    "/mis_guardias",
    "/mis_compensaciones",
    "/mis_feriados",
    "/mis_vacaciones",
    "/asistencia",
    "/mi_asistencia",
    "/mis_reportes",
]


def _login(client, perfil):
    """Inicia sesión con el perfil indicado."""
    with client.session_transaction() as sess:
        sess["usuario"] = "usuario_test"
        sess["perfil_activo"] = perfil
        sess["id_usuario"] = 1
        sess["nombre"] = "Test"


@pytest.mark.parametrize("ruta", RUTAS_ADMIN)
def test_admin_route_rechaza_fiscalizador(client, ruta):
    """Un fiscalizador no puede acceder a rutas del administrador (403 o redirect)."""
    _login(client, "fiscalizador")
    response = client.get(ruta)
    assert response.status_code in (302, 403)


@pytest.mark.parametrize("ruta", RUTAS_FISCALIZADOR)
def test_fiscalizador_route_rechaza_admin(client, ruta):
    """Un administrador no puede acceder a rutas del fiscalizador (403 o redirect)."""
    _login(client, "admin")
    response = client.get(ruta)
    assert response.status_code in (302, 403)


@pytest.mark.parametrize("ruta", RUTAS_ADMIN)
def test_admin_route_sin_sesion_redirige(client, ruta):
    """Sin sesión, las rutas admin redirigen (302) o deniegan (403)."""
    response = client.get(ruta)
    assert response.status_code in (302, 403)


def test_guardar_vacacion_sin_campos_redirige(client):
    """POST de vacaciones sin campos obligatorios debe redirigir (no 500)."""
    _login(client, "admin")
    response = client.post("/guardar_vacacion", data={})
    assert response.status_code == 302


def test_guardar_feriado_sin_campos_redirige(client):
    """POST de feriado sin campos obligatorios debe redirigir (no 500)."""
    _login(client, "admin")
    response = client.post("/guardar_feriado", data={})
    assert response.status_code == 302


def test_agregar_guardia_sin_campos_redirige(client):
    """POST de guardia sin campos obligatorios debe redirigir (no 500)."""
    _login(client, "admin")
    response = client.post("/agregar_guardia", data={})
    assert response.status_code == 302


def test_analizar_informe_requiere_auth(client):
    """El endpoint de análisis de IA requiere autenticación."""
    response = client.get("/analizar_informe/1/api")
    assert response.status_code == 401


def test_eliminar_usuario_propia_cuenta(client):
    """Un admin no puede eliminar su propia cuenta."""
    _login(client, "admin")
    response = client.post("/eliminar_usuario/1")
    assert response.status_code == 302
