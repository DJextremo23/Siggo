from flask import Blueprint, render_template, request, redirect, session, send_file, url_for, flash
from conexion import conexion
from utils import acceso_no_autorizado, no_encontrado
import os

"""
Blueprint admin para gestión de informes: listar, descargar, eliminar - lado administrador
"""

informes_bp = Blueprint("informes", __name__)

# Carpeta donde se almacenan los archivos subidos
UPLOAD_FOLDER = "uploads"

# ── Punto de entrada principal: listado de informes con filtros ──
@informes_bp.route("/admin/informes")
def admin_informes():

    # Solo usuarios autenticados
    if "usuario" not in session:
        return redirect(url_for("home"))

    # Solo perfil administrador
    if session.get("perfil_activo", "").lower() != "admin":
        return acceso_no_autorizado()

    # Parámetros de filtro opcionales
    fecha_desde = request.args.get("fecha_desde")
    fecha_hasta = request.args.get("fecha_hasta")
    tipo = request.args.get("tipo")
    anio = request.args.get("anio")
    titulo = request.args.get("titulo")
    fiscalizador = request.args.get("fiscalizador")

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

        if fiscalizador:
            sql += " AND CONCAT(u.nombre, ' ', u.apellidos) LIKE %s"
            params.append(f"%{fiscalizador}%")

        sql += " ORDER BY i.fecha_subida DESC"

        cursor.execute(sql, tuple(params))
        informes = cursor.fetchall()

        return render_template("informes.html", informes=informes)

    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()


# ── Descarga segura de un informe por ID ──
@informes_bp.route("/admin/informes/descargar/<int:id_informe>")
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
            return no_encontrado("Archivo no encontrado")

        ruta = informe["ruta_archivo"]
        # ── Protección contra Path Traversal ──
        ruta_real = os.path.realpath(ruta)
        uploads_real = os.path.realpath(UPLOAD_FOLDER)
        if not ruta_real.startswith(uploads_real + os.sep) and ruta_real != uploads_real:
            return acceso_no_autorizado()

        if not os.path.exists(ruta_real):
            return no_encontrado("El archivo no existe en el servidor")

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
@informes_bp.route("/admin/informes/eliminar/<int:id_informe>", methods=["POST"])
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
        return redirect(url_for("informes.admin_informes"))

    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()

