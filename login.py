from flask import Blueprint, render_template, request, redirect, session, url_for, current_app, make_response, flash, jsonify
from werkzeug.security import check_password_hash
from conexion import conexion
from datetime import datetime, timedelta
from limiter_instance import limiter
import pyotp
import qrcode
import qrcode.image.svg
import base64
import secrets
import hashlib
from io import BytesIO

"""
Blueprint de autenticación: login, logout, 2FA, dispositivos confiables,
bloqueo por intentos fallidos.
"""

# Blueprint que agrupa todas las rutas de autenticación
login_bp = Blueprint("login", __name__)

# Configuración de bloqueo por intentos fallidos
MAX_INTENTOS_LOGIN = 5
BLOQUEO_MINUTOS = 15

# ---------------------------------------------------------------------------
# Funciones auxiliares para control de intentos fallidos
# ---------------------------------------------------------------------------

# Elimina registros de intentos antiguos (fuera de la ventana de bloqueo)
def _limpiar_intentos_db(identificador):
    try:
        conn = conexion()
        cursor = conn.cursor()
        try:
            corte = datetime.now() - timedelta(minutes=BLOQUEO_MINUTOS)
            cursor.execute(
                "DELETE FROM intentos_login WHERE identificador = %s AND intento_en < %s",
                (identificador, corte)
            )
            conn.commit()
        finally:
            cursor.close()
            conn.close()
    except Exception:
        pass


# Cuenta los intentos fallidos vigentes para un identificador
def _contar_intentos_db(identificador):
    _limpiar_intentos_db(identificador)
    try:
        conn = conexion()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "SELECT COUNT(*) FROM intentos_login WHERE identificador = %s",
                (identificador,)
            )
            row = cursor.fetchone()
            return row[0] if row else 0
        finally:
            cursor.close()
            conn.close()
    except Exception:
        return 0


# Indica si un identificador alcanzó el máximo de intentos fallidos
def _esta_bloqueado(identificador):
    return _contar_intentos_db(identificador) >= MAX_INTENTOS_LOGIN


# Registra un nuevo intento fallido en la base de datos
def _registrar_fallo(identificador):
    try:
        conn = conexion()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "INSERT INTO intentos_login (identificador) VALUES (%s)",
                (identificador,)
            )
            conn.commit()
        finally:
            cursor.close()
            conn.close()
    except Exception:
        pass


# Elimina todos los intentos fallidos de un identificador (desbloqueo)
def _limpiar_bloqueo(identificador):
    try:
        conn = conexion()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "DELETE FROM intentos_login WHERE identificador = %s",
                (identificador,)
            )
            conn.commit()
        finally:
            cursor.close()
            conn.close()
    except Exception:
        pass

# ---------------------------------------------------------------------------
# Funciones auxiliares para dispositivos confiables (recordar dispositivo)
# ---------------------------------------------------------------------------

# Nombre de la cookie y días de validez para dispositivos confiables
NOMBRE_COOKIE_DISPOSITIVO = "ds_confiable"
DIAS_VALIDEZ_DISPOSITIVO = 30

# Genera hash SHA-256 del token para almacenarlo de forma segura
def _hash_token(token):
    return hashlib.sha256(token.encode()).hexdigest()


# Verifica si la cookie del dispositivo es válida para omitir 2FA
def _verificar_dispositivo_confiable(id_usuario):
    token = request.cookies.get(NOMBRE_COOKIE_DISPOSITIVO)
    if not token:
        return None

    token_hash = _hash_token(token)
    conn = None
    cursor = None
    try:
        conn = conexion()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id FROM dispositivos_confiables WHERE id_usuario = %s AND token_hash = %s",
            (id_usuario, token_hash)
        )
        row = cursor.fetchone()
        if row:
            return token
        return None
    except Exception:
        return None
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


# Actualiza la fecha de último uso del dispositivo confiable
def _actualizar_ultimo_uso_dispositivo(id_usuario, token):
    token_hash = _hash_token(token)
    conn = None
    cursor = None
    try:
        conn = conexion()
        cursor = conn.cursor()
        cursor.execute(
            "UPDATE dispositivos_confiables SET ultimo_uso = NOW() WHERE id_usuario = %s AND token_hash = %s",
            (id_usuario, token_hash)
        )
        conn.commit()
    except Exception:
        pass
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


# Registra un nuevo dispositivo confiable y retorna el token generado
def _registrar_dispositivo_confiable(id_usuario):
    token = secrets.token_urlsafe(64)
    token_hash = _hash_token(token)
    dispositivo_info = request.headers.get("User-Agent", "")[:500]
    conn = None
    cursor = None
    try:
        conn = conexion()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO dispositivos_confiables (id_usuario, token_hash, dispositivo_info) VALUES (%s, %s, %s)",
            (id_usuario, token_hash, dispositivo_info)
        )
        conn.commit()
        return token
    except Exception:
        return None
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


# Elimina todos los dispositivos confiables de un usuario
def _eliminar_dispositivos_confiables(id_usuario):
    conn = None
    cursor = None
    try:
        conn = conexion()
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM dispositivos_confiables WHERE id_usuario = %s",
            (id_usuario,)
        )
        conn.commit()
    except Exception:
        pass
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


# ==========================
# LOGIN — Inicio de sesión con validación de credenciales y bloqueo
# ==========================
@login_bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10 per minute")
def login():
    if request.method == "POST":
        usuario = request.form["usuario"].strip()
        password = request.form["password"].strip()

        if len(usuario) > 100 or len(password) > 128:
            return render_template(
                "login.html",
                error="Credenciales inválidas"
            )

        if _esta_bloqueado(usuario):
            return render_template(
                "login.html",
                error="Demasiados intentos fallidos. Intente de nuevo en 15 minutos."
            )

        conn = None
        cursor = None

        try:
            conn = conexion()
            cursor = conn.cursor(dictionary=True)

            cursor.execute("""
                SELECT
                    u.id_usuario,
                    u.nombre,
                    u.apellidos,
                    u.usuario,
                    u.correo,
                    u.password,
                    u.estado,
                    u.foto,
                    u.totp_secret,
                    u.dos_factores_activo,
                    r.nombre_rol
                FROM usuarios u
                INNER JOIN usuarios_roles ur
                ON u.id_usuario = ur.id_usuario
                INNER JOIN roles r
                ON ur.id_rol = r.id_rol
                WHERE u.usuario=%s
                OR u.correo=%s
            """, (usuario, usuario))

            resultados = cursor.fetchall()

            if not resultados:
                _registrar_fallo(usuario)
                return render_template(
                    "login.html",
                    error="Credenciales inválidas"
                )

            user = resultados[0]

            if user["estado"] != "activo":
                _registrar_fallo(usuario)
                return render_template(
                    "login.html",
                    error="Credenciales inválidas"
                )

            if not check_password_hash(
                user["password"],
                password
            ):
                _registrar_fallo(usuario)
                return render_template(
                    "login.html",
                    error="Credenciales inválidas"
                )

            _limpiar_bloqueo(usuario)

            session.clear()

            roles = []
            for r in resultados:
                roles.append(r["nombre_rol"])

            dos_factores = user.get("dos_factores_activo") if "dos_factores_activo" in user else False

            if dos_factores and user.get("totp_secret"):
                dispositivo_confiable = _verificar_dispositivo_confiable(user["id_usuario"])
                if dispositivo_confiable:
                    _actualizar_ultimo_uso_dispositivo(user["id_usuario"], dispositivo_confiable)
                else:
                    session["_2fa_pendiente"] = {
                        "id_usuario": user["id_usuario"],
                        "usuario": user["usuario"],
                        "nombre": f"{user['nombre']} {user['apellidos']}",
                        "foto": user.get("foto") or "",
                        "roles": roles,
                        "totp_secret": user["totp_secret"],
                    }
                    session["_2fa_pendiente_expira"] = (datetime.now() + timedelta(minutes=5)).timestamp()
                    return redirect(url_for("login.verificar_2fa"))

            session["id_usuario"] = user["id_usuario"]
            session["usuario"] = user["usuario"]
            session["nombre"] = (
                f"{user['nombre']} {user['apellidos']}"
            )
            session["foto"] = user.get("foto") or ""
            session["roles"] = roles
            session.permanent = True

            if len(roles) == 1:
                session["perfil_activo"] = roles[0]
                return redirect(url_for("inicio"))

            return redirect(url_for("login.seleccionar_perfil"))

        except Exception as e:
            print("ERROR LOGIN:", e)
            return render_template(
                "login.html",
                error="Error del sistema. Intente nuevamente."
            )

        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None:
                conn.close()
    return render_template("login.html")


# ==========================
# SELECCIONAR PERFIL — Elige rol cuando el usuario tiene múltiples roles
# ==========================
@login_bp.route("/seleccionar_perfil")
def seleccionar_perfil():
    if "id_usuario" not in session:
        return redirect(url_for("login.login"))

    return render_template(
        "seleccionar_perfil.html",
        roles=session["roles"]
    )

# ==========================
# ACTIVAR PERFIL — Guarda el rol seleccionado en la sesión
# ==========================
@login_bp.route("/activar_perfil/<rol>")
def activar_perfil(rol):
    if "id_usuario" not in session:
        return redirect(url_for("login.login"))

    if rol not in session["roles"]:
        return redirect(url_for("login.login"))

    session["perfil_activo"] = rol

    return redirect(url_for("inicio"))


# ==========================
# LOGOUT — Cierra sesión y limpia todos los datos de sesión
# ==========================
@login_bp.route("/logout")
def logout():
    session.clear()
    return redirect(
        url_for("login.login")
    )


# ==========================
# 2FA — VERIFICAR CÓDIGO TOTP durante el inicio de sesión
# ==========================
@login_bp.route("/verificar_2fa", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def verificar_2fa():
    pendiente = session.get("_2fa_pendiente")
    expira = session.get("_2fa_pendiente_expira", 0)

    if not pendiente or datetime.now().timestamp() > expira:
        session.pop("_2fa_pendiente", None)
        session.pop("_2fa_pendiente_expira", None)
        return redirect(url_for("login.login"))

    if request.method == "POST":
        codigo = request.form.get("codigo", "").strip()

        if not codigo or not codigo.isdigit() or len(codigo) != 6:
            return render_template(
                "verificar_2fa.html",
                error="Ingrese un código válido de 6 dígitos"
            )

        totp = pyotp.TOTP(pendiente["totp_secret"])
        if not totp.verify(codigo, valid_window=1):
            return render_template(
                "verificar_2fa.html",
                error="Código inválido. Intente nuevamente."
            )

        session.pop("_2fa_pendiente", None)
        session.pop("_2fa_pendiente_expira", None)
        session["id_usuario"] = pendiente["id_usuario"]
        session["usuario"] = pendiente["usuario"]
        session["nombre"] = pendiente["nombre"]
        session["foto"] = pendiente["foto"]
        session["roles"] = pendiente["roles"]
        session.permanent = True

        if len(pendiente["roles"]) == 1:
            session["perfil_activo"] = pendiente["roles"][0]
            respuesta = redirect(url_for("inicio"))
        else:
            respuesta = redirect(url_for("login.seleccionar_perfil"))

        if request.form.get("recordar_dispositivo") == "1":
            token = _registrar_dispositivo_confiable(pendiente["id_usuario"])
            if token:
                respuesta = make_response(respuesta)
                expiracion = datetime.now() + timedelta(days=DIAS_VALIDEZ_DISPOSITIVO)
                respuesta.set_cookie(
                    NOMBRE_COOKIE_DISPOSITIVO,
                    token,
                    max_age=DIAS_VALIDEZ_DISPOSITIVO * 86400,
                    expires=expiracion,
                    httponly=True,
                    secure=request.is_secure,
                    samesite="Lax",
                    path="/"
                )

        return respuesta

    return render_template("verificar_2fa.html")


# ==========================
# 2FA — CONFIGURAR (activar) desde el perfil del usuario
# ==========================
@login_bp.route("/configurar_2fa", methods=["GET", "POST"])
def configurar_2fa():
    if "usuario" not in session:
        return redirect(url_for("login.login"))

    conn = None
    cursor = None

    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        if request.method == "POST":
            codigo = request.form.get("codigo", "").strip()
            secret_temporal = session.get("_2fa_temp_secret")

            if not secret_temporal:
                return render_template(
                    "configurar_2fa.html",
                    error="La sesión expiró. Intente de nuevo."
                )

            if not codigo or not codigo.isdigit() or len(codigo) != 6:
                return render_template(
                    "configurar_2fa.html",
                    error="Ingrese un código válido de 6 dígitos",
                    secret=secret_temporal,
                    qr_mostrado=True
                )

            totp = pyotp.TOTP(secret_temporal)
            if not totp.verify(codigo, valid_window=1):
                return render_template(
                    "configurar_2fa.html",
                    error="Código inválido. Intente nuevamente.",
                    secret=secret_temporal,
                    qr_mostrado=True
                )

            cursor.execute("""
                UPDATE usuarios
                SET totp_secret = %s, dos_factores_activo = TRUE
                WHERE id_usuario = %s
            """, (secret_temporal, session["id_usuario"]))

            conn.commit()
            session.pop("_2fa_temp_secret", None)

            return render_template(
                "configurar_2fa.html",
                mensaje="Autenticación en dos pasos activada correctamente.",
                configurado=True
            )

        secret = pyotp.random_base32()
        session["_2fa_temp_secret"] = secret

        totp = pyotp.TOTP(secret)
        uri = totp.provisioning_uri(
            name=session.get("usuario", "usuario"),
            issuer_name="SIGGO-OIG"
        )

        factory = qrcode.image.svg.SvgPathImage
        img = qrcode.make(uri, image_factory=factory)
        buffer = BytesIO()
        img.save(buffer)
        qr_svg = buffer.getvalue().decode("utf-8")

        return render_template(
            "configurar_2fa.html",
            secret=secret,
            qr_svg=qr_svg,
            qr_mostrado=True
        )

    except Exception as e:
        if conn is not None:
            conn.rollback()
        print("ERROR configurar_2fa:", e)
        return render_template(
            "configurar_2fa.html",
            error="Error interno del sistema."
        )

    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


# ==========================
# 2FA — DESACTIVAR desde el perfil del usuario
# ==========================
@login_bp.route("/desactivar_2fa", methods=["POST"])
def desactivar_2fa():
    if "usuario" not in session:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"success": False, "message": "Sesión expirada. Inicia sesión nuevamente."}), 401
        return redirect(url_for("login.login"))

    ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    conn = None
    cursor = None

    try:
        conn = conexion()
        cursor = conn.cursor()

        cursor.execute("""
            UPDATE usuarios
            SET totp_secret = NULL, dos_factores_activo = FALSE
            WHERE id_usuario = %s
        """, (session["id_usuario"],))

        conn.commit()

        _eliminar_dispositivos_confiables(session["id_usuario"])

        if ajax:
            return jsonify({"success": True, "message": "Autenticación en dos pasos desactivada correctamente."})

        flash("Autenticación en dos pasos desactivada correctamente.", "success")
        return redirect(url_for("perfil.editar_mi_perfil"))

    except Exception as e:
        if conn is not None:
            conn.rollback()
        print("ERROR desactivar_2fa:", e)
        if ajax:
            return jsonify({"success": False, "message": "Error interno del servidor. Intente nuevamente."}), 500
        flash("Error al desactivar la autenticación en dos pasos.", "error")
        return redirect(url_for("perfil.editar_mi_perfil"))

    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()
