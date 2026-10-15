"""Pruebas de las rutas de eliminación en lote (seleccionar varios + borrar seleccionados)."""

from unittest.mock import MagicMock


def _login(client, perfil, id_usuario=1):
    """Inicia sesión con el perfil e id indicados."""
    with client.session_transaction() as sess:
        sess["usuario"] = "usuario_test"
        sess["perfil_activo"] = perfil
        sess["id_usuario"] = id_usuario
        sess["nombre"] = "Test"
        sess["session_version"] = 0


def _mock_sesion_valida(monkeypatch):
    """Simula una sesión válida ante el before_request de sesión revocada (main)."""
    import main as main_module

    rev_cursor = MagicMock()
    rev_cursor.fetchone.return_value = {"session_version": 0}
    rev_conn = MagicMock()
    rev_conn.cursor.return_value = rev_cursor
    monkeypatch.setattr(main_module, "conexion", rev_conn)


# ── Rutas de lote del admin (main.py) ───────────────────────────────────────

def test_eliminar_guardias_sin_seleccion(client):
    """Sin IDs seleccionados debe hacer flash y redirigir (no 500)."""
    _login(client, "admin")
    resp = client.post("/eliminar_guardias", data={})
    assert resp.status_code == 302


def test_eliminar_guardias_con_seleccion(client):
    """Con IDs válidos debe ejecutar el borrado y redirigir."""
    _login(client, "admin")
    resp = client.post("/eliminar_guardias", data={"ids_guardia": "1"})
    assert resp.status_code == 302


def test_eliminar_guardias_id_invalido(client, monkeypatch):
    """Un identificador no numérico debe devolver 400."""
    _mock_sesion_valida(monkeypatch)
    _login(client, "admin")
    resp = client.post("/eliminar_guardias", data={"ids_guardia": "abc"})
    assert resp.status_code == 400


def test_eliminar_feriados_sin_seleccion(client):
    _login(client, "admin")
    resp = client.post("/eliminar_feriados", data={})
    assert resp.status_code == 302


def test_eliminar_compensaciones_sin_seleccion(client):
    _login(client, "admin")
    resp = client.post("/eliminar_compensaciones", data={})
    assert resp.status_code == 302


def test_eliminar_vacaciones_sin_seleccion(client):
    _login(client, "admin")
    resp = client.post("/eliminar_vacaciones", data={})
    assert resp.status_code == 302
