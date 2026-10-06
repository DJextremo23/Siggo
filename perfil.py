from flask import Blueprint, render_template, request, session, redirect, url_for, flash
from werkzeug.security import generate_password_hash, check_password_hash
from conexion import conexion
from utils.validators import validar_mime_real, validar_longitudes, password_segura
from utils import registrar_auditoria
import os
from datetime import datetime

# Blueprint para edición del perfil propio del usuario: ver y actualizar datos personales y foto
perfil_bp = Blueprint("perfil", __name__)

# Carpeta donde se almacenan las fotos de perfil
FOTOS_FOLDER = os.getenv("FOTOS_FOLDER", os.path.join("static", "fotos"))
# Extensiones de imagen permitidas para la foto de perfil
ALLOWED_PHOTO_EXTENSIONS = {"png", "jpg", "jpeg", "jfif", "gif", "webp"}
# Tamaño máximo de foto: 5 MB
MAX_PHOTO_SIZE = 5 * 1024 * 1024

os.makedirs(FOTOS_FOLDER, exist_ok=True)


# Verifica que el nombre del archivo tenga una extensión de imagen permitida
def foto_permitida(nombre):
    return (
        "." in nombre
        and nombre.rsplit(".", 1)[1].lower() in ALLOWED_PHOTO_EXTENSIONS
    )


# Muestra el formulario de edición del perfil con los datos actuales del usuario
@perfil_bp.route("/mi_perfil")
def editar_mi_perfil():

    # Redirige al login si no hay sesión activa
    if "usuario" not in session:
        return redirect(url_for("login.login"))

    id_usuario = session.get("id_usuario")

    conn = None
    cursor = None

    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        # Consulta los datos del usuario autenticado para precargar el formulario
        cursor.execute("""
            SELECT id_usuario, nombre, apellidos, usuario, correo, foto, dos_factores_activo
            FROM usuarios
            WHERE id_usuario = %s
        """, (id_usuario,))

        usuario = cursor.fetchone()

        if not usuario:
            return redirect(url_for("inicio"))

        return render_template(
            "editar_mi_perfil.html",
            usuario=usuario
        )

    except Exception as e:

        print("ERROR EDITAR MI PERFIL:", e)

        return redirect(url_for("inicio"))

    finally:

        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


# Procesa el formulario POST para actualizar nombre, apellidos, correo, usuario, contraseña y foto
@perfil_bp.route("/actualizar_mi_perfil", methods=["POST"])
def actualizar_mi_perfil():

    # Redirige al login si no hay sesión activa
    if "usuario" not in session:
        return redirect(url_for("login.login"))

    id_usuario = session.get("id_usuario")

    nombre = request.form.get("nombre", "").strip()
    apellidos = request.form.get("apellidos", "").strip()
    correo = request.form.get("correo", "").strip().lower()
    usuario_form = request.form.get("usuario", "").strip()
    password = request.form.get("password", "").strip()
    password_actual = request.form.get("password_actual", "").strip()

    # Helper interno: reconstruye el diccionario de usuario para re-renderizar el formulario en caso de error
    def _datos_error(dos_factores_activo=None, foto=None):
        return {
            "nombre": nombre,
            "apellidos": apellidos,
            "correo": correo,
            "usuario": usuario_form,
            "dos_factores_activo": dos_factores_activo,
            "foto": foto,
        }

    conn = None
    cursor = None
    dos_factores = None
    foto_actual = None

    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        # Obtiene los valores actuales de dos_factores y foto para pasarlos al helper de error
        cursor.execute(
            "SELECT dos_factores_activo, foto, password, correo FROM usuarios WHERE id_usuario = %s",
            (id_usuario,)
        )
        current = cursor.fetchone()
        dos_factores = current["dos_factores_activo"] if current else None
        foto_actual = current["foto"] if current else None
        password_hash_actual = current["password"] if current else None
        correo_actual = current["correo"] if current else ""

        # Valida que los campos no excedan las longitudes máximas permitidas
        valido, msg = validar_longitudes({
            "nombre": nombre,
            "apellidos": apellidos,
            "correo": correo,
            "usuario": usuario_form,
        })
        if not valido:
            return render_template(
                "editar_mi_perfil.html",
                error=msg,
                usuario=_datos_error(dos_factores, foto_actual)
            )

        # Validar que la contraseña nueva cumpla los requisitos de seguridad
        if password and not password_segura(password):
            return render_template(
                "editar_mi_perfil.html",
                error="La contraseña es débil: mínimo 10 caracteres, con mayúscula, minúscula, número y símbolo.",
                usuario=_datos_error(dos_factores, foto_actual)
            )

        # Validar que el correo y el nombre de usuario no estén en uso por otra cuenta
        cursor.execute("""
            SELECT id_usuario
            FROM usuarios
            WHERE (LOWER(correo) = %s OR usuario = %s)
              AND id_usuario != %s
            LIMIT 1
        """, (correo, usuario_form, id_usuario))
        if cursor.fetchone():
            return render_template(
                "editar_mi_perfil.html",
                error="El correo o el nombre de usuario ya están en uso por otra cuenta.",
                usuario=_datos_error(dos_factores, foto_actual)
            )

        # Si cambia contraseña o correo, exige reautenticación con la contraseña actual
        cambia_correo = bool(correo_actual) and correo.lower() != correo_actual.lower()
        if password or cambia_correo:
            if not password_hash_actual or not check_password_hash(password_hash_actual, password_actual):
                return render_template(
                    "editar_mi_perfil.html",
                    error="Debe ingresar su contraseña actual para cambiar la contraseña o el correo.",
                    usuario=_datos_error(dos_factores, foto_actual)
                )

        # Si se proporcionó contraseña, se actualiza también el hash de la contraseña
        # y se incrementa session_version para invalidar las demás sesiones activas.
        if password:

            password_hash = generate_password_hash(password)

            cursor.execute("""
                UPDATE usuarios
                SET nombre=%s, apellidos=%s, correo=%s,
                    usuario=%s, password=%s,
                    session_version = session_version + 1
                WHERE id_usuario=%s
            """, (
                nombre, apellidos, correo,
                usuario_form, password_hash,
                id_usuario
            ))

        else:

            cursor.execute("""
                UPDATE usuarios
                SET nombre=%s, apellidos=%s, correo=%s,
                    usuario=%s
                WHERE id_usuario=%s
            """, (
                nombre, apellidos, correo,
                usuario_form, id_usuario
            ))

        # Procesa la foto de perfil si se subió un archivo
        foto = request.files.get("foto")
        if foto and foto.filename:

            if not foto_permitida(foto.filename):
                return render_template(
                    "editar_mi_perfil.html",
                    error="Formato de imagen no válido. Formatos permitidos: PNG, JPG, JPEG, JFIF, GIF o WEBP.",
                    usuario=_datos_error(dos_factores, foto_actual)
                )

            # Rechaza antes de leer el archivo completo si el tamaño declarado excede el límite
            if foto.content_length and foto.content_length > MAX_PHOTO_SIZE:
                return render_template(
                    "editar_mi_perfil.html",
                    error="La imagen excede el tamaño máximo permitido (5 MB).",
                    usuario=_datos_error(dos_factores, foto_actual)
                )

            contenido = foto.read()

            if len(contenido) > MAX_PHOTO_SIZE:
                return render_template(
                    "editar_mi_perfil.html",
                    error="La imagen excede el tamaño máximo permitido (5 MB).",
                    usuario=_datos_error(dos_factores, foto_actual)
                )

            ext = foto.filename.rsplit(".", 1)[1].lower()
            # Verifica que el contenido real del archivo coincida con la extensión declarada
            if not validar_mime_real(contenido[:12], ext):
                return render_template(
                    "editar_mi_perfil.html",
                    error="El contenido de la imagen no coincide con su extensión",
                    usuario=_datos_error(dos_factores, foto_actual)
                )

            # Genera un nombre único para la foto y la guarda en disco
            nombre_foto = f"{id_usuario}_{datetime.now().strftime('%Y%m%d%H%M%S')}.{ext}"
            ruta_foto = os.path.join(FOTOS_FOLDER, nombre_foto)
            with open(ruta_foto, "wb") as f:
                f.write(contenido)

            cursor.execute(
                "UPDATE usuarios SET foto = %s WHERE id_usuario = %s",
                (nombre_foto, id_usuario)
            )

            session["foto"] = nombre_foto

        conn.commit()

        # Actualiza los datos de sesión con los nuevos valores
        session["nombre"] = f"{nombre} {apellidos}"
        session["usuario"] = usuario_form
        # Si cambió la contraseña, la sesión actual adopta la nueva versión
        # (las demás sesiones quedan invalidadas por el incremento en BD).
        if password:
            session["session_version"] = session.get("session_version", 0) + 1

        flash("Perfil actualizado correctamente", "success")

        return redirect(url_for("inicio"))

    except Exception as e:

        # Revierte cualquier cambio pendiente en la base de datos
        if conn is not None:
            conn.rollback()

        print("ERROR ACTUALIZAR MI PERFIL:", e)

        return render_template(
            "editar_mi_perfil.html",
            error="Error interno del servidor. Intente nuevamente.",
            usuario=_datos_error(dos_factores, foto_actual)
        )

    finally:

        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


# Cierra la sesión en todos los demás dispositivos: incrementa session_version
# en BD (invalida las otras sesiones) y mantiene válida la sesión actual.
@perfil_bp.route("/cerrar_sesion_todos", methods=["POST"])
def cerrar_sesion_todos():

    if "usuario" not in session:
        return redirect(url_for("login.login"))

    id_usuario = session.get("id_usuario")
    if not id_usuario:
        return redirect(url_for("login.login"))

    conn = None
    cursor = None

    try:
        conn = conexion()
        cursor = conn.cursor()

        cursor.execute(
            "UPDATE usuarios SET session_version = session_version + 1 WHERE id_usuario = %s",
            (id_usuario,)
        )
        conn.commit()

        # La sesión actual adopta la nueva versión; las demás quedan invalidadas.
        session["session_version"] = session.get("session_version", 0) + 1

        registrar_auditoria("sesiones_cerradas", "Cierre de sesión en todos los dispositivos")

        flash("Se cerró la sesión en los demás dispositivos", "success")

        return redirect(url_for("perfil.editar_mi_perfil"))

    except Exception as e:
        if conn is not None:
            conn.rollback()
        print("ERROR CERRAR SESIONES:", e)
        flash("Error interno del servidor", "error")
        return redirect(url_for("perfil.editar_mi_perfil"))

    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()
