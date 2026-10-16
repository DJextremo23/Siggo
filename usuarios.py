from flask import Blueprint, render_template, request, session, redirect, url_for, flash
from werkzeug.security import generate_password_hash, check_password_hash
from conexion import conexion
from utils.validators import validar_mime_real, validar_longitudes, password_segura, generar_password_temporal
from utils import registrar_auditoria
from limiter_instance import limiter
import os
from datetime import datetime

"""
Blueprint de gestión de usuarios: listar, editar, actualizar,
eliminar, toggle activo/inactivo.
"""

usuarios_bp = Blueprint("usuarios", __name__)

FOTOS_FOLDER = os.getenv("FOTOS_FOLDER", os.path.join("static", "fotos"))
ALLOWED_PHOTO_EXTENSIONS = {"png", "jpg", "jpeg", "jfif", "gif", "webp"}
MAX_PHOTO_SIZE = 5 * 1024 * 1024  # 5 MB

os.makedirs(FOTOS_FOLDER, exist_ok=True)


# Verifica que la extensión del archivo esté en la lista blanca de formatos de imagen
def foto_permitida(nombre):
    return (
        "." in nombre
        and nombre.rsplit(".", 1)[1].lower() in ALLOWED_PHOTO_EXTENSIONS
    )


# ==========================
# LISTAR USUARIOS (ADMIN)
# ==========================
# Muestra todos los usuarios con sus roles en la vista de administración
@usuarios_bp.route("/admin/fiscalizadores")
def listar_fiscalizadores():

    if "usuario" not in session or session.get("perfil_activo", "").lower() != "admin":
        return redirect(url_for("login.login"))

    estado = request.args.get("estado", "").strip()

    conn = None
    cursor = None

    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        # Filtro opcional por estado (activo = sin vacaciones vigentes)
        filtro_estado = ""
        if estado == "activo":
            filtro_estado = " AND u.estado = 'activo' AND v.id_vacacion IS NULL "
        elif estado == "inactivo":
            filtro_estado = " AND u.estado = 'inactivo' "

        cursor.execute(f"""
            SELECT
                u.id_usuario,
                u.nombre,
                u.apellidos,
                u.correo,
                u.usuario,
                u.estado,
                u.foto,
                u.cuenta_bloqueada,
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
            LEFT JOIN vacaciones v
                ON v.id_usuario = u.id_usuario
                AND CURDATE() BETWEEN v.fecha_inicio AND v.fecha_fin
            WHERE 1=1
            {filtro_estado}
            GROUP BY
                u.id_usuario,
                u.nombre,
                u.apellidos,
                u.correo,
                u.usuario,
                u.estado,
                u.foto,
                u.cuenta_bloqueada
            ORDER BY u.nombre
        """)

        lista_fiscalizadores = cursor.fetchall()

        return render_template(
            "fiscalizadores.html",
            fiscalizadores=lista_fiscalizadores,
            estado=estado
        )

    except Exception as e:

        print("ERROR LISTAR FISCALIZADORES:", e)

        return render_template(
            "fiscalizadores.html",
            error="Error interno del servidor. Intente nuevamente.",
            fiscalizadores=[],
            estado=estado
        )

    finally:

        if cursor is not None: cursor.close()
        if conn is not None: conn.close()

# ==========================
# EDITAR USUARIO (GET)
# ==========================
# Carga los datos de un usuario específico para mostrar el formulario de edición
@usuarios_bp.route("/editar_usuario/<int:id>")
def editar_usuario(id):

    if "usuario" not in session or session.get("perfil_activo", "").lower() != "admin":
        return redirect(url_for("login.login"))

    conn = None
    cursor = None

    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT u.id_usuario, u.nombre, u.apellidos, u.correo, u.usuario,
                   u.estado, u.foto, u.fecha_ingreso, u.dos_factores_activo,
                   GROUP_CONCAT(r.nombre_rol ORDER BY r.nombre_rol SEPARATOR ', ') AS roles
            FROM usuarios u
            LEFT JOIN usuarios_roles ur ON u.id_usuario = ur.id_usuario
            LEFT JOIN roles r ON ur.id_rol = r.id_rol
            WHERE u.id_usuario = %s
            GROUP BY u.id_usuario
        """, (id,))

        usuario = cursor.fetchone()

        if not usuario:
            return redirect(url_for("usuarios.listar_fiscalizadores"))

        return render_template(
            "editar_fiscalizador.html",
            usuario=usuario
        )

    except Exception as e:

        print("ERROR EDITAR USUARIO:", e)

        return redirect(url_for("usuarios.listar_fiscalizadores"))

    finally:

        if cursor is not None: cursor.close()
        if conn is not None: conn.close()

# ==========================
# ACTUALIZAR USUARIO (POST)
# ==========================
# Procesa el formulario de edición: actualiza datos, contraseña, foto y rol del usuario
@usuarios_bp.route("/actualizar_usuario/<int:id>", methods=["POST"])
@limiter.limit("10 per minute")
def actualizar_usuario(id):

    if "usuario" not in session or session.get("perfil_activo", "").lower() != "admin":
        return redirect(url_for("login.login"))

    nombre = request.form.get("nombre", "").strip()
    apellidos = request.form.get("apellidos", "").strip()
    correo = request.form.get("correo", "").strip().lower()
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

        # Validar que el rol sea uno de los permitidos
        # (en autoedición el select de rol está deshabilitado y no se envía)
        if rol and rol not in ("admin", "fiscalizador"):
            return render_template(
                "editar_fiscalizador.html",
                error="Rol inválido",
                usuario=request.form
            )

        # Validar que el estado sea válido.
        # En autoedición se fuerza 'activo' (un admin no puede desactivarse a sí mismo).
        if id == session.get("id_usuario"):
            estado = "activo"
        elif estado not in ("activo", "inactivo"):
            return render_template(
                "editar_fiscalizador.html",
                error="Estado inválido",
                usuario=request.form
            )

        # Validar el formato de la fecha de ingreso
        try:
            datetime.strptime(fecha_ingreso or "", "%Y-%m-%d")
        except ValueError:
            return render_template(
                "editar_fiscalizador.html",
                error="Fecha de ingreso inválida. Use YYYY-MM-DD",
                usuario=request.form
            )

        # Validar que la nueva contraseña cumpla los requisitos de seguridad
        if password and not password_segura(password):
            return render_template(
                "editar_fiscalizador.html",
                error="La contraseña es débil: mínimo 10 caracteres, con mayúscula, minúscula, número y símbolo.",
                usuario=request.form
            )

        conn = conexion()
        cursor = conn.cursor()

        # Validar que el correo y el nombre de usuario no estén en uso por otra cuenta
        cursor.execute("""
            SELECT id_usuario
            FROM usuarios
            WHERE (LOWER(correo) = %s OR usuario = %s)
              AND id_usuario != %s
            LIMIT 1
        """, (correo, usuario_form, id))
        if cursor.fetchone():
            return render_template(
                "editar_fiscalizador.html",
                error="El correo o el nombre de usuario ya están en uso por otra cuenta.",
                usuario=request.form
            )

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
        # e incrementar session_version para invalidar las sesiones activas del usuario.
        if password:

            password_hash = generate_password_hash(password)

            cursor.execute("""
                UPDATE usuarios
                SET nombre=%s, apellidos=%s, correo=%s,
                    usuario=%s, password=%s,
                    estado=%s, fecha_ingreso=%s,
                    session_version = session_version + 1
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
        foto_nueva = None
        foto = request.files.get("foto")
        if foto and foto.filename and foto_permitida(foto.filename):
            # Rechaza antes de leer el archivo completo si el tamaño declarado excede el límite
            if foto.content_length and foto.content_length > MAX_PHOTO_SIZE:
                return render_template(
                    "editar_fiscalizador.html",
                    error="La imagen excede el tamaño máximo permitido (5 MB).",
                    usuario=request.form
                )
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
                foto_nueva = nombre_foto

        # Reasignar el rol del usuario: eliminar roles previos e insertar el nuevo
        # (evita que un administrador cambie su propio rol)
        if id_rol and id != session.get("id_usuario"):

            # Detectar si el rol realmente cambia para invalidar la sesión solo
            # cuando corresponda (un cambio de rol debe cerrar la sesión activa).
            cursor.execute("""
                SELECT r.nombre_rol
                FROM usuarios_roles ur
                INNER JOIN roles r ON ur.id_rol = r.id_rol
                WHERE ur.id_usuario = %s
            """, (id,))
            roles_actuales = {fila[0] for fila in cursor.fetchall()}
            es_admin_actual = "admin" in roles_actuales
            sera_admin = (rol == "admin")

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

            # Si el rol cambió, invalidar las sesiones activas del usuario afectado
            if es_admin_actual != sera_admin:
                cursor.execute("""
                    UPDATE usuarios SET session_version = session_version + 1 WHERE id_usuario = %s
                """, (id,))

        elif id_rol and id == session.get("id_usuario") and rol not in session.get("roles", []):
            flash("No puedes cambiar tu propio rol", "error")
            conn.rollback()
            return redirect(url_for("usuarios.listar_fiscalizadores"))

        conn.commit()

        # Si el admin se editó a sí mismo desde este panel, sincronizar la sesión
        # para que el menú y las vistas reflejen los cambios sin cerrar sesión.
        if id == session.get("id_usuario"):
            session["nombre"] = f"{nombre} {apellidos}"
            session["usuario"] = usuario_form
            if foto_nueva:
                session["foto"] = foto_nueva
            if password:
                session["session_version"] = session.get("session_version", 0) + 1

        registrar_auditoria("usuario_actualizado", f"Usuario ID {id} actualizado")

        flash("Usuario actualizado correctamente", "success")
        return redirect(url_for("usuarios.listar_fiscalizadores"))

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
# Elimina un usuario si no tiene guardias ni vacaciones; si las tiene, cambia su estado (activo/inactivo)


def _cambiar_estado_usuario(cursor, id):
    """Alterna el estado (activo/inactivo) de un usuario. Devuelve el nuevo estado o None.

    Al cambiar de estado se incrementa session_version para invalidar cualquier
    sesión activa del usuario (un usuario desactivado no debe seguir navegando)."""
    cursor.execute("SELECT estado FROM usuarios WHERE id_usuario = %s", (id,))
    usuario = cursor.fetchone()
    if not usuario:
        return None
    nuevo_estado = "inactivo" if usuario["estado"] == "activo" else "activo"
    cursor.execute(
        "UPDATE usuarios SET estado = %s, session_version = session_version + 1 WHERE id_usuario = %s",
        (nuevo_estado, id)
    )
    return nuevo_estado


@usuarios_bp.route("/eliminar_usuario/<int:id>", methods=["POST"])
@limiter.limit("10 per minute")
def eliminar_usuario(id):

    if "usuario" not in session or session.get("perfil_activo", "").lower() != "admin":
        return redirect(url_for("login.login"))

    conn = None
    cursor = None

    try:
        # Evitar que un admin se elimine a sí mismo
        if id == session.get("id_usuario"):
            flash("No puedes eliminar tu propia cuenta", "error")
            return redirect(url_for("usuarios.listar_fiscalizadores"))

        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                (SELECT COUNT(*) FROM guardias WHERE id_usuario = %s) AS cnt_guardias,
                (SELECT COUNT(*) FROM vacaciones WHERE id_usuario = %s) AS cnt_vacaciones
            FROM usuarios u
            WHERE u.id_usuario = %s
        """, (id, id, id))

        usuario = cursor.fetchone()

        if not usuario:
            flash("Usuario no encontrado", "error")
            return redirect(url_for("usuarios.listar_fiscalizadores"))

        # Si el usuario tiene guardias o vacaciones registradas, alternar estado
        # en lugar de eliminar para preservar el historial.
        if usuario["cnt_guardias"] > 0 or usuario["cnt_vacaciones"] > 0:
            nuevo_estado = _cambiar_estado_usuario(cursor, id)
            conn.commit()
            registrar_auditoria("usuario_estado_cambiado", f"Usuario ID {id} -> {nuevo_estado} (tenía historial)")
            flash(f"Usuario {'desactivado' if nuevo_estado == 'inactivo' else 'activado'} (tiene historial registrado)", "success")
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
            registrar_auditoria("usuario_eliminado", f"Usuario ID {id} eliminado")
            flash("Usuario eliminado correctamente", "success")

        return redirect(url_for("usuarios.listar_fiscalizadores"))

    except Exception as e:

        if conn is not None: conn.rollback()

        print("ERROR ELIMINAR USUARIO:", e)
        flash("Error interno del servidor al eliminar usuario", "error")

        return redirect(url_for("usuarios.listar_fiscalizadores"))

    finally:

        if cursor is not None: cursor.close()
        if conn is not None: conn.close()


# ==========================
# TOGGLE USUARIO (ACTIVAR / DESACTIVAR)
# ==========================
# Alterna el estado de un usuario entre activo e inactivo
@usuarios_bp.route("/toggle_usuario/<int:id>", methods=["POST"])
@limiter.limit("10 per minute")
def toggle_usuario(id):

    if "usuario" not in session or session.get("perfil_activo", "").lower() != "admin":
        return redirect(url_for("login.login"))

    conn = None
    cursor = None

    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        # Evitar que un admin se desactive a sí mismo
        if id == session.get("id_usuario"):
            flash("No puedes desactivar tu propia cuenta", "error")
            return redirect(url_for("usuarios.listar_fiscalizadores"))

        nuevo_estado = _cambiar_estado_usuario(cursor, id)

        if nuevo_estado is None:
            return redirect(url_for("usuarios.listar_fiscalizadores"))

        conn.commit()
        registrar_auditoria("usuario_toggle", f"Usuario ID {id} -> {nuevo_estado}")

        flash(f"Usuario {'activado' if nuevo_estado == 'activo' else 'desactivado'} correctamente", "success")

        return redirect(url_for("usuarios.listar_fiscalizadores"))

    except Exception as e:

        if conn is not None: conn.rollback()

        print("ERROR TOGGLE USUARIO:", e)
        flash("Error interno del servidor al cambiar estado", "error")

        return redirect(url_for("usuarios.listar_fiscalizadores"))

    finally:

        if cursor is not None: cursor.close()
        if conn is not None: conn.close()

# ==========================
# DESBLOQUEAR CUENTA (ADMIN)
# ==========================
# Reactiva una cuenta bloqueada por intentos fallidos: verifica la contraseña del
# administrador, genera una contraseña temporal, limpia los intentos y revoca los
# dispositivos confiables.
@usuarios_bp.route("/desbloquear_usuario/<int:id>", methods=["POST"])
@limiter.limit("10 per minute")
def desbloquear_usuario(id):

    if "usuario" not in session or session.get("perfil_activo", "").lower() != "admin":
        return redirect(url_for("login.login"))

    conn = None
    cursor = None

    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            "SELECT usuario FROM usuarios WHERE id_usuario = %s",
            (id,)
        )
        row = cursor.fetchone()
        if not row:
            flash("Usuario no encontrado", "error")
            return redirect(url_for("usuarios.listar_fiscalizadores"))

        # Verifica la contraseña del administrador
        password_admin = request.form.get("password", "")
        cursor.execute(
            "SELECT password FROM usuarios WHERE id_usuario = %s",
            (session.get("id_usuario"),)
        )
        admin_row = cursor.fetchone()
        if not admin_row or not check_password_hash(admin_row["password"], password_admin):
            flash("Contraseña de administrador incorrecta", "error")
            return redirect(url_for("usuarios.listar_fiscalizadores"))

        # Genera una contraseña temporal para el usuario
        password_temporal = generar_password_temporal()
        password_hash = generate_password_hash(password_temporal)

        cursor.execute("""
            UPDATE usuarios
            SET cuenta_bloqueada = FALSE,
                bloqueos_consecutivos = 0,
                debe_cambiar_password = TRUE,
                password = %s,
                session_version = session_version + 1
            WHERE id_usuario = %s
        """, (password_hash, id))

        # Elimina los intentos fallidos pendientes de la cuenta
        cursor.execute(
            "DELETE FROM intentos_login WHERE identificador = %s",
            (f"uid:{id}",)
        )

        # Elimina los dispositivos confiables de la cuenta
        cursor.execute(
            "DELETE FROM dispositivos_confiables WHERE id_usuario = %s",
            (id,)
        )

        conn.commit()
        registrar_auditoria(
            "cuenta_desbloqueada",
            f"Cuenta reactivada por admin con rotación de contraseña: {row['usuario']}"
        )

        flash(
            f"Cuenta de {row['usuario']} desbloqueada. Contraseña temporal: {password_temporal} "
            "— entréguela al usuario por un canal seguro; deberá cambiarla al iniciar sesión.",
            "success"
        )

        return redirect(url_for("usuarios.listar_fiscalizadores"))

    except Exception as e:

        if conn is not None: conn.rollback()

        print("ERROR DESBLOQUEAR USUARIO:", e)
        flash("Error interno del servidor al desbloquear la cuenta", "error")

        return redirect(url_for("usuarios.listar_fiscalizadores"))

    finally:

        if cursor is not None: cursor.close()
        if conn is not None: conn.close()

# ==========================
# RESETEAR 2FA (ADMIN)
# ==========================
# Desactiva la autenticación en dos pasos de un usuario (por pérdida de código o cambio de dispositivo)
@usuarios_bp.route("/resetear_2fa/<int:id>", methods=["POST"])
@limiter.limit("10 per minute")
def resetear_2fa(id):

    if "usuario" not in session or session.get("perfil_activo", "").lower() != "admin":
        return redirect(url_for("login.login"))

    conn = None
    cursor = None

    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT nombre, apellidos
            FROM usuarios
            WHERE id_usuario = %s
        """, (id,))
        usuario = cursor.fetchone()

        if not usuario:
            flash("Usuario no encontrado", "error")
            return redirect(url_for("usuarios.listar_fiscalizadores"))

        # Reautenticación del administrador antes de desactivar el 2FA de otro usuario
        password = request.form.get("password", "")
        cursor.execute(
            "SELECT password FROM usuarios WHERE id_usuario = %s",
            (session.get("id_usuario"),)
        )
        admin_row = cursor.fetchone()
        if not admin_row or not check_password_hash(admin_row["password"], password):
            flash("Contraseña de administrador incorrecta", "error")
            return redirect(url_for("usuarios.editar_usuario", id=id))

        # Desactivar 2FA y limpiar el secreto TOTP
        cursor.execute("""
            UPDATE usuarios
            SET totp_secret = NULL, dos_factores_activo = FALSE
            WHERE id_usuario = %s
        """, (id,))

        # Eliminar los dispositivos de confianza asociados
        cursor.execute("""
            DELETE FROM dispositivos_confiables
            WHERE id_usuario = %s
        """, (id,))

        conn.commit()
        registrar_auditoria("2fa_reseteado", f"2FA reseteado por admin para usuario ID {id}")

        flash(
            f"Autenticación en dos pasos desactivada para {usuario['nombre']} {usuario['apellidos']}.",
            "success"
        )
        return redirect(url_for("usuarios.editar_usuario", id=id))

    except Exception as e:

        if conn is not None: conn.rollback()

        print("ERROR RESETEAR 2FA:", e)
        flash("Error interno del servidor al desactivar la autenticación en dos pasos", "error")

        return redirect(url_for("usuarios.editar_usuario", id=id))

    finally:

        if cursor is not None: cursor.close()
        if conn is not None: conn.close()


# ==========================
# LISTA PÚBLICA FISCALIZADORES
# ==========================
# Muestra la lista de fiscalizadores para usuarios autenticados (vista de solo lectura)
@usuarios_bp.route("/fiscalizadores")
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
                u.foto
            FROM usuarios u
            LEFT JOIN usuarios_roles ur
                ON u.id_usuario = ur.id_usuario
            LEFT JOIN roles r
                ON ur.id_rol = r.id_rol
            WHERE r.nombre_rol = 'fiscalizador'
              AND u.estado = 'activo'
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
