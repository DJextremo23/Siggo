"""Pruebas de seguridad: expiración de sesión por inactividad / tope absoluto."""


def _sesion(client, **kw):
    base = {
        "usuario": "usuario_test",
        "id_usuario": 1,
        "nombre": "Test",
        "perfil_activo": "fiscalizador",
        "roles": ["fiscalizador"],
    }
    base.update(kw)
    with client.session_transaction() as sess:
        for k, v in base.items():
            sess[k] = v


def test_sesion_inactiva_es_cerrada(client):
    """Una sesión con marca de actividad muy antigua se cierra y redirige al login."""
    _sesion(client)
    with client.session_transaction() as sess:
        sess["_login_at"] = 0
        sess["_last_activity"] = 0
    resp = client.get("/mis_guardias")
    assert resp.status_code == 302
    assert "/login" in resp.headers.get("Location", "")


def test_sesion_activa_no_es_cerrada(client):
    """Una sesión con actividad reciente no se cierra."""
    _sesion(client)
    resp = client.get("/mis_guardias")
    assert "/login" not in resp.headers.get("Location", "")
