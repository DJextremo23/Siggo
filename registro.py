from flask import Blueprint, request, render_template, session, redirect, url_for
import re
import os
from datetime import datetime
from werkzeug.security import generate_password_hash
from werkzeug.utils import secure_filename
from conexion import conexion
from utils.validators import validar_mime_real, sanitizar_nombre, validar_longitudes, INPUT_LIMITS
from limiter_instance import limiter

"""
Blueprint para registro de nuevos usuarios con validación de contraseña segura,
email único, asignación de roles y foto de perfil.
"""
registro_bp = Blueprint("registro", __name__)

FOTOS_FOLDER = os.path.join("static", "fotos")
# Extensiones de imagen permitidas para la foto de perfil
ALLOWED_PHOTO_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
MAX_PHOTO_SIZE = 5 * 1024 * 1024  # 5 MB

os.makedirs(FOTOS_FOLDER, exist_ok=True)


def foto_permitida(nombre):
    return (
        "." in nombre
        and nombre.rsplit(".", 1)[1].lower() in ALLOWED_PHOTO_EXTENSIONS
    )

# =====================================================
# VALIDAR PASSWORD
# =====================================================
def password_segura(password):

    return all([

        len(password) >= 10,

        bool(re.search(r"[A-Z]", password)),

        bool(re.search(r"[a-z]", password)),

        bool(re.search(r"[0-9]", password)),

        bool(re.search(r"[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>\/?]", password))

    ])

# =====================================================
# REGISTRO
# =====================================================
@registro_bp.route("/registro", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def registro():

    if "usuario" not in session or session.get("perfil_activo") != "admin":
        return redirect(url_for("login.login"))

    if request.method == "POST":

        nombre = request.form.get("nombre", "").strip()

        apellidos = request.form.get("apellidos", "").strip()

        correo = request.form.get(
            "correo",
            ""
        ).strip().lower()

        usuario = request.form.get(
            "usuario",
            ""
        ).strip()

        password = request.form.get("password", "")

        confirm = request.form.get(
            "confirm_password",
            ""
        )

        rol = request.form.get("rol", "")

        conn = None
        cursor = None

        try:
            conn = conexion()
            cursor = conn.cursor(dictionary=True)

            # =================================================
            # VALIDAR CAMPOS
            # =================================================
            # Validar que todos los campos obligatorios estén presentes
            if not all([

                nombre,
                apellidos,
                correo,
                usuario,
                password,
                rol

            ]):

                return render_template(
                    "registro.html",
                    error="Todos los campos son obligatorios"
                )

            # =================================================
            # VALIDAR LONGITUDES DE CAMPOS
            # =================================================
            valido, msg = validar_longitudes({
                "nombre": nombre,
                "apellidos": apellidos,
                "correo": correo,
                "usuario": usuario,
                "password": password,
            })
            if not valido:
                return render_template("registro.html", error=msg)

            # Verificar que las contraseñas coincidan antes de continuar
            if password != confirm:

                return render_template(
                    "registro.html",
                    error="Las contraseñas no coinciden"
                )

            # Validar que la contraseña cumpla con los requisitos de seguridad
            if not password_segura(password):

                return render_template(
                    "registro.html",
                    error="Contraseña débil"
                )

            # Verificar que el correo no esté registrado previamente
            cursor.execute(

                """
                SELECT id_usuario
                FROM usuarios
                WHERE correo=%s
                """,

                (correo,)
            )

            if cursor.fetchone():

                return render_template(
                    "registro.html",
                    error="El correo ya está registrado"
                )

            # Verificar que el nombre de usuario no esté en uso
            cursor.execute(

                """
                SELECT id_usuario
                FROM usuarios
                WHERE usuario=%s
                """,

                (usuario,)
            )

            if cursor.fetchone():

                return render_template(
                    "registro.html",
                    error="El usuario ya existe"
                )

            # Generar hash seguro de la contraseña antes de almacenarla
            password_hash = generate_password_hash(password)

            # Insertar los datos del nuevo usuario en la base de datos
            cursor.execute(

                """
                INSERT INTO usuarios (

                    nombre,
                    apellidos,
                    correo,
                    usuario,
                    password,
                    estado,
                    fecha_ingreso

                )

                VALUES (

                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    'activo',
                    %s

                )
                """,

                (

                    nombre,
                    apellidos,
                    correo,
                    usuario,
                    password_hash,
                    datetime.today().date()

                )
            )

            conn.commit()

            # Recuperar el ID autogenerado del usuario recién insertado
            id_usuario = cursor.lastrowid

            # Procesar y guardar la foto de perfil si se subió una válida
            foto = request.files.get("foto")
            nombre_foto = None

            if foto and foto.filename and foto_permitida(foto.filename):
                contenido = foto.read()
                if len(contenido) <= MAX_PHOTO_SIZE:
                    ext = foto.filename.rsplit(".", 1)[1].lower()
                    if not validar_mime_real(contenido[:12], ext):
                        return render_template(
                            "registro.html",
                            error="El contenido de la imagen no coincide con su extensión"
                        )
                    nombre_foto = f"{id_usuario}_{datetime.now().strftime('%Y%m%d%H%M%S')}.{ext}"
                    ruta_foto = os.path.join(FOTOS_FOLDER, nombre_foto)
                    with open(ruta_foto, "wb") as f:
                        f.write(contenido)

                    cursor.execute(
                        "UPDATE usuarios SET foto = %s WHERE id_usuario = %s",
                        (nombre_foto, id_usuario)
                    )
                    conn.commit()

            # Obtener ID del rol administrador para la asignación de roles
            cursor.execute(

                """
                SELECT id_rol
                FROM roles
                WHERE nombre_rol='admin'
                """
            )

            admin = cursor.fetchone()

            # Obtener ID del rol fiscalizador para la asignación de roles
            cursor.execute(

                """
                SELECT id_rol
                FROM roles
                WHERE nombre_rol='fiscalizador'
                """
            )

            fiscalizador = cursor.fetchone()

            # Asignar rol: si es admin se le otorgan ambos roles (admin + fiscalizador)
            if rol == "admin":

                # ADMIN
                cursor.execute(

                    """
                    INSERT INTO usuarios_roles
                    (id_usuario, id_rol)
                    VALUES (%s,%s)
                    """,

                    (

                        id_usuario,
                        admin["id_rol"]

                    )
                )

                # FISCALIZADOR
                cursor.execute(

                    """
                    INSERT INTO usuarios_roles
                    (id_usuario, id_rol)
                    VALUES (%s,%s)
                    """,

                    (

                        id_usuario,
                        fiscalizador["id_rol"]

                    )
                )

            # Si el rol no es admin, se asigna únicamente el rol de fiscalizador
            else:

                cursor.execute(

                    """
                    INSERT INTO usuarios_roles
                    (id_usuario, id_rol)
                    VALUES (%s,%s)
                    """,

                    (

                        id_usuario,
                        fiscalizador["id_rol"]

                    )
                )

            conn.commit()

            return render_template(

                "registro.html",

                mensaje="Usuario registrado correctamente"

            )

        except Exception as e:

            if conn is not None:
                conn.rollback()

            print("ERROR:", e)

            return render_template(
                "registro.html",
                error="Error técnico"
            )

        finally:
            if cursor is not None:
                cursor.close()
            if conn is not None:
                conn.close()

    return render_template("registro.html")