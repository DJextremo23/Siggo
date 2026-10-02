"""
Blueprint para gestión de informes del fiscalizador - registrar, listar,
editar, eliminar, descargar y analizar informes con IA
"""

from flask import Blueprint, render_template, request, redirect, session, send_file, url_for, jsonify, flash
from werkzeug.utils import secure_filename
from conexion import conexion
from utils import acceso_no_autorizado, datos_invalidos, no_encontrado, guardar_filtros, redirigir_con_filtros
from utils.validators import archivo_permitido, sanitizar_nombre, validar_mime_real, validar_longitudes
from datetime import datetime
from io import BytesIO
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.pagesizes import landscape, letter
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
import os
import json
import re
from dotenv import load_dotenv
from limiter_instance import limiter

# Cargar variables de entorno desde archivo .env
load_dotenv()

# Blueprint principal de informes
informe_bp = Blueprint("informe", __name__)

# Configuración de subida de archivos
UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "uploads")
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB

# Crear carpeta de subidas si no existe
os.makedirs(UPLOAD_FOLDER, exist_ok=True)


# ==========================================
# LISTAR INFORMES DEL FISCALIZADOR
# ==========================================
@informe_bp.route("/mis_informes")
def mis_informes():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "fiscalizador":
        return acceso_no_autorizado()

    guardar_filtros("filtro_mis_informes")

    fecha_desde = request.args.get("fecha_desde")
    fecha_hasta = request.args.get("fecha_hasta")
    tipo = request.args.get("tipo")
    anio = request.args.get("anio")
    titulo = request.args.get("titulo")

    conn = None
    cursor = None

    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        sql = """
            SELECT
                i.*,
                g.fecha_guardia
            FROM informes i
            INNER JOIN guardias g
                ON i.id_guardia = g.id_guardia
            WHERE i.id_usuario = %s
            AND i.estado = 'activo'
        """

        parametros = [session["id_usuario"]]

        # ── Construcción dinámica de la consulta con filtros opcionales ──
        if fecha_desde:
            sql += " AND g.fecha_guardia >= %s "
            parametros.append(fecha_desde)

        # FILTRO FECHA HASTA
        if fecha_hasta:
            sql += " AND g.fecha_guardia <= %s "
            parametros.append(fecha_hasta)

        # FILTRO TIPO
        if tipo:
            sql += " AND i.tipo_archivo = %s "
            parametros.append(tipo)

        # FILTRO AÑO
        if anio:
            sql += " AND YEAR(g.fecha_guardia) = %s "
            parametros.append(anio)

        # FILTRO TITULO
        if titulo:
            sql += " AND i.titulo LIKE %s "
            parametros.append(f"%{titulo}%")

        sql += " ORDER BY i.fecha_subida DESC "

        cursor.execute(sql, tuple(parametros))
        informes = cursor.fetchall()

        anio_actual = datetime.now().year

        return render_template(
            "mis_informes.html",
            informes=informes,
            anio_actual=anio_actual
        )

    finally:
        if cursor is not None: cursor.close()
        if conn is not None: conn.close()


# ==========================================
# REGISTRAR INFORME
# ==========================================
@informe_bp.route("/registrar_informe", methods=["GET", "POST"])
def registrar_informe():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "fiscalizador":
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

            cursor.execute("""
                SELECT 1
                FROM guardias
                WHERE id_guardia = %s
                  AND id_usuario = %s
                LIMIT 1
            """, (id_guardia, session["id_usuario"]))
            if not cursor.fetchone():
                return datos_invalidos("La guardia seleccionada no es válida")

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

            # ── Validar tamaño máximo ──
            archivo.seek(0, os.SEEK_END)
            tamano_archivo = archivo.tell()
            archivo.seek(0)
            if tamano_archivo > MAX_FILE_SIZE:
                return datos_invalidos("El archivo excede el tamaño máximo permitido (10 MB)")

            # ── Validar MIME real ──
            magic_bytes = archivo.read(12)
            archivo.seek(0)
            if not validar_mime_real(magic_bytes, extension):
                return datos_invalidos("El contenido del archivo no coincide con su extensión")

            nombre = (
                datetime.now().strftime("%Y%m%d%H%M%S_")
                + sanitizar_nombre(secure_filename(archivo.filename))
            )

            ruta = os.path.join(
                UPLOAD_FOLDER,
                nombre
            )

            archivo.save(ruta)

            cursor.execute("""
                INSERT INTO informes(
                    id_guardia,
                    id_usuario,
                    titulo,
                    descripcion,
                    nombre_archivo,
                    ruta_archivo,
                    tipo_archivo,
                    extension,
                    tamano_archivo
                )
                VALUES(
                    %s,%s,%s,%s,
                    %s,%s,%s,%s,%s
                )
            """, (
                id_guardia,
                session["id_usuario"],
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
            return redirigir_con_filtros("informe.mis_informes", "filtro_mis_informes")

        cursor.execute("""
            SELECT
                id_guardia,
                fecha_guardia,
                YEAR(fecha_guardia) AS anio
            FROM guardias
            WHERE id_usuario = %s
            ORDER BY fecha_guardia DESC
        """, (session["id_usuario"],))

        guardias = cursor.fetchall()

        guardias_por_anio = {}
        for g in guardias:
            guardias_por_anio.setdefault(g["anio"], []).append(g)

        return render_template(
            "registrar_informe.html",
            guardias_por_anio=guardias_por_anio
        )

    finally:
        if cursor is not None: cursor.close()
        if conn is not None: conn.close()


# ==========================================
# EDITAR INFORME (título, descripción y archivo)
# ==========================================
@informe_bp.route("/editar_informe/<int:id_informe>", methods=["GET", "POST"])
def editar_informe(id_informe):

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "fiscalizador":
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
            AND id_usuario = %s
            AND estado = 'activo'
        """, (
            id_informe,
            session["id_usuario"]
        ))

        informe = cursor.fetchone()

        if not informe:
            return no_encontrado("Informe no encontrado")

        if request.method == "POST":

            titulo = request.form.get("titulo", "").strip()
            descripcion = request.form.get("descripcion", "").strip()

            valido, msg = validar_longitudes({
                "titulo": titulo,
                "descripcion": descripcion,
            })
            if not valido:
                return datos_invalidos(msg)

            cursor.execute("""
                UPDATE informes
                SET titulo=%s,
                    descripcion=%s
                WHERE id_informe=%s
                  AND id_usuario=%s
            """, (
                titulo,
                descripcion,
                id_informe,
                session["id_usuario"]
            ))

            archivo = request.files.get("archivo")

            if archivo and archivo.filename != "":

                if not archivo_permitido(archivo.filename):
                    return datos_invalidos("Formato de archivo no permitido")

                extension_edit = archivo.filename.rsplit(".", 1)[1].lower()

                # ── Validar tamaño máximo ──
                archivo.seek(0, os.SEEK_END)
                tamano_nuevo = archivo.tell()
                archivo.seek(0)
                if tamano_nuevo > MAX_FILE_SIZE:
                    return datos_invalidos("El archivo excede el tamaño máximo permitido (10 MB)")

                # ── Validar MIME real ──
                magic_bytes = archivo.read(12)
                archivo.seek(0)
                if not validar_mime_real(magic_bytes, extension_edit):
                    return datos_invalidos("El contenido del archivo no coincide con su extensión")

                nombre = (
                    datetime.now().strftime("%Y%m%d%H%M%S_")
                    + sanitizar_nombre(secure_filename(archivo.filename))
                )

                ruta = os.path.join(
                    UPLOAD_FOLDER,
                    nombre
                )

                archivo.save(ruta)

                # Elimina el archivo anterior para no dejar huérfanos
                ruta_anterior = informe.get("ruta_archivo")
                if ruta_anterior:
                    uploads_real = os.path.realpath(UPLOAD_FOLDER)
                    ruta_anterior_real = os.path.realpath(ruta_anterior)
                    if ruta_anterior_real == uploads_real or ruta_anterior_real.startswith(uploads_real + os.sep):
                        try:
                            if os.path.isfile(ruta_anterior_real):
                                os.remove(ruta_anterior_real)
                        except OSError:
                            pass

                extension = nombre.rsplit(".", 1)[1].lower()

                cursor.execute("""
                    UPDATE informes
                    SET nombre_archivo=%s,
                        ruta_archivo=%s,
                        tipo_archivo=%s,
                        extension=%s,
                        tamano_archivo=%s
                    WHERE id_informe=%s
                      AND id_usuario=%s
                """, (
                    nombre,
                    ruta,
                    extension,
                    extension,
                    tamano_nuevo,
                    id_informe,
                    session["id_usuario"]
                ))

            conn.commit()

            flash("Informe actualizado correctamente", "success")
            return redirigir_con_filtros("informe.mis_informes", "filtro_mis_informes")

        return render_template(
            "editar_informe.html",
            informe=informe
        )

    finally:
        if cursor is not None: cursor.close()
        if conn is not None: conn.close()


# ==========================================
# DESCARGAR ARCHIVO DEL INFORME
# ==========================================
@informe_bp.route("/descargar_informe/<int:id_informe>")
def descargar_informe(id_informe):

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "fiscalizador":
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
            AND id_usuario = %s
            AND estado = 'activo'
        """, (id_informe, session["id_usuario"]))

        informe = cursor.fetchone()

        if not informe:
            flash("Archivo no encontrado", "error")
            return redirigir_con_filtros("informe.mis_informes", "filtro_mis_informes")

        ruta = informe["ruta_archivo"]
        # ── Protección contra Path Traversal ──
        ruta_real = os.path.realpath(ruta)
        uploads_real = os.path.realpath(UPLOAD_FOLDER)
        if not ruta_real.startswith(uploads_real + os.sep) and ruta_real != uploads_real:
            return acceso_no_autorizado()

        if not os.path.exists(ruta_real):
            flash("El archivo no existe en el servidor", "error")
            return redirigir_con_filtros("informe.mis_informes", "filtro_mis_informes")

        return send_file(
            ruta_real,
            as_attachment=True,
            download_name=informe["nombre_archivo"]
        )

    finally:
        if cursor is not None: cursor.close()
        if conn is not None: conn.close()


# ==========================================
# ELIMINAR INFORME (SOFT DELETE)
# ==========================================
@informe_bp.route("/eliminar_informe/<int:id_informe>", methods=["POST"])
def eliminar_informe(id_informe):

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "fiscalizador":
        return acceso_no_autorizado()

    conn = None
    cursor = None

    try:
        conn = conexion()
        cursor = conn.cursor()

        cursor.execute(
            "SELECT ruta_archivo FROM informes WHERE id_informe = %s AND id_usuario = %s AND estado = 'activo'",
            (id_informe, session["id_usuario"]),
        )
        informe = cursor.fetchone()
        if not informe:
            flash("Informe no encontrado", "error")
            return redirigir_con_filtros("informe.mis_informes", "filtro_mis_informes")

        cursor.execute(
            "UPDATE informes SET estado = 'eliminado' WHERE id_informe = %s AND id_usuario = %s",
            (id_informe, session["id_usuario"]),
        )

        conn.commit()

        ruta = informe[0]
        if ruta:
            uploads_real = os.path.realpath(UPLOAD_FOLDER)
            ruta_real = os.path.realpath(ruta)
            if ruta_real == uploads_real or ruta_real.startswith(uploads_real + os.sep):
                try:
                    if os.path.isfile(ruta_real):
                        os.remove(ruta_real)
                except OSError:
                    pass

        flash("Informe eliminado correctamente", "success")
        return redirigir_con_filtros("informe.mis_informes", "filtro_mis_informes")

    finally:
        if cursor is not None: cursor.close()
        if conn is not None: conn.close()


# ==========================================
# EXPORTAR LISTADO A PDF
# ==========================================
@informe_bp.route("/mis_informes/exportar/pdf")
def exportar_informes_pdf():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "fiscalizador":
        return acceso_no_autorizado()

    fecha_desde = request.args.get("fecha_desde")
    fecha_hasta = request.args.get("fecha_hasta")
    tipo = request.args.get("tipo")
    anio = request.args.get("anio")
    titulo = request.args.get("titulo")

    conn = None
    cursor = None

    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        sql = """
            SELECT
                i.titulo,
                i.descripcion,
                i.nombre_archivo,
                i.tipo_archivo,
                i.fecha_subida,
                g.fecha_guardia
            FROM informes i
            INNER JOIN guardias g
                ON i.id_guardia = g.id_guardia
            WHERE i.id_usuario = %s
            AND i.estado = 'activo'
        """

        parametros = [session["id_usuario"]]

        if fecha_desde:
            sql += " AND g.fecha_guardia >= %s "
            parametros.append(fecha_desde)

        if fecha_hasta:
            sql += " AND g.fecha_guardia <= %s "
            parametros.append(fecha_hasta)

        if tipo:
            sql += " AND i.tipo_archivo = %s "
            parametros.append(tipo)

        if anio:
            sql += " AND YEAR(g.fecha_guardia) = %s "
            parametros.append(anio)

        if titulo:
            sql += " AND i.titulo LIKE %s "
            parametros.append(f"%{titulo}%")

        sql += " ORDER BY i.fecha_subida DESC "

        cursor.execute(sql, tuple(parametros))
        data = cursor.fetchall()

        buffer = BytesIO()
        doc = SimpleDocTemplate(buffer, pagesize=landscape(letter))
        styles = getSampleStyleSheet()
        elementos = []

        elementos.append(Paragraph("MIS INFORMES", styles["Title"]))
        elementos.append(Spacer(1, 10))

        table_data = [["Fecha Guardia", "Título", "Descripción", "Archivo", "Tipo", "Registro"]]

        for d in data:
            table_data.append([
                str(d["fecha_guardia"]) if d["fecha_guardia"] else "",
                d["titulo"] or "",
                (d["descripcion"] or "")[:80],
                d["nombre_archivo"] or "",
                (d["tipo_archivo"] or "").upper(),
                str(d["fecha_subida"]) if d["fecha_subida"] else ""
            ])

        table = Table(table_data, repeatRows=1)

        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.darkblue),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
            ("ALIGN", (0, 0), (-1, -1), "CENTER"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("BACKGROUND", (0, 1), (-1, -1), colors.white),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.lightgrey]),
        ]))

        elementos.append(table)

        doc.build(elementos)
        buffer.seek(0)

        return send_file(buffer, download_name="mis_informes.pdf", as_attachment=True)

    finally:
        if cursor is not None: cursor.close()
        if conn is not None: conn.close()


# ==========================================
# EXPORTAR LISTADO A EXCEL
# ==========================================
@informe_bp.route("/mis_informes/exportar/excel")
def exportar_informes_excel():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "fiscalizador":
        return acceso_no_autorizado()

    fecha_desde = request.args.get("fecha_desde")
    fecha_hasta = request.args.get("fecha_hasta")
    tipo = request.args.get("tipo")
    anio = request.args.get("anio")
    titulo = request.args.get("titulo")

    conn = None
    cursor = None

    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        sql = """
            SELECT
                i.titulo,
                i.descripcion,
                i.nombre_archivo,
                i.tipo_archivo,
                i.fecha_subida,
                g.fecha_guardia
            FROM informes i
            INNER JOIN guardias g
                ON i.id_guardia = g.id_guardia
            WHERE i.id_usuario = %s
            AND i.estado = 'activo'
        """

        parametros = [session["id_usuario"]]

        if fecha_desde:
            sql += " AND g.fecha_guardia >= %s "
            parametros.append(fecha_desde)

        if fecha_hasta:
            sql += " AND g.fecha_guardia <= %s "
            parametros.append(fecha_hasta)

        if tipo:
            sql += " AND i.tipo_archivo = %s "
            parametros.append(tipo)

        if anio:
            sql += " AND YEAR(g.fecha_guardia) = %s "
            parametros.append(anio)

        if titulo:
            sql += " AND i.titulo LIKE %s "
            parametros.append(f"%{titulo}%")

        sql += " ORDER BY i.fecha_subida DESC "

        cursor.execute(sql, tuple(parametros))
        data = cursor.fetchall()

        wb = Workbook()
        ws = wb.active
        ws.title = "Mis Informes"

        header_font = Font(name="Segoe UI", bold=True, color="FFFFFF", size=11)
        header_fill = PatternFill(start_color="1E40AF", end_color="1E40AF", fill_type="solid")
        header_alignment = Alignment(horizontal="center", vertical="center")
        thin_border = Border(
            left=Side(style="thin"),
            right=Side(style="thin"),
            top=Side(style="thin"),
            bottom=Side(style="thin")
        )

        headers = ["Fecha Guardia", "Título", "Descripción", "Archivo", "Tipo", "Registro"]
        for col, header in enumerate(headers, 1):
            cell = ws.cell(row=1, column=col, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = header_alignment
            cell.border = thin_border

        for row_idx, d in enumerate(data, 2):
            row_data = [
                str(d["fecha_guardia"]) if d["fecha_guardia"] else "",
                d["titulo"] or "",
                d["descripcion"] or "",
                d["nombre_archivo"] or "",
                (d["tipo_archivo"] or "").upper(),
                str(d["fecha_subida"]) if d["fecha_subida"] else ""
            ]
            for col_idx, val in enumerate(row_data, 1):
                cell = ws.cell(row=row_idx, column=col_idx, value=val)
                cell.border = thin_border
                cell.alignment = Alignment(vertical="center")

        ws.column_dimensions["A"].width = 15
        ws.column_dimensions["B"].width = 30
        ws.column_dimensions["C"].width = 40
        ws.column_dimensions["D"].width = 35
        ws.column_dimensions["E"].width = 12
        ws.column_dimensions["F"].width = 20

        output = BytesIO()
        wb.save(output)
        output.seek(0)

        return send_file(output, download_name="mis_informes.xlsx", as_attachment=True)

    finally:
        if cursor is not None: cursor.close()
        if conn is not None: conn.close()


# ==========================================
# FUNCIONES AUXILIARES PARA ANÁLISIS CON IA
# ==========================================

def _extraer_estructura_xlsx(archivo_obj):
    """Extrae y estructura localmente el contenido de un XLSX.

    Produce una representación compacta (títulos, tablas detectadas y textos
    sueltos por hoja, más referencias de imágenes) para que Gemini reciba
    información ya organizada y no gaste tiempo interpretando celdas sueltas.

    Devuelve un dict con 'hojas' e 'imagenes'. Imprime métricas de extracción.
    """
    import time as _time
    from openpyxl import load_workbook
    from openpyxl.utils import get_column_letter

    t0 = _time.time()
    ruta = archivo_obj.get("ruta_archivo")
    wb = load_workbook(ruta, data_only=True)

    hojas = []
    imagenes = []
    total_celdas_raw = 0
    total_caracteres_raw = 0
    total_filas = 0

    def _es_numero(v):
        try:
            float(str(v).replace(",", ".").strip())
            return True
        except (ValueError, TypeError):
            return False

    for nombre_hoja in wb.sheetnames:
        ws = wb[nombre_hoja]

        # Referencias de imágenes (hoja + celda de anclaje).
        for img in getattr(ws, "_images", []):
            celda = ""
            try:
                anchor = getattr(img, "anchor", None)
                src = getattr(anchor, "_from", None) if anchor is not None else None
                if src is not None:
                    col = int(getattr(src, "col", 0) or 0) + 1
                    row = int(getattr(src, "row", 0) or 0) + 1
                    celda = f"{get_column_letter(col)}{row}"
            except Exception:
                celda = ""
            imagenes.append({"id": f"img_{len(imagenes) + 1}", "hoja": nombre_hoja, "celda": celda})

        # Filas no vacías: (nº fila, [(col, valor), ...], resaltada_en_amarillo)
        filas = []
        for fila in ws.iter_rows():
            celdas = []
            fila_amarilla = False
            for c in fila:
                if c.value is None:
                    continue
                val = str(c.value).strip().replace("\n", " ")
                if val == "":
                    continue
                total_celdas_raw += 1
                total_caracteres_raw += len(val)
                celdas.append((c.column, val))
                try:
                    f = c.fill
                    if f is not None and f.patternType == "solid":
                        rgb = getattr(f.fgColor, "rgb", None)
                        if isinstance(rgb, str) and rgb.upper() in ("FFFFFF00", "00FFFF00"):
                            fila_amarilla = True
                except Exception:
                    pass
            if celdas:
                total_filas += 1
                filas.append((fila[0].row, celdas, fila_amarilla))

        titulo = None
        esquemas = []
        tablas = []
        textos = []
        tabla = None

        def _esquema_idx(encabezados):
            for i, e in enumerate(esquemas):
                if e == encabezados:
                    return i
            esquemas.append(encabezados)
            return len(esquemas) - 1

        def _cerrar():
            nonlocal tabla
            if tabla and tabla["registros"]:
                idx = _esquema_idx(tabla["encabezados"])
                tablas.append({"fila": tabla["fila"], "esquema": idx, "registros": tabla["registros"]})
            tabla = None

        def _es_encabezado(celdas):
            # Encabezado: varias celdas y ninguna es numérica.
            return len(celdas) >= 2 and all(not _es_numero(v) for _, v in celdas)

        def _es_cabecera_produccion(celdas):
            # La cabecera de producción son pares "etiqueta: valor" (pocas celdas con ':').
            return len(celdas) <= 2 and any(":" in v for _, v in celdas)

        for row_num, celdas, amarillo in filas:
            if len(celdas) == 1:
                val = celdas[0][1]
                # Fila de una sola celda numérica con tabla abierta => registro (columna ítem).
                if tabla is not None and _es_numero(val):
                    fila_vals = [""] * len(tabla["encabezados"])
                    col = celdas[0][0]
                    if col in tabla["cols"]:
                        fila_vals[tabla["cols"].index(col)] = val
                    else:
                        fila_vals[0] = val
                    while fila_vals and fila_vals[-1] == "":
                        fila_vals.pop()
                    tabla["registros"].append(fila_vals)
                    continue
                _cerrar()
                if titulo is None:
                    titulo = val
                else:
                    textos.append({"celda": f"{get_column_letter(celdas[0][0])}{row_num}", "contenido": val})
            elif _es_encabezado(celdas) and not _es_cabecera_produccion(celdas):
                _cerrar()
                enc = [v for _, v in celdas]
                if amarillo:
                    enc[0] = "[CRITICO-AMARILLO] " + enc[0]
                tabla = {"fila": row_num, "encabezados": enc, "cols": [c for c, _ in celdas], "registros": []}
            elif tabla is not None:
                fila_vals = [""] * len(tabla["encabezados"])
                for col, v in celdas:
                    if col in tabla["cols"]:
                        fila_vals[tabla["cols"].index(col)] = v
                    else:
                        fila_vals.append(v)
                if amarillo and fila_vals:
                    fila_vals[0] = "[CRITICO-AMARILLO] " + fila_vals[0]
                while fila_vals and fila_vals[-1] == "":
                    fila_vals.pop()
                tabla["registros"].append(fila_vals)
            else:
                textos.append({"celda": f"{get_column_letter(celdas[0][0])}{row_num}",
                               "contenido": " | ".join(v for _, v in celdas)})
        _cerrar()

        hojas.append({"hoja": nombre_hoja, "titulo": titulo, "esquemas": esquemas, "tablas": tablas, "textos": textos})

    resultado = {"hojas": hojas, "imagenes": imagenes}
    compacto = json.dumps(resultado, ensure_ascii=False)

    print(f"[IA] Hojas detectadas: {len(hojas)}")
    print(f"[IA] Celdas procesadas: {total_celdas_raw}")
    print(f"[IA] Filas útiles: {total_filas}")
    print(f"[IA] Tablas detectadas: {sum(len(h['tablas']) for h in hojas)}")
    print(f"[IA] Imágenes detectadas: {len(imagenes)}")
    print(f"[IA] Caracteres antes de limpieza: {total_caracteres_raw}")
    print(f"[IA] Caracteres después de limpieza: {len(compacto)}")
    print(f"[IA] Tiempo extracción local: {_time.time() - t0:.2f}s")

    return resultado


def _extraer_texto_archivo(archivo_obj):
    """Extrae texto de PDF, DOCX o XLSX y lo devuelve como string."""
    ext = archivo_obj["extension"].lower()
    ruta = archivo_obj["ruta_archivo"]

    texto = ""

    if ext == "pdf":
        try:
            import pdfplumber

            def _es_amarillo(color):
                try:
                    if isinstance(color, (tuple, list)):
                        if len(color) == 3:
                            r, g, b = color
                            return r >= 0.75 and g >= 0.75 and b <= 0.45
                        if len(color) == 4:
                            c, m, y, k = color
                            return c <= 0.25 and m <= 0.35 and y >= 0.6 and k <= 0.25
                except Exception:
                    pass
                return False

            with pdfplumber.open(ruta) as pdf:
                paginas = []
                for num, pagina in enumerate(pdf.pages, 1):
                    rects_amarillos = []
                    try:
                        for r in pagina.rects:
                            if _es_amarillo(r.get("non_stroking_color")):
                                rects_amarillos.append((r["x0"], r["top"], r["x1"], r["bottom"]))
                    except Exception:
                        pass

                    lineas = []
                    try:
                        for lin in pagina.extract_text_lines():
                            t = (lin.get("text") or "").strip()
                            if not t:
                                continue
                            marca = ""
                            if rects_amarillos:
                                for (x0, top, x1, bottom) in rects_amarillos:
                                    if lin["top"] < bottom and lin["bottom"] > top and lin["x0"] < x1 and lin["x1"] > x0:
                                        marca = "[CRITICO-AMARILLO] "
                                        break
                            lineas.append(marca + t)
                    except Exception:
                        t = pagina.extract_text()
                        if t:
                            lineas.append(t)
                    if lineas:
                        paginas.append(f"===== PÁGINA: {num} =====\n" + "\n".join(lineas))
                texto = "\n".join(paginas)
        except Exception as e:
            texto = f"[Error al extraer texto del PDF: {str(e)}]"

    elif ext == "docx":
        try:
            from docx import Document
            from docx.enum.text import WD_COLOR_INDEX

            def _parrafo_amarillo(p):
                try:
                    return any(r.font.highlight_color == WD_COLOR_INDEX.YELLOW for r in p.runs)
                except Exception:
                    return False

            def _celda_amarilla(celda):
                try:
                    tcPr = celda._tc.tcPr
                    if tcPr is not None:
                        shd = tcPr.find("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}shd")
                        if shd is not None:
                            fill = (shd.get("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}fill") or "").upper()
                            if fill == "FFFF00" or (len(fill) == 6 and fill not in ("AUTO", "FFFFFF") and fill.startswith("FF") and fill.endswith("00")):
                                return True
                    return any(_parrafo_amarillo(p) for p in celda.paragraphs)
                except Exception:
                    return False

            doc = Document(ruta)
            parrafos = []
            for p in doc.paragraphs:
                if p.text.strip():
                    marca = "[CRITICO-AMARILLO] " if _parrafo_amarillo(p) else ""
                    parrafos.append(marca + p.text)
            texto = "\n".join(parrafos)

            # Intentar extraer tablas del DOCX
            if doc.tables:
                texto += "\n\n--- TABLAS ENCONTRADAS ---\n"
                for idx, tabla in enumerate(doc.tables, 1):
                    texto += f"\nTabla {idx}:\n"
                    for fila in tabla.rows:
                        celdas = []
                        fila_amarilla = False
                        for celda in fila.cells:
                            celdas.append(celda.text.strip().replace("\n", " / "))
                            if _celda_amarilla(celda):
                                fila_amarilla = True
                        if any(c for c in celdas):
                            marca = "[CRITICO-AMARILLO] " if fila_amarilla else ""
                            texto += marca + " | ".join(celdas) + "\n"
        except Exception as e:
            texto = f"[Error al extraer texto del DOCX: {str(e)}]"

    elif ext in ("xlsx", "xlsm"):
        try:
            texto = json.dumps(_extraer_estructura_xlsx(archivo_obj), ensure_ascii=False)
        except Exception as e:
            texto = f"[Error al extraer texto del XLSX: {str(e)}]"

    return texto.strip()


def _formato_imagen(data):
    """Detecta el formato real de una imagen a partir de sus bytes (firma del archivo).

    Devuelve (formato, mime). Soporta PNG, JPEG/JFIF (WhatsApp), WEBP, HEIC/HEIF,
    GIF, BMP, TIFF, ICO y AVIF. Si no lo reconoce devuelve (None, None)."""
    if not data:
        return None, None
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png", "image/png"
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg", "image/jpeg"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif", "image/gif"
    if data[:2] == b"BM":
        return "bmp", "image/bmp"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp", "image/webp"
    if data[:4] == b"II*\x00" or data[:4] == b"MM\x00*":
        return "tiff", "image/tiff"
    if data[:4] == b"\x00\x00\x01\x00":
        return "ico", "image/x-icon"
    if len(data) >= 12 and data[4:8] == b"ftyp":
        brand = data[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"hevx"):
            return "heic", "image/heic"
        if brand in (b"heif", b"mif1", b"msf1"):
            return "heif", "image/heif"
        if brand == b"avif":
            return "avif", "image/avif"
    return None, None


def _convertir_a_png(data):
    """Convierte una imagen a PNG usando Pillow (para formatos que Gemini no
    acepta de forma nativa). Devuelve (bytes_png, 'image/png') o (None, None)."""
    try:
        from PIL import Image as PILImage
        im = PILImage.open(BytesIO(data))
        if im.mode in ("RGBA", "LA", "P"):
            im = im.convert("RGBA")
        else:
            im = im.convert("RGB")
        buf = BytesIO()
        im.save(buf, format="PNG")
        return buf.getvalue(), "image/png"
    except Exception:
        return None, None


def _extraer_imagenes_archivo(archivo_obj):
    """Extrae las imágenes incrustadas en un XLSX junto con su ubicación (hoja y celda).

    Detecta el formato real desde los bytes (soporta JPEG/JFIF de WhatsApp, WEBP,
    HEIC/HEIF, GIF, BMP, TIFF, ICO, AVIF...) y convierte a PNG lo que Gemini no lee
    de forma nativa. Devuelve una lista de dicts: {indice, hoja, celda, formato,
    mime, bytes, ancho, alto}."""
    imagenes = []
    ext = (archivo_obj.get("extension") or "").lower()
    ruta = archivo_obj.get("ruta_archivo")
    if ext not in ("xlsx", "xlsm") or not ruta or not os.path.exists(ruta):
        return imagenes

    # Formatos que Gemini acepta directamente como entrada de imagen.
    FORMATOS_GEMINI = {"png", "jpeg", "webp", "heic", "heif"}

    try:
        from openpyxl import load_workbook
        from openpyxl.utils import get_column_letter

        wb = load_workbook(ruta, data_only=True)
        for ws in wb.worksheets:
            try:
                ws_images = ws._images
            except Exception:
                continue
            for img in ws_images:
                try:
                    data = img._data()
                    if not data:
                        continue

                    formato, mime = _formato_imagen(data)

                    # Dimensiones: preferir Pillow (exacto); si no, openpyxl.
                    ancho = getattr(img, "width", None)
                    alto = getattr(img, "height", None)
                    try:
                        from PIL import Image as PILImage
                        with PILImage.open(BytesIO(data)) as im:
                            ancho, alto = im.size
                    except Exception:
                        pass

                    if formato in FORMATOS_GEMINI:
                        final_data, final_mime = data, mime
                    else:
                        final_data, final_mime = _convertir_a_png(data)
                        if not final_data:
                            continue
                        if not formato:
                            formato = "png"

                    celda = ""
                    try:
                        anchor = getattr(img, "anchor", None)
                        src = getattr(anchor, "_from", None) if anchor is not None else None
                        if src is not None:
                            col = int(getattr(src, "col", 0) or 0) + 1
                            row = int(getattr(src, "row", 0) or 0) + 1
                            celda = f"{get_column_letter(col)}{row}"
                    except Exception:
                        celda = ""

                    imagenes.append({
                        "indice": len(imagenes) + 1,
                        "hoja": ws.title,
                        "celda": celda,
                        "formato": formato or "png",
                        "mime": final_mime,
                        "bytes": final_data,
                        "ancho": ancho,
                        "alto": alto,
                    })
                except Exception:
                    continue
    except Exception:
        pass
    return imagenes


def _cargar_pront():
    """Carga el prompt de análisis desde pront.json (única fuente del prompt de IA)."""
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pront.json")
    with open(ruta, "r", encoding="utf-8") as f:
        return f.read()


def _cargar_formato():
    """Carga la descripción del formato del Reporte de Guardia desde
    formato_guardia_mtto.md (fuente única de la estructura a analizar)."""
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "formato_guardia_mtto.md")
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"[FORMATO] No se pudo cargar formato_guardia_mtto.md: {e}")
        return ""


def _cargar_formato_reporte():
    """Carga la descripción del formato del Reporte (PPT) desde
    formato_reporte_ppt.md (estructura de salida del reporte)."""
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "formato_reporte_ppt.md")
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            return f.read()
    except Exception as e:
        print(f"[FORMATO] No se pudo cargar formato_reporte_ppt.md: {e}")
        return ""


def _extraer_json_respuesta(texto):
    """Extrae un JSON válido de la respuesta de Gemini, tolerando markdown,
    bloques de código y texto adicional antes o después del JSON."""
    if not texto:
        return None
    t = texto.strip()
    if t.startswith("\ufeff"):
        t = t[1:].strip()

    # Quitar bloques de código markdown (```json ... ```)
    t = re.sub(r"```(?:json|JSON)?\s*", "", t)
    t = re.sub(r"\s*```", "", t)
    t = t.strip()

    # Intento directo
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        pass

    # Aislar el primer objeto JSON ({ ... }) si hay texto adicional alrededor
    inicio = t.find("{")
    fin = t.rfind("}")
    if inicio != -1 and fin > inicio:
        try:
            return json.loads(t[inicio:fin + 1])
        except json.JSONDecodeError:
            pass

    return None


def _normalizar_evidencia(ev, indice):
    """Normaliza una evidencia a la estructura canónica actual, soportando también
    el esquema anterior (tipo/fecha/lugar/que_paso/descripcion)."""
    if not isinstance(ev, dict):
        ev = {}

    # Esquema anterior: usa fecha/lugar/que_paso/descripcion en lugar de los
    # campos actuales. Se detecta por presencia de las claves antiguas y ausencia
    # de las nuevas.
    es_viejo = ("que_paso" in ev or "lugar" in ev or "fecha" in ev) \
        and "pozo_equipo" not in ev and "hallazgos" not in ev

    if es_viejo:
        descripcion = ev.get("que_paso", "")
        hallazgos = ev.get("descripcion", "")
        fecha_hora = ev.get("fecha", "")
        pozo_equipo = ev.get("lugar", "")
    else:
        descripcion = ev.get("descripcion", "")
        hallazgos = ev.get("hallazgos", "")
        fecha_hora = ev.get("fecha_hora", "")
        pozo_equipo = ev.get("pozo_equipo", "")

    return {
        "n_evidencia": ev.get("n_evidencia") or (indice + 1),
        "item_ref": ev.get("item_ref", ""),
        "pozo_equipo": pozo_equipo,
        "titulo": ev.get("titulo", ""),
        "descripcion": descripcion,
        "hallazgos": hallazgos,
        "accion_tomada": ev.get("accion_tomada", ""),
        "responsable": ev.get("responsable", ""),
        "fecha_hora": fecha_hora,
        "tipo": ev.get("tipo", ""),
        "imagen": ev.get("imagen", ""),
        "imagen_indice": ev.get("imagen_indice"),
    }


def _normalizar_resultado(resultado):
    """Adapta resultados cacheados con esquemas anteriores al esquema actual.

    Normaliza las evidencias (dentro de registro_guardia y a nivel superior) para
    que tanto la vista web como el generador de PPT reciban siempre la estructura
    canónica, sin importar con qué prompt se generó originalmente."""
    if not isinstance(resultado, dict):
        return resultado

    reg = resultado.get("registro_guardia")
    if isinstance(reg, dict):
        evs = reg.get("evidencias")
        if isinstance(evs, list):
            reg["evidencias"] = [_normalizar_evidencia(e, i) for i, e in enumerate(evs)]

    evs_top = resultado.get("evidencias")
    if isinstance(evs_top, list):
        resultado["evidencias"] = [_normalizar_evidencia(e, i) for i, e in enumerate(evs_top)]

    return resultado


def _analizar_con_gemini(texto, titulo, descripcion, imagenes=None):
    """Envía el texto (y opcionalmente las imágenes del documento) a Gemini y
    devuelve la respuesta estructurada. Prueba múltiples modelos en cascada si
    hay error de cuota."""
    imagenes = imagenes or []

    # Configuración de API Key y modelos de fallback
    from google import genai
    import time

    api_key = os.getenv("GEMINI_API_KEY")
    model_principal = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

    if not api_key or api_key == "TU_GEMINI_API_KEY":
        print("[IA] API Key de Gemini no configurada")
        return {
            "error": "El análisis no está disponible en este momento. Inténtalo nuevamente en unos minutos."
        }

    # Modelos de Google en orden de prioridad (más capaz → menos capaz)
    # Se prueban en cascada: si uno falla por cuota/demanda, el siguiente toma el relevo.
    # Nota: gemini-2.0-flash y gemini-2.0-flash-lite ya fueron descontinuados (404).
    modelos_fallback = [
        model_principal,
        "gemini-3-flash-preview",
        "gemini-2.5-flash-lite",
    ]
    # Eliminar duplicados manteniendo el orden
    modelos = list(dict.fromkeys(modelos_fallback))

    timeout_ms = int(os.getenv("GEMINI_TIMEOUT_MS", "120000"))
    client = genai.Client(api_key=api_key, http_options={"timeout": timeout_ms})

    # Límites configurables por entorno para controlar el costo/latencia de Gemini.
    max_input_chars = int(os.getenv("GEMINI_MAX_INPUT_CHARS", "60000"))
    max_output_tokens = int(os.getenv("GEMINI_MAX_OUTPUT_TOKENS", "8000"))
    max_imagenes = int(os.getenv("GEMINI_MAX_IMAGENES", "8"))

    # Thinking desactivado por defecto (GEMINI_THINKING=0): la interpretación de
    # contenido ya estructurado no necesita razonamiento profundo; prioriza velocidad.
    thinking_activo = os.getenv("GEMINI_THINKING", "0").strip().lower() not in ("", "0", "false", "no")

    # Configuración de generación para reducir latencia: salida forzada como JSON
    # válido (evita reintentos por JSON inválido), temperatura baja y límite de tokens.
    config_kwargs = dict(
        temperature=0.2,
        max_output_tokens=max_output_tokens,
        response_mime_type="application/json",
    )
    if not thinking_activo:
        try:
            config_kwargs["thinking_config"] = genai.types.ThinkingConfig(thinking_budget=0)
        except Exception:
            pass

    config_gen = genai.types.GenerateContentConfig(**config_kwargs)

    prompt = (_cargar_pront()
              .replace("<<<TITULO>>>", titulo or "")
              .replace("<<<DESCRIPCION>>>", descripcion or "")
              .replace("<<<CONTENIDO>>>", texto[:max_input_chars]))

    print(f"[IA] Tamaño aproximado enviado a Gemini: {len(prompt)} caracteres")

    t_inicio_gemini = time.time()

    # Limitar la cantidad de imágenes enviadas para no exceder el tamaño/latencia.
    imagenes = imagenes[:max_imagenes]

    # Construir el contenido multimodal: texto + imágenes adjuntas en orden.
    contents = [genai.types.Part.from_text(text=prompt)]
    if imagenes:
        refs = []
        for im in imagenes:
            ubicacion = f'Hoja "{im.get("hoja", "")}"'
            if im.get("celda"):
                ubicacion += f" (celda {im['celda']})"
            refs.append(f"Imagen {im['indice']}: {ubicacion}")
        prompt += "\n\nIMÁGENES DEL DOCUMENTO (enviadas adjuntas en este MISMO orden):\n" + "\n".join(refs)
        contents = [genai.types.Part.from_text(text=prompt)]
        for im in imagenes:
            try:
                contents.append(genai.types.Part.from_bytes(data=im["bytes"], mime_type=im["mime"]))
            except Exception:
                continue

    max_intentos_por_modelo = 2
    ultimo_error = None
    texto_respuesta = ""

    for modelo in modelos:
        for intento in range(1, max_intentos_por_modelo + 1):
            try:
                t_solicitud = time.time()
                respuesta = client.models.generate_content(
                    model=modelo,
                    contents=contents,
                    config=config_gen,
                )
                t_respuesta = time.time()
                texto_respuesta = (respuesta.text or "").strip()

                # Métricas de uso (tokens de entrada/salida) cuando están disponibles.
                try:
                    uso = respuesta.usage_metadata
                    tokens_entrada = getattr(uso, "prompt_token_count", None)
                    tokens_salida = getattr(uso, "candidates_token_count", None)
                except Exception:
                    tokens_entrada = tokens_salida = None

                resultado = _extraer_json_respuesta(texto_respuesta)
                t_parseo = time.time()
                if resultado is None:
                    print(f"[IA] {modelo}: respuesta sin JSON válido (intento {intento}/{max_intentos_por_modelo})")
                    ultimo_error = "La IA devolvió una respuesta no válida."
                    if intento < max_intentos_por_modelo:
                        time.sleep(1)
                        continue
                    break

                print(f"[IA] Modelo utilizado: {modelo}")
                print(f"[IA] Caracteres enviados: {len(prompt)}")
                print(f"[IA] Imágenes enviadas: {len(imagenes)}")
                print(f"[IA] Tokens de entrada: {tokens_entrada}")
                print(f"[IA] Tokens de salida: {tokens_salida}")
                print(f"[IA] Tiempo solicitud->respuesta Gemini: {t_respuesta - t_solicitud:.2f}s")
                print(f"[IA] Tiempo parseo JSON: {t_parseo - t_respuesta:.4f}s")
                print(f"[IA] Tiempo total llamada Gemini: {t_parseo - t_inicio_gemini:.2f}s")
                return resultado

            except json.JSONDecodeError as e:
                print(f"[IA] Error al interpretar la respuesta de Gemini: {e}")
                ultimo_error = "La IA devolvió una respuesta no válida."
                if intento < max_intentos_por_modelo:
                    time.sleep(1)
                    continue
                break
            except Exception as e:
                error_str = str(e)
                es_503 = "503" in error_str or "UNAVAILABLE" in error_str.upper()
                es_conexion = any(s in error_str.lower() for s in ("disconnected", "connection", "timeout", "timed out"))
                es_429 = "429" in error_str or "RESOURCE_EXHAUSTED" in error_str.upper()
                es_404 = "404" in error_str or "NOT_FOUND" in error_str.upper()
                es_no_encontrado = "no se encuentra" in error_str.lower()

                if es_429:
                    ultimo_error = error_str
                    # Si es el último intento de este modelo, pasar al siguiente
                    if intento >= max_intentos_por_modelo:
                        break
                    # Si no, esperar y reintentar
                    match = re.search(r'retry in ([\d.]+)s', error_str, re.IGNORECASE)
                    espera = float(match.group(1)) + 1 if match else 3
                    time.sleep(espera)
                    continue

                if es_404 or es_no_encontrado:
                    # Modelo no encontrado/deprecado, saltar al siguiente
                    ultimo_error = f"Modelo {modelo} no disponible. Probando siguiente..."
                    break

                if es_503 and intento < max_intentos_por_modelo:
                    time.sleep(2)
                    continue

                if es_conexion and intento < max_intentos_por_modelo:
                    time.sleep(1)
                    continue

                if es_503:
                    ultimo_error = "Gemini está experimentando alta demanda. Inténtalo nuevamente en unos minutos."
                    break

                if es_conexion:
                    ultimo_error = "Gemini está experimentando alta demanda en este momento."
                    break

                # Error desconocido: devolver inmediatamente
                print(f"[IA] Error al conectar con Gemini: {error_str}")
                return {
                    "error": f"Error de Gemini: {error_str}"
                }

    # Todos los modelos gratuitos fallaron por cuota.
    # Intentar con API Key de pago si está configurada (respaldo final)
    api_key_pago = os.getenv("GEMINI_API_KEY_PAGO", "").strip()
    if api_key_pago and api_key_pago != api_key:
        try:
            client_pago = genai.Client(api_key=api_key_pago)
            respuesta = client_pago.models.generate_content(
                model=model_principal,
                contents=contents,
                config=config_gen,
            )
            resultado = _extraer_json_respuesta((respuesta.text or "").strip())
            if resultado is not None:
                return resultado
        except Exception:
            pass

    print("[IA] Cuota de Gemini agotada o servicio no disponible en este momento")
    detalle = ultimo_error or "servicio no disponible"
    return {
        "error": f"El análisis no está disponible en este momento. Detalle: {detalle}"
    }


# ── Ruta para la página de análisis con IA ──
@informe_bp.route("/analizar_informe/<int:id_informe>")
def analizar_informe(id_informe):
    """Página de análisis del documento con Gemini."""
    if "usuario" not in session:
        return redirect(url_for("home"))
    perfil = session.get("perfil_activo")
    if perfil not in ("fiscalizador", "admin"):
        return acceso_no_autorizado()

    conn = None
    cursor = None

    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        if perfil == "admin":
            cursor.execute("""
                SELECT i.*, g.fecha_guardia
                FROM informes i
                INNER JOIN guardias g ON i.id_guardia = g.id_guardia
                WHERE i.id_informe = %s
                AND i.estado = 'activo'
            """, (id_informe,))
        else:
            cursor.execute("""
                SELECT i.*, g.fecha_guardia
                FROM informes i
                INNER JOIN guardias g ON i.id_guardia = g.id_guardia
                WHERE i.id_informe = %s
                AND i.id_usuario = %s
                AND i.estado = 'activo'
            """, (id_informe, session["id_usuario"]))
        informe = cursor.fetchone()

        if not informe:
            return no_encontrado("Informe no encontrado")

        return render_template("analizar_informe.html", informe=informe)

    finally:
        if cursor is not None: cursor.close()
        if conn is not None: conn.close()


@informe_bp.route("/analizar_informe/<int:id_informe>/api")
@limiter.limit("5 per minute")
def analizar_informe_api(id_informe):
    """API que extrae texto, llama a Gemini y devuelve JSON.
    Usa cache en BD: solo llama a Gemini si no hay resultado previo o si se fuerza (?force=1)."""

    # Verificar sesión y perfil activo
    if "usuario" not in session:
        return jsonify({"error": "No autorizado"}), 401
    perfil = session.get("perfil_activo")
    if perfil not in ("fiscalizador", "admin"):
        return jsonify({"error": "Acceso no autorizado"}), 403

    forzar = request.args.get("force") == "1"

    conn = None
    cursor = None

    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        if perfil == "admin":
            cursor.execute("""
                SELECT i.*, g.fecha_guardia
                FROM informes i
                INNER JOIN guardias g ON i.id_guardia = g.id_guardia
                WHERE i.id_informe = %s
                AND i.estado = 'activo'
            """, (id_informe,))
        else:
            cursor.execute("""
                SELECT i.*, g.fecha_guardia
                FROM informes i
                INNER JOIN guardias g ON i.id_guardia = g.id_guardia
                WHERE i.id_informe = %s
                AND i.id_usuario = %s
            AND i.estado = 'activo'
        """, (id_informe, session["id_usuario"]))
        informe = cursor.fetchone()

        if not informe:
            return jsonify({"error": "Informe no encontrado"}), 404

        # Servir desde caché si existe y no se fuerza re-análisis
        if not forzar and informe.get("resultado_analisis"):
            import json as _json
            cache = informe["resultado_analisis"]
            if isinstance(cache, str):
                cache = _json.loads(cache)
            return jsonify(_normalizar_resultado(cache))

        ruta = informe["ruta_archivo"]
        if not os.path.exists(ruta):
            return jsonify({"error": "El archivo no existe en el servidor"}), 404

        import time as _time

        t_inicio_total = _time.time()

        # Extraer texto (y estructura) del archivo
        t_extraccion = _time.time()
        texto = _extraer_texto_archivo(informe)
        t_extraccion_fin = _time.time()

        if not texto:
            return jsonify({"error": "No se pudo extraer texto del archivo"}), 400

        if texto.startswith("[Error"):
            return jsonify({"error": texto}), 500

        # Extraer las imágenes incrustadas (referencias, no se envían todas por defecto)
        t_imagenes = _time.time()
        imagenes = _extraer_imagenes_archivo(informe)
        t_imagenes_fin = _time.time()

        # Analizar con Gemini
        t_gemini = _time.time()
        resultado = _analizar_con_gemini(
            texto,
            informe["titulo"] or "",
            informe["descripcion"] or "",
            imagenes
        )
        t_gemini_fin = _time.time()

        print("[PERFORMANCE]")
        print(f"[PERFORMANCE] Extracción Excel: {t_extraccion_fin - t_extraccion:.2f}s")
        print(f"[PERFORMANCE] Extracción imágenes: {t_imagenes_fin - t_imagenes:.2f}s")
        print(f"[PERFORMANCE] Llamada Gemini (total): {t_gemini_fin - t_gemini:.2f}s")
        print(f"[PERFORMANCE] TOTAL: {t_gemini_fin - t_inicio_total:.2f}s")

        # Guardar en caché si el análisis fue exitoso (sin error)
        if not resultado.get("error"):
            try:
                cursor.execute(
                    "UPDATE informes SET resultado_analisis = %s WHERE id_informe = %s",
                    (json.dumps(resultado, ensure_ascii=False), id_informe)
                )
                conn.commit()
            except Exception as e:
                print(f"[ADVERTENCIA] No se pudo guardar el analisis en cache (informe {id_informe}): {e}")

        return jsonify(resultado)

    finally:
        if cursor is not None: cursor.close()
        if conn is not None: conn.close()


@informe_bp.route("/analizar_informe/<int:id_informe>/imagen/<int:indice>")
def analizar_informe_imagen(id_informe, indice):
    """Sirve la imagen incrustada en el Excel en la posición indicada (1-based)."""
    if "usuario" not in session:
        return jsonify({"error": "No autorizado"}), 401
    perfil = session.get("perfil_activo")
    if perfil not in ("fiscalizador", "admin"):
        return jsonify({"error": "Acceso no autorizado"}), 403

    conn = None
    cursor = None
    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        if perfil == "admin":
            cursor.execute("""
                SELECT i.ruta_archivo, i.tipo_archivo
                FROM informes i
                WHERE i.id_informe = %s AND i.estado = 'activo'
            """, (id_informe,))
        else:
            cursor.execute("""
                SELECT i.ruta_archivo, i.tipo_archivo
                FROM informes i
                WHERE i.id_informe = %s AND i.id_usuario = %s AND i.estado = 'activo'
            """, (id_informe, session["id_usuario"]))
        informe = cursor.fetchone()
        if not informe:
            return jsonify({"error": "Informe no encontrado"}), 404

        imagenes = _extraer_imagenes_archivo({
            "extension": informe.get("tipo_archivo", ""),
            "ruta_archivo": informe.get("ruta_archivo", ""),
        })

        idx = indice - 1
        if idx < 0 or idx >= len(imagenes):
            return jsonify({"error": "Imagen no encontrada"}), 404

        img = imagenes[idx]
        return send_file(
            BytesIO(img["bytes"]),
            mimetype=img.get("mime", "image/png"),
        )
    finally:
        if cursor is not None: cursor.close()
        if conn is not None: conn.close()


@informe_bp.route("/analizar_informe/<int:id_informe>/ppt", methods=["POST"])
def generar_ppt_analisis(id_informe):
    """Genera una presentación PPTX profesional con los resultados del análisis."""

    # Validar sesión y datos de entrada
    if "usuario" not in session:
        return jsonify({"error": "No autorizado"}), 401
    perfil = session.get("perfil_activo")
    if perfil not in ("fiscalizador", "admin"):
        return jsonify({"error": "Acceso no autorizado"}), 403

    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Datos no proporcionados o formato invalido"}), 400

    conn = None
    cursor = None

    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        if perfil == "admin":
            cursor.execute("""
                SELECT i.titulo, i.nombre_archivo, i.tipo_archivo, i.ruta_archivo,
                       g.fecha_guardia,
                       CONCAT(u.nombre, ' ', u.apellidos) AS fiscalizador
                FROM informes i
                INNER JOIN guardias g ON i.id_guardia = g.id_guardia
                INNER JOIN usuarios u ON i.id_usuario = u.id_usuario
                WHERE i.id_informe = %s AND i.estado = 'activo'
            """, (id_informe,))
        else:
            cursor.execute("""
                SELECT i.titulo, i.nombre_archivo, i.tipo_archivo, i.ruta_archivo,
                       g.fecha_guardia,
                       CONCAT(u.nombre, ' ', u.apellidos) AS fiscalizador
                FROM informes i
                INNER JOIN guardias g ON i.id_guardia = g.id_guardia
                INNER JOIN usuarios u ON i.id_usuario = u.id_usuario
                WHERE i.id_informe = %s AND i.id_usuario = %s AND i.estado = 'activo'
            """, (id_informe, session["id_usuario"]))
        informe = cursor.fetchone()

        if not informe:
            return jsonify({"error": "Informe no encontrado"}), 404

        # Re-extraer las imágenes incrustadas en el archivo para incluirlas en el PPT
        imagenes_archivo = _extraer_imagenes_archivo({
            "extension": informe.get("tipo_archivo", ""),
            "ruta_archivo": informe.get("ruta_archivo", ""),
        })

        # Generar la presentación con el formato "Reporte de Guardia" (rv0)
        from utils.generar_informe_ppt import generar_informe_ppt
        prs = generar_informe_ppt(informe, data, imagenes_archivo)

        # ── Guardar presentación en memoria y enviar como descarga ──
        output = BytesIO()
        prs.save(output)
        output.seek(0)

        nombre_archivo = secure_filename(informe.get("titulo", "analisis") or "analisis")
        nombre_ppt = f"Analisis_IA_{nombre_archivo}.pptx"

        return send_file(
            output,
            as_attachment=True,
            download_name=nombre_ppt,
            mimetype="application/vnd.openxmlformats-officedocument.presentationml.presentation"
        )

    except Exception as e:
        return jsonify({"error": f"Error al generar PPT: {str(e)}"}), 500

    finally:
        if cursor is not None: cursor.close()
        if conn is not None: conn.close()
