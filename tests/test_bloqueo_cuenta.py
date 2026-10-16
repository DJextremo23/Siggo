"""Pruebas del flujo de bloqueo de cuenta por intentos fallidos y su recuperación."""

from unittest.mock import MagicMock


def test_minutos_bloqueo_progresivo():
    """La duración del bloqueo temporal crece con los ciclos previos."""
    from login import _minutos_bloqueo

    assert _minutos_bloqueo(0) == 15
    assert _minutos_bloqueo(1) == 30
    assert _minutos_bloqueo(2) == 60
    assert _minutos_bloqueo(3) == 1440
    assert _minutos_bloqueo(10) == 1440


def test_escalar_bloqueo_cuenta_activa_bloqueo(monkeypatch):
    """Al alcanzar el umbral de ciclos, la cuenta queda marcada como bloqueada."""
    import login as login_module

    cursor = MagicMock()
    cursor.fetchone.return_value = {"bloqueos_consecutivos": 3}
    conn = MagicMock()
    conn.cursor.return_value = cursor

    monkeypatch.setattr(login_module, "conexion", lambda: conn)
    monkeypatch.setattr(login_module, "registrar_auditoria", lambda *a, **k: None)

    login_module._escalar_bloqueo_cuenta(1)

    ejecutados = [c.args[0] for c in cursor.execute.call_args_list]
    assert any("cuenta_bloqueada = TRUE" in s for s in ejecutados)


def test_desbloquear_usuario_limpia_intentos_login(client, monkeypatch):
    """Al desbloquear, se limpian los intentos fallidos y se fuerza el cambio de contraseña."""
    import usuarios
    import main as main_module

    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"usuario": "pepe"},        # SELECT del usuario a desbloquear
        {"password": "hash_admin"},  # SELECT del password del admin (reautenticación)
    ]
    conn = MagicMock()
    conn.cursor.return_value = cursor

    # Evita que el before_request de sesión revocada (main) redirija al login:
    # simula que la sesión actual sigue siendo válida.
    rev_cursor = MagicMock()
    rev_cursor.fetchone.return_value = {"session_version": 0}
    rev_conn = MagicMock()
    rev_conn.cursor.return_value = rev_cursor

    monkeypatch.setattr(usuarios, "conexion", lambda: conn)
    monkeypatch.setattr(main_module, "conexion", rev_conn)
    monkeypatch.setattr(usuarios, "check_password_hash", lambda h, p: True)
    monkeypatch.setattr(usuarios, "generar_password_temporal", lambda: "TempClave1!")

    with client.session_transaction() as sess:
        sess["usuario"] = "admin_test"
        sess["perfil_activo"] = "admin"
        sess["id_usuario"] = 99
        sess["session_version"] = 0

    resp = client.post("/desbloquear_usuario/7", data={"password": "adminpass"})
    assert resp.status_code == 302

    llamadas = [(c.args[0], c.args[1] if len(c.args) > 1 else None)
                for c in cursor.execute.call_args_list]
    sqls = [sql for sql, args in llamadas]
    borrado = [args for sql, args in llamadas if "DELETE FROM intentos_login" in sql]
    assert any(args == ("uid:7",) for args in borrado)
    assert any("debe_cambiar_password = TRUE" in s for s in sqls)
    assert any("session_version = session_version + 1" in s for s in sqls)


def test_desbloquear_usuario_exige_password_admin(client, monkeypatch):
    """Sin la contraseña correcta del admin no se desbloquea ni se rota la contraseña."""
    import usuarios
    import main as main_module

    cursor = MagicMock()
    cursor.fetchone.side_effect = [
        {"usuario": "pepe"},
        {"password": "hash_admin"},
    ]
    conn = MagicMock()
    conn.cursor.return_value = cursor

    rev_cursor = MagicMock()
    rev_cursor.fetchone.return_value = {"session_version": 0}
    rev_conn = MagicMock()
    rev_conn.cursor.return_value = rev_cursor

    monkeypatch.setattr(usuarios, "conexion", lambda: conn)
    monkeypatch.setattr(main_module, "conexion", rev_conn)
    monkeypatch.setattr(usuarios, "check_password_hash", lambda h, p: False)

    with client.session_transaction() as sess:
        sess["usuario"] = "admin_test"
        sess["perfil_activo"] = "admin"
        sess["id_usuario"] = 99
        sess["session_version"] = 0

    resp = client.post("/desbloquear_usuario/7", data={"password": "mala"})
    assert resp.status_code == 302

    llamadas = [c.args[0] for c in cursor.execute.call_args_list]
    assert not any("debe_cambiar_password" in s for s in llamadas)
    assert not any("DELETE FROM intentos_login" in s for s in llamadas)


def test_login_exitoso_redirige_a_cambio_forzado(client, monkeypatch):
    """Con debe_cambiar_password activo, el login redirige al cambio de contraseña."""
    import login as login_module

    user = {
        "id_usuario": 7,
        "nombre": "A",
        "apellidos": "B",
        "usuario": "pepe",
        "correo": "pepe@x.com",
        "password": "hash",
        "estado": "activo",
        "foto": None,
        "totp_secret": None,
        "dos_factores_activo": False,
        "cuenta_bloqueada": False,
        "bloqueos_consecutivos": 0,
        "debe_cambiar_password": True,
        "session_version": 0,
        "nombre_rol": "fiscalizador",
    }

    cursor = MagicMock()
    cursor.fetchall.return_value = [user]
    conn = MagicMock()
    conn.cursor.return_value = cursor

    monkeypatch.setattr(login_module, "conexion", lambda: conn)
    monkeypatch.setattr(login_module, "_purga_periodica", lambda: None)
    monkeypatch.setattr(login_module, "_esta_bloqueado", lambda *a, **k: False)
    monkeypatch.setattr(login_module, "_limpiar_bloqueo", lambda *a, **k: None)
    monkeypatch.setattr(login_module, "_resetear_bloqueos_cuenta", lambda *a, **k: None)
    monkeypatch.setattr(login_module, "_detectar_nuevo_dispositivo", lambda *a, **k: None)
    monkeypatch.setattr(login_module, "registrar_auditoria", lambda *a, **k: None)
    monkeypatch.setattr(login_module, "check_password_hash", lambda *a, **k: True)

    resp = client.post("/login", data={"usuario": "pepe", "password": "correcta"})

    assert resp.status_code == 302
    assert "/cambiar_password_forzado" in resp.headers["Location"]


def test_cambiar_password_forzado_requiere_bandera(client):
    """Sin la bandera de sesión, la pantalla de cambio forzado redirige al login."""
    resp = client.get("/cambiar_password_forzado")
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


def test_login_fallido_muestra_intentos_restantes(client, monkeypatch):
    """Tras un fallo de credenciales se informa cuántos intentos quedan antes del bloqueo."""
    import login as login_module

    user = {
        "id_usuario": 7,
        "nombre": "A",
        "apellidos": "B",
        "usuario": "pepe",
        "correo": "pepe@x.com",
        "password": "hash",
        "estado": "activo",
        "foto": None,
        "totp_secret": None,
        "dos_factores_activo": False,
        "cuenta_bloqueada": False,
        "bloqueos_consecutivos": 0,
        "session_version": 0,
        "nombre_rol": "fiscalizador",
    }

    cursor = MagicMock()
    cursor.fetchall.return_value = [user]
    conn = MagicMock()
    conn.cursor.return_value = cursor

    monkeypatch.setattr(login_module, "conexion", lambda: conn)
    monkeypatch.setattr(login_module, "_purga_periodica", lambda: None)
    monkeypatch.setattr(login_module, "_esta_bloqueado", lambda *a, **k: False)
    monkeypatch.setattr(login_module, "_registrar_fallo", lambda *a, **k: None)
    monkeypatch.setattr(login_module, "_contar_intentos_db", lambda *a, **k: 1)
    monkeypatch.setattr(login_module, "check_password_hash", lambda *a, **k: False)

    resp = client.post("/login", data={"usuario": "pepe", "password": "mala"})

    texto = resp.get_data(as_text=True)
    assert "Le quedan 2 intentos" in texto


def test_login_cuenta_bloqueada_precede_bloqueo_temporal(client, monkeypatch):
    """Una cuenta bloqueada muestra 'contacte al administrador' aunque exista bloqueo temporal."""
    import login as login_module

    user = {
        "id_usuario": 7,
        "nombre": "A",
        "apellidos": "B",
        "usuario": "pepe",
        "correo": "pepe@x.com",
        "password": "hash",
        "estado": "activo",
        "foto": None,
        "totp_secret": None,
        "dos_factores_activo": False,
        "cuenta_bloqueada": True,
        "bloqueos_consecutivos": 3,
        "session_version": 0,
        "nombre_rol": "fiscalizador",
    }

    cursor = MagicMock()
    cursor.fetchall.return_value = [user]
    conn = MagicMock()
    conn.cursor.return_value = cursor

    monkeypatch.setattr(login_module, "conexion", lambda: conn)
    monkeypatch.setattr(login_module, "_purga_periodica", lambda: None)
    monkeypatch.setattr(login_module, "_esta_bloqueado", lambda *a, **k: True)

    resp = client.post("/login", data={"usuario": "pepe", "password": "cualquiera"})

    texto = resp.get_data(as_text=True)
    assert "Cuenta bloqueada por seguridad" in texto
    assert "Demasiados intentos fallidos" not in texto
