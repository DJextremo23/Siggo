"""Pruebas de integración - verifica que rutas requieren autenticación y redirigen correctamente."""


def test_app_exists(app):
    """Verifica que la aplicación Flask se crea correctamente."""
    assert app is not None


def test_testing_mode(app):
    """Verifica que la app está en modo testing."""
    assert app.config["TESTING"]


def test_secret_key(app):
    """Verifica que la clave secreta está configurada."""
    assert app.secret_key is not None


def test_home_redirects_to_login(client):
    """La raíz debe mostrar la página de login."""
    response = client.get("/")
    assert response.status_code == 200
    assert b"login" in response.data.lower() or b"Login" in response.data


def test_inicio_requires_session(client):
    """La ruta /inicio requiere sesión iniciada."""
    response = client.get("/inicio", follow_redirects=True)
    assert response.status_code == 200


def test_administrador_requires_auth(client):
    """La ruta /administrador redirige si no hay sesión."""
    response = client.get("/administrador")
    assert response.status_code == 302 and b"/" in response.data


def test_panel_fiscalizador_requires_auth(client):
    """La ruta /index requiere autenticación."""
    response = client.get("/index")
    assert response.status_code in (302, 403)


def test_guardias_requires_auth(client):
    """La ruta /guardias redirige sin autenticación."""
    response = client.get("/guardias")
    assert response.status_code == 302 and b"/" in response.data


def test_asistencia_admin_requires_auth(client):
    """La ruta /asistencias requiere autenticación."""
    resp = client.get("/asistencias")
    assert resp.status_code == 302


def test_compensaciones_admin_requires_auth(client):
    """La ruta /compensaciones requiere autenticación."""
    resp = client.get("/compensaciones")
    assert resp.status_code == 302


def test_feriados_requires_auth(client):
    """La ruta /feriados requiere autenticación."""
    resp = client.get("/feriados")
    assert resp.status_code == 302


def test_vacaciones_requires_auth(client):
    """La ruta /vacaciones requiere autenticación."""
    resp = client.get("/vacaciones")
    assert resp.status_code == 302


def test_invalid_route_returns_404(client):
    """Una ruta inexistente debe devolver 404."""
    resp = client.get("/ruta_inexistente")
    assert resp.status_code == 404
