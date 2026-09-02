from flask import Blueprint, render_template, request, session, redirect, url_for, flash
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename
from conexion import conexion
from utils.validators import validar_mime_real, sanitizar_nombre, validar_longitudes
import os
from datetime import datetime

"""
Blueprint de gestión de fiscalizadores/usuarios: listar, editar,
actualizar, eliminar, toggle activo/inactivo.
"""

fiscalizadores_bp = Blueprint("fiscalizadores", __name__)

FOTOS_FOLDER = os.path.join("static", "fotos")
ALLOWED_PHOTO_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_PHOTO_SIZE = 5 * 1024 * 1024  # 5 MB

os.makedirs(FOTOS_FOLDER, exist_ok=True)


# Verifica que la extensión del archivo esté en la lista blanca de formatos de imagen
def foto_permitida(nombre):
    return (
        "." in nombre
        and nombre.rsplit(".", 1)[1].lower() in ALLOWED_PHOTO_EXTENSIONS
    )

# ==========================
# LISTAR FISCALIZADORES (ADMIN)
# ==========================
# Muestra todos los usuarios con sus roles en la vista de administración
@fiscalizadores_bp.route("/admin/fiscalizadores")
def listar_fiscalizadores():

    if "usuario" not in session or session.get("perfil_activo") != "admin":
        return redirect(url_for("login.login"))

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
                u.correo,
                u.usuario,
                u.estado,
                u.foto,
                GROUP_CONCAT(
                    r.nombre_rol
                    ORDER BY r.nombre_rol
                    SEPARATOR ', '
                ) AS roles
            FROM usuarios u
            LEFT JOIN usuarios_roles ur
                ON u.id_usuario = ur.id_usuario
            LEFT JOIN roles r
                ON ur.id_rol = r.id_rol
            GROUP BY
                u.id_usuario,
                u.nombre,
                u.apellidos,
                u.correo,
                u.usuario,
                u.estado,
                u.foto
            ORDER BY u.nombre
        """)

        lista_fiscalizadores = cursor.fetchall()

        return render_template(
            "fiscalizadores.html",
            fiscalizadores=lista_fiscalizadores
        )

    except Exception as e:

        print("ERROR LISTAR FISCALIZADORES:", e)

        return render_template(
            "fiscalizadores.html",
            error="Error interno del servidor. Intente nuevamente.",
            fiscalizadores=[]
        )

    finally:

        if cursor is not None: cursor.close()
        if conn is not None: conn.close()

# ==========================
# EDITAR USUARIO (GET)
# ==========================
# Carga los datos de un usuario específico para mostrar el formulario de edición
@fiscalizadores_bp.route("/editar_usuario/<int:id>")
def editar_usuario(id):

    if "usuario" not in session or session.get("perfil_activo") != "admin":
        return redirect(url_for("login.login"))

    conn = None
    cursor = None

    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT u.*, GROUP_CONCAT(r.nombre_rol ORDER BY r.nombre_rol SEPARATOR ', ') AS roles
            FROM usuarios u
            LEFT JOIN usuarios_roles ur ON u.id_usuario = ur.id_usuario
            LEFT JOIN roles r ON ur.id_rol = r.id_rol
            WHERE u.id_usuario = %s
            GROUP BY u.id_usuario
        """, (id,))

        usuario = cursor.fetchone()

        if not usuario:
            return redirect(url_for("fiscalizadores.listar_fiscalizadores"))

        return render_template(
            "editar_fiscalizador.html",
            usuario=usuario
        )

    except Exception as e:

        print("ERROR EDITAR USUARIO:", e)

        return redirect(url_for("fiscalizadores.listar_fiscalizadores"))

    finally:

        if cursor is not None: cursor.close()
        if conn is not None: conn.close()

# ==========================
# ACTUALIZAR USUARIO (POST)
# ==========================
# Procesa el formulario de edición: actualiza datos, contraseña, foto y rol del usuario
@fiscalizadores_bp.route("/actualizar_usuario/<int:id>", methods=["POST"])
def actualizar_usuario(id):

    if "usuario" not in session or session.get("perfil_activo") != "admin":
        return redirect(url_for("login.login"))

    nombre = request.form.get("nombre", "").strip()
    apellidos = request.form.get("apellidos", "").strip()
    correo = request.form.get("correo", "").strip()
    usuario_form = request.form.get("usuario", "").strip()
    password = request.form.get("password", "").strip()
    rol = request.form.get("rol")
    estado = request.form.get("estado")
    fecha_ingreso = request.form.get("fecha_ingreso")

    conn = None
    cursor = None

    try:
        valido, msg = validar_longitudes({
            "nombre": nombre,
            "apellidos": apellidos,
            "correo": correo,
            "usuario": usuario_form,
        })
        if not valido:
            return render_template(
                "editar_fiscalizador.html",
                error=msg,
                usuario=request.form
            )

        conn = conexion()
        cursor = conn.cursor()

        id_rol = None

        # Buscar el id del rol a partir del nombre de rol enviado en el formulario
        if rol:

            cursor.execute("""
                SELECT id_rol
                FROM roles
                WHERE nombre_rol = %s
            """, (rol,))

            data = cursor.fetchone()

            if data:
                id_rol = data[0]

        # Si se proporcionó una nueva contraseña, generar hash y actualizar junto con los demás datos
        if password:

            password_hash = generate_password_hash(password)

            cursor.execute("""
                UPDATE usuarios
                SET nombre=%s, apellidos=%s, correo=%s,
                    usuario=%s, password=%s,
                    estado=%s, fecha_ingreso=%s
                WHERE id_usuario=%s
            """, (
                nombre, apellidos, correo,
                usuario_form, password_hash,
                estado, fecha_ingreso, id
            ))

        # Si no se envió contraseña, actualizar todos los campos excepto el password
        else:

            cursor.execute("""
                UPDATE usuarios
                SET nombre=%s, apellidos=%s, correo=%s,
                    usuario=%s,
                    estado=%s, fecha_ingreso=%s
                WHERE id_usuario=%s
            """, (
                nombre, apellidos, correo,
                usuario_form,
                estado, fecha_ingreso, id
            ))

        # Guardar la foto de perfil si el archivo es válido y no excede el tamaño máximo
        foto = request.files.get("foto")
        if foto and foto.filename and foto_permitida(foto.filename):
            contenido = foto.read()
            if len(contenido) <= MAX_PHOTO_SIZE:
                ext = foto.filename.rsplit(".", 1)[1].lower()
                if not validar_mime_real(contenido[:12], ext):
                    return render_template(
                        "editar_fiscalizador.html",
                        error="El contenido de la imagen no coincide con su extensión",
                        usuario=request.form
                    )
                nombre_foto = f"{id}_{datetime.now().strftime('%Y%m%d%H%M%S')}.{ext}"
                ruta_foto = os.path.join(FOTOS_FOLDER, nombre_foto)
                with open(ruta_foto, "wb") as f:
                    f.write(contenido)

                cursor.execute(
                    "UPDATE usuarios SET foto = %s WHERE id_usuario = %s",
                    (nombre_foto, id)
                )

        # Reasignar el rol del usuario: eliminar roles previos e insertar el nuevo
        # (evita que un administrador cambie su propio rol)
        if id_rol and id != session.get("id_usuario"):

            cursor.execute("""
                DELETE FROM usuarios_roles
                WHERE id_usuario = %s
            """, (id,))

            cursor.execute("""
                INSERT INTO usuarios_roles (id_usuario, id_rol)
                VALUES (%s, %s)
            """, (id, id_rol))

            # Si el rol seleccionado es "admin", asignar también el rol "fiscalizador" automáticamente
            if rol == "admin":

                cursor.execute("SELECT id_rol FROM roles WHERE nombre_rol = 'fiscalizador'")

                fiscalizador_id = cursor.fetchone()[0]

                cursor.execute("""
                    INSERT INTO usuarios_roles (id_usuario, id_rol)
                    VALUES (%s, %s)
                """, (id, fiscalizador_id))

        elif id_rol and id == session.get("id_usuario") and rol not in session.get("roles", []):
            flash("No puedes cambiar tu propio rol", "error")

        conn.commit()

        flash("Usuario actualizado correctamente", "success")
        return redirect(url_for("fiscalizadores.listar_fiscalizadores"))

    except Exception as e:

        if conn is not None: conn.rollback()

        print("ERROR ACTUALIZAR USUARIO:", e)

        return render_template(
            "editar_fiscalizador.html",
            error="Error interno del servidor. Intente nuevamente.",
            usuario=request.form
        )

    finally:

        if cursor is not None: cursor.close()
        if conn is not None: conn.close()

# ==========================
# ELIMINAR USUARIO
# ==========================
# Elimina un usuario si no tiene guardias; si las tiene, solo cambia su estado (toggle activo/inactivo)
@fiscalizadores_bp.route("/eliminar_usuario/<int:id>", methods=["POST"])
def eliminar_usuario(id):

    if "usuario" not in session or session.get("perfil_activo") != "admin":
        return redirect(url_for("login.login"))

    conn = None
    cursor = None

    try:
        # Evitar que un admin se elimine a sí mismo
        if id == session.get("id_usuario"):
            flash("No puedes eliminar tu propia cuenta", "error")
            return redirect(url_for("fiscalizadores.listar_fiscalizadores"))

        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                u.estado,
                (SELECT COUNT(*) FROM guardias WHERE id_usuario = %s) AS cnt
            FROM usuarios u
            WHERE u.id_usuario = %s
        """, (id, id))

        usuario = cursor.fetchone()

        if not usuario:
            flash("Usuario no encontrado", "error")
            return redirect(url_for("fiscalizadores.listar_fiscalizadores"))

        # Si el usuario tiene guardias registradas, alternar estado en lugar de eliminar
        if usuario["cnt"] > 0:
            nuevo_estado = (
                "inactivo"
                if usuario["estado"] == "activo"
                else "activo"
            )
            cursor.execute("""
                UPDATE usuarios
                SET estado = %s
                WHERE id_usuario = %s
            """, (nuevo_estado, id))
            conn.commit()
            flash(f"Usuario {'desactivado' if nuevo_estado == 'inactivo' else 'activado'} (tiene guardias registradas)", "success")
        else:
            cursor.execute(
                "DELETE FROM usuarios_roles WHERE id_usuario = %s",
                (id,)
            )
            cursor.execute(
                "DELETE FROM usuarios WHERE id_usuario = %s",
                (id,)
            )
            conn.commit()
            flash("Usuario eliminado correctamente", "success")

        return redirect(url_for("fiscalizadores.listar_fiscalizadores"))

    except Exception as e:

        if conn is not None: conn.rollback()

        print("ERROR ELIMINAR USUARIO:", e)
        flash("Error interno del servidor al eliminar usuario", "error")

        return redirect(url_for("fiscalizadores.listar_fiscalizadores"))

    finally:

        if cursor is not None: cursor.close()
        if conn is not None: conn.close()

# ==========================
# TOGGLE USUARIO (ACTIVAR / DESACTIVAR)
# ==========================
# Alterna el estado de un usuario entre activo e inactivo
@fiscalizadores_bp.route("/toggle_usuario/<int:id>", methods=["POST"])
def toggle_usuario(id):

    if "usuario" not in session or session.get("perfil_activo") != "admin":
        return redirect(url_for("login.login"))

    conn = None
    cursor = None

    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT estado
            FROM usuarios
            WHERE id_usuario = %s
        """, (id,))

        usuario = cursor.fetchone()

        if not usuario:

            return redirect(url_for("fiscalizadores.listar_fiscalizadores"))

        # Evitar que un admin se desactive a sí mismo
        if id == session.get("id_usuario"):
            flash("No puedes desactivar tu propia cuenta", "error")
            return redirect(url_for("fiscalizadores.listar_fiscalizadores"))

        nuevo_estado = (
            "inactivo"
            if usuario["estado"] == "activo"
            else "activo"
        )

        cursor.execute("""
            UPDATE usuarios
            SET estado = %s
            WHERE id_usuario = %s
        """, (nuevo_estado, id))

        conn.commit()

        flash(f"Usuario {'activado' if nuevo_estado == 'activo' else 'desactivado'} correctamente", "success")

        return redirect(url_for("fiscalizadores.listar_fiscalizadores"))

    except Exception as e:

        if conn is not None: conn.rollback()

        print("ERROR TOGGLE USUARIO:", e)
        flash("Error interno del servidor al cambiar estado", "error")

        return redirect(url_for("fiscalizadores.listar_fiscalizadores"))

    finally:

        if cursor is not None: cursor.close()
        if conn is not None: conn.close()

# ==========================
# LISTA PÚBLICA FISCALIZADORES
# ==========================
# Muestra la lista de fiscalizadores para usuarios autenticados (vista de solo lectura)
@fiscalizadores_bp.route("/fiscalizadores")
def fiscalizadores():

    if "usuario" not in session:
        return redirect(url_for("login.login"))

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
                u.correo,
                u.estado,
                u.foto
            FROM usuarios u
            LEFT JOIN usuarios_roles ur
                ON u.id_usuario = ur.id_usuario
            LEFT JOIN roles r
                ON ur.id_rol = r.id_rol
            WHERE r.nombre_rol = 'fiscalizador'
            ORDER BY u.nombre
        """)

        lista_fiscalizadores = cursor.fetchall()

        return render_template(
            "lista_fiscalizadores.html",
            fiscalizadores=lista_fiscalizadores
        )

    except Exception as e:

        print("ERROR LISTA FISCALIZADORES:", e)

        return render_template(
            "lista_fiscalizadores.html",
            error="Error interno del servidor. Intente nuevamente.",
            fiscalizadores=[]
        )

    finally:

        if cursor is not None: cursor.close()
        if conn is not None: conn.close()
