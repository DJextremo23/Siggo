from flask import Blueprint, render_template, request, redirect, session, send_file, url_for, flash
from werkzeug.utils import secure_filename
from conexion import conexion
from utils import acceso_no_autorizado, guardar_filtros, redirigir_con_filtros, datos_invalidos, no_encontrado
from utils.validators import archivo_permitido, sanitizar_nombre, validar_mime_real, validar_longitudes
from datetime import datetime
import os

"""
Blueprint admin para gestión de informes: listar, registrar, editar, descargar, eliminar - lado administrador
"""

informes_bp = Blueprint("informes", __name__)

# Carpeta donde se almacenan los archivos subidos
UPLOAD_FOLDER = "uploads"
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

# ── Punto de entrada principal: listado de informes con filtros ──
@informes_bp.route("/informes")
def admin_informes():

    # Solo usuarios autenticados
    if "usuario" not in session:
        return redirect(url_for("home"))

    # Solo perfil administrador
    if session.get("perfil_activo", "").lower() != "admin":
        return acceso_no_autorizado()

    guardar_filtros("filtro_informes")

    # Parámetros de filtro opcionales
    fecha_desde = request.args.get("fecha_desde")
    fecha_hasta = request.args.get("fecha_hasta")
    tipo = request.args.get("tipo")
    anio = request.args.get("anio")
    titulo = request.args.get("titulo")
    id_usuario = request.args.get("id_usuario")

    conn = None
    cursor = None

    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        # Construcción dinámica de la consulta SQL con filtros opcionales
        sql = """
            SELECT
                i.id_informe,
                i.titulo,
                i.descripcion,
                i.nombre_archivo,
                i.tipo_archivo,
                i.extension,
                i.fecha_subida,
                g.fecha_guardia,
                CONCAT(u.nombre,' ',u.apellidos) AS fiscalizador,
                u.foto
            FROM informes i
            INNER JOIN guardias g ON i.id_guardia = g.id_guardia
            INNER JOIN usuarios u ON i.id_usuario = u.id_usuario
            WHERE i.estado = 'activo'
        """

        params = []

        if fecha_desde:
            sql += " AND g.fecha_guardia >= %s"
            params.append(fecha_desde)

        if fecha_hasta:
            sql += " AND g.fecha_guardia <= %s"
            params.append(fecha_hasta)

        if tipo:
            sql += " AND i.tipo_archivo = %s"
            params.append(tipo)

        if anio:
            sql += " AND YEAR(g.fecha_guardia) = %s"
            params.append(anio)

        if titulo:
            sql += " AND i.titulo LIKE %s"
            params.append(f"%{titulo}%")

        if id_usuario:
            sql += " AND i.id_usuario = %s"
            params.append(id_usuario)

        sql += " ORDER BY i.fecha_subida DESC"

        cursor.execute(sql, tuple(params))
        informes = cursor.fetchall()

        cursor.execute("""
            SELECT DISTINCT
                u.id_usuario,
                u.nombre,
                u.apellidos
            FROM usuarios u
            INNER JOIN usuarios_roles ur ON u.id_usuario = ur.id_usuario
            INNER JOIN roles r ON ur.id_rol = r.id_rol
            WHERE r.nombre_rol = 'fiscalizador'
              AND u.estado = 'activo'
            ORDER BY u.nombre
        """)
        fiscalizadores = cursor.fetchall()

        return render_template("informes.html", informes=informes, fiscalizadores=fiscalizadores)

    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


# ── Registrar informe (subir) para cualquier fiscalizador ──
@informes_bp.route("/informes/registrar", methods=["GET", "POST"])
def admin_registrar_informe():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo", "").lower() != "admin":
        return acceso_no_autorizado()

    conn = None
    cursor = None

    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        if request.method == "POST":
            id_guardia = request.form.get("id_guardia", "").strip()
            titulo = request.form.get("titulo", "").strip()
            descripcion = request.form.get("descripcion", "").strip()

            if not id_guardia or not titulo:
                return datos_invalidos("La guardia y el título son obligatorios")

            valido, msg = validar_longitudes({
                "titulo": titulo,
                "descripcion": descripcion,
            })
            if not valido:
                return datos_invalidos(msg)

            archivo = request.files.get("archivo")

            if not archivo or archivo.filename == "":
                return datos_invalidos("Debe seleccionar un archivo")

            if not archivo_permitido(archivo.filename):
                return datos_invalidos("Formato de archivo no permitido")

            extension = archivo.filename.rsplit(".", 1)[1].lower()

            archivo.seek(0, os.SEEK_END)
            tamano_archivo = archivo.tell()
            archivo.seek(0)
            if tamano_archivo > MAX_FILE_SIZE:
                return datos_invalidos("El archivo excede el tamaño máximo permitido (10 MB)")

            magic_bytes = archivo.read(12)
            archivo.seek(0)
            if not validar_mime_real(magic_bytes, extension):
                return datos_invalidos("El contenido del archivo no coincide con su extensión")

            # Obtener el fiscalizador dueño de la guardia seleccionada
            cursor.execute("""
                SELECT g.id_usuario, u.estado
                FROM guardias g
                INNER JOIN usuarios u ON g.id_usuario = u.id_usuario
                WHERE g.id_guardia = %s
            """, (id_guardia,))
            guardia = cursor.fetchone()
            if not guardia:
                return no_encontrado("La guardia seleccionada no existe")

            if guardia["estado"] != "activo":
                return datos_invalidos("No se puede registrar un informe para un usuario inactivo")

            nombre = (
                datetime.now().strftime("%Y%m%d%H%M%S_")
                + sanitizar_nombre(secure_filename(archivo.filename))
            )
            ruta = os.path.join(UPLOAD_FOLDER, nombre)
            archivo.save(ruta)

            cursor.execute("""
                INSERT INTO informes(
                    id_guardia, id_usuario, titulo, descripcion,
                    nombre_archivo, ruta_archivo, tipo_archivo, extension, tamano_archivo
                )
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                id_guardia,
                guardia["id_usuario"],
                titulo,
                descripcion,
                nombre,
                ruta,
                extension,
                extension,
                tamano_archivo
            ))

            conn.commit()

            flash("Informe registrado correctamente", "success")
            return redirigir_con_filtros("informes.admin_informes", "filtro_informes")

        # GET: listar guardias de todos los fiscalizadores activos
        cursor.execute("""
            SELECT
                g.id_guardia,
                g.fecha_guardia,
                YEAR(g.fecha_guardia) AS anio,
                u.id_usuario,
                CONCAT(u.nombre, ' ', u.apellidos) AS fiscalizador
            FROM guardias g
            INNER JOIN usuarios u ON g.id_usuario = u.id_usuario
            INNER JOIN usuarios_roles ur ON u.id_usuario = ur.id_usuario
            INNER JOIN roles r ON ur.id_rol = r.id_rol
            WHERE r.nombre_rol = 'fiscalizador'
              AND u.estado = 'activo'
            ORDER BY u.nombre, u.apellidos, g.fecha_guardia DESC
        """)
        guardias = cursor.fetchall()

        for g in guardias:
            g["fecha_str"] = g["fecha_guardia"].strftime("%d.%m.%Y") if g["fecha_guardia"] else ""

        return render_template(
            "registrar_informe_admin.html",
            guardias=guardias
        )

    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


# ── Editar informe (título, descripción y reemplazo de archivo) ──
@informes_bp.route("/informes/editar/<int:id_informe>", methods=["GET", "POST"])
def admin_editar_informe(id_informe):

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo", "").lower() != "admin":
        return acceso_no_autorizado()

    conn = None
    cursor = None

    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT *
            FROM informes
            WHERE id_informe = %s
              AND estado = 'activo'
        """, (id_informe,))
        informe = cursor.fetchone()

        if not informe:
            return no_encontrado("Informe no encontrado")

        if request.method == "POST":
            titulo = request.form.get("titulo", "").strip()
            descripcion = request.form.get("descripcion", "").strip()

            if not titulo:
                return datos_invalidos("El título es obligatorio")

            valido, msg = validar_longitudes({
                "titulo": titulo,
                "descripcion": descripcion,
            })
            if not valido:
                return datos_invalidos(msg)

            cursor.execute("""
                UPDATE informes
                SET titulo = %s, descripcion = %s
                WHERE id_informe = %s
            """, (titulo, descripcion, id_informe))

            archivo = request.files.get("archivo")

            if archivo and archivo.filename != "":
                if not archivo_permitido(archivo.filename):
                    return datos_invalidos("Formato de archivo no permitido")

                extension = archivo.filename.rsplit(".", 1)[1].lower()

                archivo.seek(0, os.SEEK_END)
                tamano_nuevo = archivo.tell()
                archivo.seek(0)
                if tamano_nuevo > MAX_FILE_SIZE:
                    return datos_invalidos("El archivo excede el tamaño máximo permitido (10 MB)")

                magic_bytes = archivo.read(12)
                archivo.seek(0)
                if not validar_mime_real(magic_bytes, extension):
                    return datos_invalidos("El contenido del archivo no coincide con su extensión")

                nombre = (
                    datetime.now().strftime("%Y%m%d%H%M%S_")
                    + sanitizar_nombre(secure_filename(archivo.filename))
                )
                ruta = os.path.join(UPLOAD_FOLDER, nombre)
                archivo.save(ruta)

                cursor.execute("""
                    UPDATE informes
                    SET nombre_archivo = %s,
                        ruta_archivo = %s,
                        tipo_archivo = %s,
                        extension = %s,
                        tamano_archivo = %s
                    WHERE id_informe = %s
                """, (nombre, ruta, extension, extension, tamano_nuevo, id_informe))

            conn.commit()

            flash("Informe actualizado correctamente", "success")
            return redirigir_con_filtros("informes.admin_informes", "filtro_informes")

        return render_template("editar_informe_admin.html", informe=informe)

    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


# ── Descarga segura de un informe por ID ──
@informes_bp.route("/informes/descargar/<int:id_informe>")
def admin_descargar_informe(id_informe):

    # Solo usuarios autenticados
    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo", "").lower() != "admin":
        return acceso_no_autorizado()

    conn = None
    cursor = None

    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT *
            FROM informes
            WHERE id_informe = %s
            AND estado = 'activo'
        """, (id_informe,))

        informe = cursor.fetchone()

        if not informe:
            flash("Archivo no encontrado", "error")
            return redirigir_con_filtros("informes.admin_informes", "filtro_informes")

        ruta = informe["ruta_archivo"]
        # ── Protección contra Path Traversal ──
        ruta_real = os.path.realpath(ruta)
        uploads_real = os.path.realpath(UPLOAD_FOLDER)
        if not ruta_real.startswith(uploads_real + os.sep) and ruta_real != uploads_real:
            return acceso_no_autorizado()

        if not os.path.exists(ruta_real):
            flash("El archivo no existe en el servidor", "error")
            return redirigir_con_filtros("informes.admin_informes", "filtro_informes")

        return send_file(
            ruta_real,
            as_attachment=True,
            download_name=informe["nombre_archivo"]
        )

    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


# ── Eliminación lógica de un informe (cambia estado a 'eliminado') ──
@informes_bp.route("/informes/eliminar/<int:id_informe>", methods=["POST"])
def admin_eliminar_informe(id_informe):

    # Solo usuarios autenticados
    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo", "").lower() != "admin":
        return acceso_no_autorizado()

    conn = None
    cursor = None

    try:
        conn = conexion()
        cursor = conn.cursor()
        cursor.execute("""
            UPDATE informes
            SET estado = 'eliminado'
            WHERE id_informe = %s
        """, (id_informe,))

        conn.commit()

        flash("Informe eliminado correctamente", "success")
        return redirigir_con_filtros("informes.admin_informes", "filtro_informes")

    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()

