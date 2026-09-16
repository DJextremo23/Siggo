"""Pruebas de las rutas de eliminación en lote (seleccionar varios + borrar seleccionados)."""

from unittest.mock import MagicMock


def _login(client, perfil, id_usuario=1):
    """Inicia sesión con el perfil e id indicados."""
    with client.session_transaction() as sess:
        sess["usuario"] = "usuario_test"
        sess["perfil_activo"] = perfil
        sess["id_usuario"] = id_usuario
        sess["nombre"] = "Test"


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


def test_eliminar_guardias_id_invalido(client):
    """Un identificador no numérico debe devolver 400."""
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


# ── Ruta de lote del fiscalizador ────────────────────────────────────────────

def test_eliminar_mis_compensaciones_sin_seleccion(client):
    _login(client, "fiscalizador")
    resp = client.post("/eliminar_mis_compensaciones", data={})
    assert resp.status_code == 302


def test_eliminar_mis_compensaciones_rechaza_admin(client):
    """Un admin no puede usar la ruta de lote del fiscalizador."""
    _login(client, "admin")
    resp = client.post("/eliminar_mis_compensaciones", data={})
    assert resp.status_code in (302, 403)


# ── Ruta de lote de usuarios (fiscalizadores.py) ─────────────────────────────

def test_eliminar_usuarios_sin_seleccion(client):
    """Sin selección debe redirigir sin tocar la base de datos."""
    _login(client, "admin")
    resp = client.post("/eliminar_usuarios", data={})
    assert resp.status_code == 302


def _mock_conexion_usuarios(monkeypatch, filas_fetchone):
    """Reemplaza fiscalizadores.conexion por un mock cuyo cursor devuelve `filas_fetchone` secuencialmente."""
    import fiscalizadores

    conn = MagicMock()
    cursor = MagicMock()
    conn.cursor.return_value = cursor
    cursor.fetchone.side_effect = filas_fetchone
    monkeypatch.setattr(fiscalizadores, "conexion", lambda: conn)
    return cursor


def test_eliminar_usuarios_desactiva_si_tiene_guardias(client, monkeypatch):
    """Si el usuario tiene guardias, se desactiva (no se elimina)."""
    cursor = _mock_conexion_usuarios(
        monkeypatch,
        [{"cnt": 2}, {"estado": "activo"}],  # SELECT cnt -> tiene guardias; SELECT estado -> activo
    )
    _login(client, "admin")
    resp = client.post("/eliminar_usuarios", data={"ids_usuario": "5"})
    assert resp.status_code == 302

    sqls = [c.args[0] for c in cursor.execute.call_args_list]
    assert any("UPDATE usuarios SET estado" in s for s in sqls)
    assert not any("DELETE FROM usuarios" in s for s in sqls)


def test_eliminar_usuarios_elimina_si_no_tiene_guardias(client, monkeypatch):
    """Si el usuario no tiene guardias, se elimina (no se desactiva)."""
    cursor = _mock_conexion_usuarios(monkeypatch, [{"cnt": 0}])
    _login(client, "admin")
    resp = client.post("/eliminar_usuarios", data={"ids_usuario": "5"})
    assert resp.status_code == 302

    sqls = [c.args[0] for c in cursor.execute.call_args_list]
    assert any("DELETE FROM usuarios" in s for s in sqls)
    assert not any("UPDATE usuarios SET estado" in s for s in sqls)


def test_eliminar_usuarios_ignora_propia_cuenta(client, monkeypatch):
    """El admin no puede eliminarse/desactivarse a sí mismo en el lote."""
    cursor = _mock_conexion_usuarios(monkeypatch, [])
    _login(client, "admin", id_usuario=7)
    resp = client.post("/eliminar_usuarios", data={"ids_usuario": "7"})
    assert resp.status_code == 302

    sqls = [c.args[0] for c in cursor.execute.call_args_list]
    assert not any("DELETE FROM usuarios" in s for s in sqls)
    assert not any("UPDATE usuarios SET estado" in s for s in sqls)
