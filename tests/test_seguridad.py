"""Pruebas de seguridad: expiración de sesión por inactividad / tope absoluto."""

from unittest.mock import MagicMock


def _sesion(client, **kw):
    base = {
        "usuario": "usuario_test",
        "id_usuario": 1,
        "nombre": "Test",
        "perfil_activo": "fiscalizador",
        "roles": ["fiscalizador"],
        "session_version": 0,
    }
    base.update(kw)
    with client.session_transaction() as sess:
        for k, v in base.items():
            sess[k] = v


def _mock_sesion_valida(monkeypatch):
    """Simula una sesión válida ante los before_request de main (sesión revocada)."""
    import main as main_module

    rev_cursor = MagicMock()
    rev_cursor.fetchone.return_value = {"session_version": 0}
    rev_conn = MagicMock()
    rev_conn.cursor.return_value = rev_cursor
    monkeypatch.setattr(main_module, "conexion", rev_conn)


def test_sesion_inactiva_es_cerrada(client):
    """Una sesión con marca de actividad muy antigua se cierra y redirige al login."""
    _sesion(client)
    with client.session_transaction() as sess:
        sess["_login_at"] = 0
        sess["_last_activity"] = 0
    resp = client.get("/mis_guardias")
    assert resp.status_code == 302
    assert "/login" in resp.headers.get("Location", "")


def test_sesion_activa_no_es_cerrada(client, monkeypatch):
    """Una sesión con actividad reciente no se cierra ni se redirige al login."""
    import guardias

    _mock_sesion_valida(monkeypatch)

    # La ruta consulta guardias del usuario; se mockea su acceso a BD para que
    # el panel responda 200 en lugar de intentar conectarse a una BD real.
    cursor = MagicMock()
    cursor.fetchall.return_value = []
    conn = MagicMock()
    conn.cursor.return_value = cursor
    monkeypatch.setattr(guardias, "conexion", conn)

    _sesion(client)
    resp = client.get("/mis_guardias")
    assert resp.status_code == 200
    assert "/login" not in resp.headers.get("Location", "")
