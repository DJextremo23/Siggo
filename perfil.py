from flask import Blueprint, render_template, request, session, redirect, url_for, flash
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename
from conexion import conexion
from utils.validators import validar_mime_real, sanitizar_nombre, validar_longitudes
import os
from datetime import datetime

# Blueprint para edición del perfil propio del usuario: ver y actualizar datos personales y foto
perfil_bp = Blueprint("perfil", __name__)

# Carpeta donde se almacenan las fotos de perfil
FOTOS_FOLDER = os.path.join("static", "fotos")
# Extensiones de imagen permitidas para la foto de perfil
ALLOWED_PHOTO_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
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
    correo = request.form.get("correo", "").strip()
    usuario_form = request.form.get("usuario", "").strip()
    password = request.form.get("password", "").strip()

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
            "SELECT dos_factores_activo, foto FROM usuarios WHERE id_usuario = %s",
            (id_usuario,)
        )
        current = cursor.fetchone()
        dos_factores = current["dos_factores_activo"] if current else None
        foto_actual = current["foto"] if current else None

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

        # Si se proporcionó contraseña, se actualiza también el hash de la contraseña
        if password:

            password_hash = generate_password_hash(password)

            cursor.execute("""
                UPDATE usuarios
                SET nombre=%s, apellidos=%s, correo=%s,
                    usuario=%s, password=%s
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

        # Procesa la foto de perfil si se subió un archivo válido
        foto = request.files.get("foto")
        if foto and foto.filename and foto_permitida(foto.filename):
            contenido = foto.read()
            if len(contenido) <= MAX_PHOTO_SIZE:
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
