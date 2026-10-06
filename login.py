from flask import Blueprint, render_template, request, redirect, session, url_for, make_response, flash, jsonify
from werkzeug.security import check_password_hash, generate_password_hash
from conexion import conexion
from datetime import datetime, timedelta
from limiter_instance import limiter
from utils import registrar_auditoria
import pyotp
import qrcode
import qrcode.image.svg
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

# Tras N ciclos de bloqueo temporal, la cuenta se bloquea de forma "dura":
# solo un administrador puede reactivarla (escalado anti fuerza bruta).
MAX_LOCKOUTS_ANTES_BLOQUEO = 5

# Configuración específica para el código TOTP durante el login
MAX_INTENTOS_2FA = 10
TOKEN_2FA_TTL_MINUTOS = 5

# Hash dummy para igualar tiempos de respuesta en login (evita enumeración de usuarios)
_DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_urlsafe(32))

# ---------------------------------------------------------------------------
# Funciones auxiliares para control de intentos fallidos
# ---------------------------------------------------------------------------

# Elimina registros de intentos antiguos (fuera de la ventana de bloqueo)
def _limpiar_intentos_db(identificador, minutos=BLOQUEO_MINUTOS):
    try:
        conn = conexion()
        cursor = conn.cursor()
        try:
            corte = datetime.now() - timedelta(minutes=minutos)
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


# Cuenta los intentos fallidos vigentes para un identificador (dentro de la ventana)
def _contar_intentos_db(identificador, minutos=BLOQUEO_MINUTOS):
    _limpiar_intentos_db(identificador, minutos)
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
def _esta_bloqueado(identificador, max_intentos=MAX_INTENTOS_LOGIN, minutos=BLOQUEO_MINUTOS):
    return _contar_intentos_db(identificador, minutos) >= max_intentos


# Duración (minutos) del bloqueo temporal según la cantidad de bloqueos previos
# de la cuenta (escalado progresivo):
#   0 previos -> 15 min, 1 -> 30 min, 2 -> 60 min, 3 o más -> 24 h
def _minutos_bloqueo_actual(usuario_o_correo):
    ciclos = _ciclos_bloqueo_cuenta(usuario_o_correo)
    if ciclos <= 0:
        return 15
    if ciclos == 1:
        return 30
    if ciclos == 2:
        return 60
    return 1440


# Devuelve la cantidad de bloqueos consecutivos registrados para una cuenta
def _ciclos_bloqueo_cuenta(usuario_o_correo):
    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)
        try:
            if "@" in usuario_o_correo:
                cursor.execute(
                    "SELECT bloqueos_consecutivos FROM usuarios WHERE LOWER(correo) = %s",
                    (usuario_o_correo.lower(),)
                )
            else:
                cursor.execute(
                    "SELECT bloqueos_consecutivos FROM usuarios WHERE usuario = %s",
                    (usuario_o_correo,)
                )
            row = cursor.fetchone()
            return row["bloqueos_consecutivos"] if row else 0
        finally:
            cursor.close()
            conn.close()
    except Exception:
        return 0


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


# Escala el bloqueo a nivel de cuenta: cada vez que se cruza el umbral de
# intentos fallidos (un "ciclo de bloqueo"), incrementa el contador de la
# cuenta. Al alcanzar MAX_LOCKOUTS_ANTES_BLOQUEO, marca la cuenta como
# bloqueada (cuenta_bloqueada=TRUE) y solo un administrador podrá reactivarla.
def _escalar_bloqueo_cuenta(usuario_o_correo):
    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)
        try:
            if "@" in usuario_o_correo:
                cursor.execute(
                    "SELECT id_usuario FROM usuarios WHERE LOWER(correo) = %s",
                    (usuario_o_correo.lower(),)
                )
            else:
                cursor.execute(
                    "SELECT id_usuario FROM usuarios WHERE usuario = %s",
                    (usuario_o_correo,)
                )
            row = cursor.fetchone()
            if not row:
                return

            id_usuario = row["id_usuario"]

            cursor.execute(
                "UPDATE usuarios SET bloqueos_consecutivos = bloqueos_consecutivos + 1 WHERE id_usuario = %s",
                (id_usuario,)
            )
            cursor.execute(
                "SELECT bloqueos_consecutivos FROM usuarios WHERE id_usuario = %s",
                (id_usuario,)
            )
            contador = cursor.fetchone()["bloqueos_consecutivos"]

            if contador >= MAX_LOCKOUTS_ANTES_BLOQUEO:
                cursor.execute(
                    "UPDATE usuarios SET cuenta_bloqueada = TRUE WHERE id_usuario = %s",
                    (id_usuario,)
                )
                registrar_auditoria(
                    "cuenta_bloqueada",
                    f"Bloqueo duro por intentos fallidos repetidos: {usuario_o_correo}"
                )

            conn.commit()
        finally:
            cursor.close()
            conn.close()
    except Exception:
        pass


# Reinicia el contador de bloqueos consecutivos tras un inicio de sesión exitoso
def _resetear_bloqueos_cuenta(id_usuario):
    try:
        conn = conexion()
        cursor = conn.cursor()
        try:
            cursor.execute(
                "UPDATE usuarios SET bloqueos_consecutivos = 0 WHERE id_usuario = %s",
                (id_usuario,)
            )
            conn.commit()
        finally:
            cursor.close()
            conn.close()
    except Exception:
        pass


# Purga periódica de las tablas de seguridad (mejor esfuerzo, como máximo 1 vez/hora).
# Evita el crecimiento ilimitado de intentos_login, login_2fa_pendiente y
# dispositivos_confiables, que de otro modo solo se limpian de forma parcial.
_ULTIMA_PURGA = None
_PURGA_INTERVALO_SEG = 3600


def _purga_periodica():
    global _ULTIMA_PURGA
    ahora = datetime.now()
    if _ULTIMA_PURGA is not None and (ahora - _ULTIMA_PURGA).total_seconds() < _PURGA_INTERVALO_SEG:
        return
    _ULTIMA_PURGA = ahora

    try:
        conn = conexion()
        cursor = conn.cursor()
        try:
            # Intentos fallidos: se retienen 1 día (más que suficiente para el bloqueo de 24 h).
            cursor.execute("DELETE FROM intentos_login WHERE intento_en < (NOW() - INTERVAL 1 DAY)")
            # Tokens 2FA pendientes ya expirados.
            cursor.execute("DELETE FROM login_2fa_pendiente WHERE expira < NOW()")
            # Dispositivos confiables inactivos por más de 90 días.
            cursor.execute("DELETE FROM dispositivos_confiables WHERE ultimo_uso < (NOW() - INTERVAL 90 DAY)")
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
    # Vincula la cookie al navegador: si el User-Agent actual no coincide
    # con el registrado, se rechaza y se vuelve a exigir 2FA.
    dispositivo_actual = request.headers.get("User-Agent", "")[:500]
    conn = None
    cursor = None
    try:
        conn = conexion()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT dispositivo_info FROM dispositivos_confiables WHERE id_usuario = %s AND token_hash = %s",
            (id_usuario, token_hash)
        )
        row = cursor.fetchone()
        if row and row[0] == dispositivo_actual:
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


# ---------------------------------------------------------------------------
# Funciones auxiliares para el secreto TOTP pendiente durante el login (2FA)
# El secreto se guarda en servidor (BD), no en la cookie de sesión.
# ---------------------------------------------------------------------------

def _guardar_pendiente_2fa(id_usuario, totp_secret, ttl_minutos=None):
    """Guarda el secreto TOTP en la BD y devuelve un token de un solo uso."""
    token = secrets.token_urlsafe(32)
    expira = datetime.now() + timedelta(minutes=(ttl_minutos or TOKEN_2FA_TTL_MINUTOS))
    conn = None
    cursor = None
    try:
        conn = conexion()
        cursor = conn.cursor()
        # Limpieza perezosa de tokens expirados
        cursor.execute("DELETE FROM login_2fa_pendiente WHERE expira < NOW()")
        cursor.execute(
            "INSERT INTO login_2fa_pendiente (token, id_usuario, totp_secret, expira) VALUES (%s, %s, %s, %s)",
            (token, id_usuario, totp_secret, expira)
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


def _obtener_secret_2fa(token):
    """Devuelve el secreto TOTP asociado al token si existe y no expiró."""
    if not token:
        return None
    conn = None
    cursor = None
    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT totp_secret, expira FROM login_2fa_pendiente WHERE token = %s",
            (token,)
        )
        row = cursor.fetchone()
        if not row:
            return None
        if row["expira"] and row["expira"] < datetime.now():
            cursor.execute("DELETE FROM login_2fa_pendiente WHERE token = %s", (token,))
            conn.commit()
            return None
        return row["totp_secret"]
    except Exception:
        return None
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


def _eliminar_pendiente_2fa(token):
    """Elimina el token pendiente de 2FA (un solo uso)."""
    if not token:
        return
    conn = None
    cursor = None
    try:
        conn = conexion()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM login_2fa_pendiente WHERE token = %s", (token,))
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
@limiter.limit("5 per minute")
def login():
    _purga_periodica()

    if request.method == "POST":
        usuario = request.form.get("usuario", "").strip()
        password = request.form.get("password", "").strip()

        if len(usuario) > 100 or len(password) > 128:
            return render_template(
                "login.html",
                error="Credenciales inválidas"
            )

        # Identificador de bloqueo: usuario + IP (evita que un tercero bloquee la cuenta de otro)
        ip = request.remote_addr or ""
        identificador_bloqueo = f"{usuario.lower()}|{ip}"

        # Bloqueo temporal con duración progresiva según los ciclos previos de la cuenta
        minutos_bloqueo = _minutos_bloqueo_actual(usuario)
        if _esta_bloqueado(identificador_bloqueo, minutos=minutos_bloqueo):
            if minutos_bloqueo >= 1440:
                mensaje = "Demasiados intentos fallidos. Intente de nuevo en 24 horas."
            else:
                mensaje = f"Demasiados intentos fallidos. Intente de nuevo en {minutos_bloqueo} minutos."
            return render_template(
                "login.html",
                error=mensaje
            )

        conn = None
        cursor = None

        try:
            conn = conexion()
            cursor = conn.cursor(dictionary=True)

            # Búsqueda por usuario o por correo (un único criterio, sin ambigüedad)
            if "@" in usuario:
                filtro_identidad = "WHERE LOWER(u.correo) = %s"
                param_identidad = usuario.lower()
            else:
                filtro_identidad = "WHERE u.usuario = %s"
                param_identidad = usuario

            cursor.execute(f"""
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
                    u.cuenta_bloqueada,
                    u.session_version,
                    r.nombre_rol
                FROM usuarios u
                INNER JOIN usuarios_roles ur
                ON u.id_usuario = ur.id_usuario
                INNER JOIN roles r
                ON ur.id_rol = r.id_rol
                {filtro_identidad}
            """, (param_identidad,))

            resultados = cursor.fetchall()

            user = resultados[0] if resultados else None

            # Bloqueo duro de cuenta: si fue marcada por intentos fallidos repetidos,
            # solo un administrador puede reactivarla. Se informa genéricamente para
            # no confirmar la existencia del usuario.
            if user and user.get("cuenta_bloqueada"):
                return render_template(
                    "login.html",
                    error="Cuenta bloqueada por seguridad. Contacte al administrador."
                )

            # Comparar siempre contra un hash (real o dummy) para igualar tiempos de respuesta
            if user and user["estado"] == "activo":
                hash_a_comparar = user["password"]
            else:
                hash_a_comparar = _DUMMY_PASSWORD_HASH

            password_ok = check_password_hash(hash_a_comparar, password)

            if not user or user["estado"] != "activo" or not password_ok:
                _registrar_fallo(identificador_bloqueo)
                # Si este fallo cruza el umbral de bloqueo temporal, escala el
                # contador de bloqueos consecutivos de la cuenta.
                if _contar_intentos_db(identificador_bloqueo, minutos=minutos_bloqueo) >= MAX_INTENTOS_LOGIN:
                    _escalar_bloqueo_cuenta(usuario)
                return render_template(
                    "login.html",
                    error="Credenciales inválidas"
                )

            _limpiar_bloqueo(identificador_bloqueo)
            _resetear_bloqueos_cuenta(user["id_usuario"])

            session.clear()

            roles = []
            for r in resultados:
                roles.append(r["nombre_rol"])

            dos_factores = user.get("dos_factores_activo") if "dos_factores_activo" in user else False

            if dos_factores:
                if not user.get("totp_secret"):
                    return render_template(
                        "login.html",
                        error="Error del sistema. Contacte al administrador."
                    )
                dispositivo_confiable = _verificar_dispositivo_confiable(user["id_usuario"])
                if dispositivo_confiable:
                    _actualizar_ultimo_uso_dispositivo(user["id_usuario"], dispositivo_confiable)
                else:
                    token = _guardar_pendiente_2fa(user["id_usuario"], user["totp_secret"])
                    if not token:
                        return render_template(
                            "login.html",
                            error="Error del sistema. Intente nuevamente."
                        )
                    session["_2fa_pendiente"] = {
                        "id_usuario": user["id_usuario"],
                        "usuario": user["usuario"],
                        "nombre": f"{user['nombre']} {user['apellidos']}",
                        "foto": user.get("foto") or "",
                        "roles": roles,
                        "session_version": user.get("session_version", 0),
                        "token": token,
                    }
                    session["_2fa_pendiente_expira"] = (datetime.now() + timedelta(minutes=TOKEN_2FA_TTL_MINUTOS)).timestamp()
                    return redirect(url_for("login.verificar_2fa"))

            session["id_usuario"] = user["id_usuario"]
            session["usuario"] = user["usuario"]
            session["nombre"] = (
                f"{user['nombre']} {user['apellidos']}"
            )
            session["foto"] = user.get("foto") or ""
            session["roles"] = roles
            session["session_version"] = user.get("session_version", 0)
            session.permanent = True

            registrar_auditoria("login", f"Inicio de sesión: {user['usuario']}")

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
@login_bp.route("/activar_perfil/<rol>", methods=["POST"])
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
@login_bp.route("/logout", methods=["POST"])
def logout():
    registrar_auditoria("logout", f"Cierre de sesión: {session.get('usuario', '')}")
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

        # Bloqueo por demasiados códigos incorrectos (además del límite por IP)
        id_2fa_bloqueo = f"2fa:{pendiente['id_usuario']}"
        if _esta_bloqueado(id_2fa_bloqueo, MAX_INTENTOS_2FA):
            session.pop("_2fa_pendiente", None)
            session.pop("_2fa_pendiente_expira", None)
            _eliminar_pendiente_2fa(pendiente.get("token"))
            return redirect(url_for("login.login"))

        secret = _obtener_secret_2fa(pendiente.get("token"))
        if not secret:
            session.pop("_2fa_pendiente", None)
            session.pop("_2fa_pendiente_expira", None)
            return redirect(url_for("login.login"))

        totp = pyotp.TOTP(secret)
        if not totp.verify(codigo, valid_window=1):
            _registrar_fallo(id_2fa_bloqueo)
            return render_template(
                "verificar_2fa.html",
                error="Código inválido. Intente nuevamente."
            )

        _eliminar_pendiente_2fa(pendiente.get("token"))
        session.pop("_2fa_pendiente", None)
        session.pop("_2fa_pendiente_expira", None)
        session["id_usuario"] = pendiente["id_usuario"]
        session["usuario"] = pendiente["usuario"]
        session["nombre"] = pendiente["nombre"]
        session["foto"] = pendiente["foto"]
        session["roles"] = pendiente["roles"]
        session["session_version"] = pendiente.get("session_version", 0)
        session.permanent = True

        registrar_auditoria("login_2fa", f"Inicio de sesión con 2FA: {pendiente['usuario']}")

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

# Genera el SVG del código QR a partir de un secreto TOTP
def _generar_qr_svg(secret, usuario):
    totp = pyotp.TOTP(secret)
    usuario_seguro = "".join(c if c.isalnum() or c in ".-_@" else "_" for c in (usuario or "usuario"))
    uri = totp.provisioning_uri(name=usuario_seguro, issuer_name="SIGGO-OIG")
    factory = qrcode.image.svg.SvgPathImage
    img = qrcode.make(uri, image_factory=factory)
    buffer = BytesIO()
    img.save(buffer)
    return buffer.getvalue().decode("utf-8")


# Devuelve el secreto temporal si el token de configuración sigue vigente.
# El secreto vive en la BD (no en la cookie); la sesión solo guarda el token.
def _temp_secret_2fa_valido():
    token = session.get("_2fa_temp_token")
    if not token:
        return None
    secret = _obtener_secret_2fa(token)
    if not secret:
        session.pop("_2fa_temp_token", None)
    return secret


@login_bp.route("/configurar_2fa", methods=["GET", "POST"])
def configurar_2fa():
    if "usuario" not in session:
        return redirect(url_for("login.login"))

    conn = None
    cursor = None

    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        # Obtiene el hash de la contraseña actual para reautenticar al usuario
        cursor.execute(
            "SELECT password FROM usuarios WHERE id_usuario = %s",
            (session["id_usuario"],)
        )
        user_row = cursor.fetchone()

        if request.method == "POST":
            password = request.form.get("password", "")
            codigo = request.form.get("codigo", "").strip()

            # PASO 1: confirmar identidad con la contraseña actual antes de mostrar el QR
            if password:
                if not user_row or not check_password_hash(user_row["password"], password):
                    return render_template(
                        "configurar_2fa.html",
                        error="Contraseña incorrecta."
                    )

                secret = pyotp.random_base32()
                # El secreto se guarda en la BD; en la sesión solo queda un token opaco.
                token = _guardar_pendiente_2fa(session["id_usuario"], secret, ttl_minutos=10)
                if not token:
                    return render_template(
                        "configurar_2fa.html",
                        error="Error del sistema. Intente nuevamente."
                    )
                session["_2fa_temp_token"] = token
                qr_svg = _generar_qr_svg(secret, session.get("usuario", "usuario"))

                return render_template(
                    "configurar_2fa.html",
                    secret=secret,
                    qr_svg=qr_svg,
                    qr_mostrado=True
                )

            # PASO 2: verificar el código TOTP y activar el 2FA
            secret_temporal = _temp_secret_2fa_valido()
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
            _eliminar_pendiente_2fa(session.pop("_2fa_temp_token", None))
            registrar_auditoria("2fa_activado", f"2FA activado para {session.get('usuario', '')}")

            return render_template(
                "configurar_2fa.html",
                mensaje="Autenticación en dos pasos activada correctamente.",
                configurado=True
            )

        # GET: si ya se confirmó la contraseña, muestra el QR; si no, pide la contraseña
        secret_temporal = _temp_secret_2fa_valido()
        if secret_temporal:
            qr_svg = _generar_qr_svg(secret_temporal, session.get("usuario", "usuario"))
            return render_template(
                "configurar_2fa.html",
                secret=secret_temporal,
                qr_svg=qr_svg,
                qr_mostrado=True
            )

        return render_template("configurar_2fa.html")

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


# Cancela la configuración de 2FA en curso y vuelve al perfil
@login_bp.route("/cancelar_configurar_2fa", methods=["POST"])
def cancelar_configurar_2fa():
    _eliminar_pendiente_2fa(session.pop("_2fa_temp_token", None))
    return redirect(url_for("perfil.editar_mi_perfil"))


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

    password = request.form.get("password", "")

    conn = None
    cursor = None

    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            "SELECT password FROM usuarios WHERE id_usuario = %s",
            (session["id_usuario"],)
        )
        user_row = cursor.fetchone()

        # Reautenticación obligatoria antes de desactivar el 2FA
        if not user_row or not check_password_hash(user_row["password"], password):
            if ajax:
                return jsonify({"success": False, "message": "Contraseña incorrecta."}), 400
            flash("Contraseña incorrecta.", "error")
            return redirect(url_for("perfil.editar_mi_perfil"))

        cursor.execute("""
            UPDATE usuarios
            SET totp_secret = NULL, dos_factores_activo = FALSE
            WHERE id_usuario = %s
        """, (session["id_usuario"],))

        conn.commit()

        _eliminar_dispositivos_confiables(session["id_usuario"])
        registrar_auditoria("2fa_desactivado", f"2FA desactivado para {session.get('usuario', '')}")

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
