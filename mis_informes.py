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
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
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
UPLOAD_FOLDER = "uploads"
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

            id_guardia = request.form["id_guardia"]
            titulo = request.form["titulo"]
            descripcion = request.form["descripcion"]

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

            titulo = request.form["titulo"]
            descripcion = request.form["descripcion"]

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
            """, (
                titulo,
                descripcion,
                id_informe
            ))

            archivo = request.files.get("archivo")

            if archivo and archivo.filename != "":

                if archivo_permitido(archivo.filename):

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

                    extension = nombre.rsplit(".", 1)[1].lower()

                    cursor.execute("""
                        UPDATE informes
                        SET nombre_archivo=%s,
                            ruta_archivo=%s,
                            tipo_archivo=%s,
                            extension=%s,
                            tamano_archivo=%s
                        WHERE id_informe=%s
                    """, (
                        nombre,
                        ruta,
                        extension,
                        extension,
                        tamano_nuevo,
                        id_informe
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

        cursor.execute("""
            UPDATE informes
            SET estado = 'eliminado'
            WHERE id_informe = %s
            AND id_usuario = %s
        """, (id_informe, session["id_usuario"]))

        conn.commit()

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
            from openpyxl import load_workbook
            wb = load_workbook(ruta, data_only=True)
            for nombre_hoja in wb.sheetnames:
                ws = wb[nombre_hoja]
                texto += f"\n===== HOJA: {nombre_hoja} =====\n"
                for fila in ws.iter_rows():
                    celdas = []
                    fila_amarilla = False
                    for c in fila:
                        valor = str(c.value).replace("\n", " / ") if c.value is not None else ""
                        celdas.append(valor)
                        try:
                            f = c.fill
                            if f is not None and f.patternType == "solid":
                                rgb = getattr(f.fgColor, "rgb", None)
                                if isinstance(rgb, str) and rgb.upper() in ("FFFFFF00", "00FFFF00"):
                                    fila_amarilla = True
                        except Exception:
                            pass
                    if any(c for c in celdas):
                        marca = "[CRITICO-AMARILLO] " if fila_amarilla else ""
                        texto += marca + " | ".join(celdas) + "\n"
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


def _validar_coherencia_analisis(resultado):
    """Corrige de forma determinista las inconsistencias aritméticas del análisis:
    los totales del módulo 1 deben coincidir con los desgloses y los módulos 2, 3 y 4."""

    # Ajustar conteos: trabajos críticos, producción recuperada y total de actividades
    try:
        m1 = resultado.get("modulo1_resumen_ejecutivo")
        if not isinstance(m1, dict):
            return resultado

        # Trabajos críticos = cantidad real de filas en modulo3
        criticos = resultado.get("modulo3_trabajos_importantes")
        if isinstance(criticos, list):
            m1["total_trabajos_criticos"] = len(criticos)

        # Producción recuperada = suma exacta de bopd_por_taller
        m4 = resultado.get("modulo4_resumen_operativo")
        if isinstance(m4, dict):
            bopd = m4.get("bopd_por_taller")
            if isinstance(bopd, dict) and bopd:
                valores = [v for v in bopd.values() if isinstance(v, (int, float))]
                m1["total_produccion_recuperada_bopd"] = round(sum(valores), 2)
            tipos = m4.get("trabajos_por_tipo")
            if isinstance(tipos, dict) and tipos:
                m1["desglose_por_tipo"] = tipos

        # Total de actividades = suma de ejecutadas del desglose por taller
        desglose = m1.get("desglose_por_taller")
        if isinstance(desglose, dict) and desglose:
            total_ej = 0
            valido = True
            for v in desglose.values():
                if isinstance(v, dict) and isinstance(v.get("ejecutadas"), (int, float)):
                    total_ej += v["ejecutadas"]
                elif isinstance(v, (int, float)):
                    total_ej += v
                else:
                    valido = False
                    break
            if valido and total_ej > 0:
                m1["total_actividades"] = int(total_ej)

        # Eficacia por taller recalculada exactamente
        m2 = resultado.get("modulo2_eficacia_taller")
        if isinstance(m2, list):
            for fila in m2:
                if isinstance(fila, dict):
                    ej = fila.get("ejecutadas")
                    comp = fila.get("completadas")
                    if isinstance(ej, (int, float)) and isinstance(comp, (int, float)) and ej > 0:
                        fila["eficacia"] = round(comp * 100.0 / ej, 2)
    except Exception:
        pass
    return resultado


def _cargar_pront():
    """Carga el prompt de análisis desde pront.json (única fuente del prompt de IA)."""
    ruta = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pront.json")
    with open(ruta, "r", encoding="utf-8") as f:
        return f.read()


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
            "error": "El análisis no está disponible en este momento. Inténtalo nuevamente en unos minutos.",
            "recomendaciones": []
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

    client = genai.Client(api_key=api_key, http_options={"timeout": 120000})

    # Configuración de generación para reducir latencia: salida forzada como JSON
    # válido (evita reintentos por JSON inválido), temperatura baja y límite de
    # tokens para que el "resumen" no se extienda innecesariamente.
    config_gen = genai.types.GenerateContentConfig(
        temperature=0.2,
        max_output_tokens=5000,
        response_mime_type="application/json",
    )

    prompt = _cargar_pront() + f"""

TÍTULO DEL DOCUMENTO: {titulo}
DESCRIPCIÓN: {descripcion}

CONTENIDO DEL DOCUMENTO:
{texto[:30000]}

6. ESTRUCTURA DE SALIDA REQUERIDA
Devuelve ÚNICAMENTE un JSON válido (sin markdown, sin comillas triples) con esta estructura exacta:

{{
  "resumen": "Pega aquí el texto completo del MÓDULO 1 (Resumen Ejecutivo) en formato legible, incluyendo total de actividades, producción recuperada en BOPD, total de trabajos críticos y desglose por tipo y taller.",
  "hallazgos": ["Hallazgo clave 1", "Hallazgo clave 2", "Hallazgo clave 3", "Hallazgo clave 4", "Hallazgo clave 5"],
  "recomendaciones": ["Recomendación 1", "Recomendación 2", "Recomendación 3", "Recomendación 4", "Recomendación 5"],
  "graficas": [
    {{
      "tipo": "bar",
      "titulo": "Actividades por Taller Unificado",
      "labels": ["<TALLERES REALES UNIFICADOS DEL DOCUMENTO>"],
      "datasets": [
        {{"label": "Ejecutadas", "data": [0]}},
        {{"label": "Completadas", "data": [0]}}
      ]
    }},
    {{
      "tipo": "pie",
      "titulo": "Distribución de Trabajos por Tipo",
      "labels": ["<TIPOS REALES DEL DOCUMENTO, ej. CNP, PV, SOP, SUS>"],
      "datasets": [
        {{"label": "Cantidad", "data": [0], "backgroundColor": ["#22c55e", "#f59e0b", "#3b82f6", "#8b5cf6"]}}
      ]
    }},
    {{
      "tipo": "bar",
      "titulo": "Producción Recuperada CNP por Taller (BOPD)",
      "labels": ["<TALLERES REALES CON CNP COMPLETADO>"],
      "datasets": [
        {{"label": "BOPD Recuperados", "data": [0], "backgroundColor": ["#1e3a5f", "#2563eb", "#3b82f6", "#60a5fa"]}}
      ]
    }}
  ],
  "modulo1_resumen_ejecutivo": {{
    "total_actividades": 0,
    "total_produccion_recuperada_bopd": 0,
    "total_trabajos_criticos": 0,
    "desglose_por_tipo": {{}},
    "desglose_por_taller": {{}}
  }},
  "modulo2_eficacia_taller": [
    {{"taller": "MONTAJE", "ejecutadas": 0, "completadas": 0, "eficacia": 0.0}}
  ],
  "modulo3_trabajos_importantes": [
    {{"taller": "MONTAJE", "pozo": "XXX-001", "falla": "Descripción de la falla", "solucion": "Descripción de la solución"}}
  ],
  "modulo4_resumen_operativo": {{
    "bopd_por_taller": {{}},
    "trabajos_por_tipo": {{}}
  }},
  "modulo5_pendientes": [
    {{"taller": "MONTAJE", "pozo": "XXX-001", "requerimiento": "Descripción de la tarea pendiente", "estado": "pendiente"}}
  ],
  "analisis_imagenes": [
    {{"referencia": "Imagen 1 — Hoja 'Guardia 01' (celda C5)", "descripcion": "Descripción de lo que muestra la imagen y qué información aporta"}}
  ]
}}

IMPORTANTE: 
- Responde SOLO con el JSON válido, sin explicaciones ni markdown.
- Reemplaza TODOS los valores de ejemplo con los datos REALES extraídos del documento.
- Llena TODOS los arrays y objetos con los datos correctos según tu análisis.
- El campo "resumen" debe contener el texto completo del MÓDULO 1: RESUMEN EJECUTIVO DE GUARDIA con formato legible (usa saltos de línea \\n para separar secciones).
- "analisis_imagenes": describe cada imagen enviada en el MISMO orden (Imagen 1, Imagen 2, ...). En "referencia" usa la ubicación indicada (hoja y celda) y en "descripcion" escribe qué muestra la imagen y qué información aporta. Devuelve [] si el documento no contiene imágenes.
- VERIFICACIÓN FINAL OBLIGATORIA antes de responder:
  a) modulo3_trabajos_importantes debe contener TODAS las filas marcadas "[CRITICO-AMARILLO]" (tras deduplicar por pozo+requerimiento) y NINGUNA actividad sin esa marca.
  b) modulo5_pendientes SOLO debe contener tareas cuyo Estado FINAL (último día donde aparece la tarea) sea distinto de 1. Revisa tarea por tarea: si su última aparición tiene Estado = 1, elimínala de pendientes (ej. una tarea con Estado 1 en todas sus apariciones NUNCA es pendiente). Y toda tarea cuya última aparición tenga Estado 0, decimal o vacío DEBE estar en pendientes, aunque aparezca un solo día.
  c) Recalcula cada suma: total_produccion_recuperada_bopd debe ser EXACTAMENTE igual a la suma de bopd_por_taller, y total_actividades igual a la suma de "ejecutadas" del desglose_por_taller. Corrige cualquier inconsistencia antes de responder.
  d) bopd_por_taller debe incluir SOLO talleres cuya suma de CNP completados sea mayor que 0 (omite talleres con 0)."""

    # Limitar la cantidad de imágenes enviadas para no exceder el tamaño/latencia.
    imagenes = imagenes[:8]

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
                respuesta = client.models.generate_content(
                    model=modelo,
                    contents=contents,
                    config=config_gen,
                )
                texto_respuesta = (respuesta.text or "").strip()

                resultado = _extraer_json_respuesta(texto_respuesta)
                if resultado is None:
                    print(f"[IA] {modelo}: respuesta sin JSON válido (intento {intento}/{max_intentos_por_modelo})")
                    ultimo_error = "La IA devolvió una respuesta no válida."
                    if intento < max_intentos_por_modelo:
                        time.sleep(1)
                        continue
                    break

                return _validar_coherencia_analisis(resultado)

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
                    "error": f"Error de Gemini: {error_str}",
                    "hallazgos": [],
                    "recomendaciones": [],
                    "graficas": []
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
                return _validar_coherencia_analisis(resultado)
        except Exception:
            pass

    print("[IA] Cuota de Gemini agotada o servicio no disponible en este momento")
    detalle = ultimo_error or "servicio no disponible"
    return {
        "error": f"El análisis no está disponible en este momento. Detalle: {detalle}",
        "hallazgos": [],
        "recomendaciones": [],
        "graficas": []
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
            return jsonify(cache)

        ruta = informe["ruta_archivo"]
        if not os.path.exists(ruta):
            return jsonify({"error": "El archivo no existe en el servidor"}), 404

        # Extraer texto del archivo
        texto = _extraer_texto_archivo(informe)

        if not texto:
            return jsonify({"error": "No se pudo extraer texto del archivo"}), 400

        if texto.startswith("[Error"):
            return jsonify({"error": texto}), 500

        # Analizar con Gemini (incluyendo las imágenes incrustadas, si las hay)
        imagenes = _extraer_imagenes_archivo(informe)
        resultado = _analizar_con_gemini(
            texto,
            informe["titulo"] or "",
            informe["descripcion"] or "",
            imagenes
        )

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

        prs = Presentation()
        prs.slide_width = Inches(13.333)
        prs.slide_height = Inches(7.5)

        # Paleta de colores corporativos para la presentación
        C_BG_DARK   = RGBColor(0x08, 0x0E, 0x1A)
        C_PRIMARY   = RGBColor(0x1E, 0x3A, 0x5F)
        C_ACCENT    = RGBColor(0x63, 0x66, 0xF1)
        C_ACCENT2   = RGBColor(0x8B, 0x5C, 0xF6)
        C_WHITE     = RGBColor(0xFF, 0xFF, 0xFF)
        C_LIGHT_BG  = RGBColor(0xF8, 0xFA, 0xFC)
        C_CARD_BG   = RGBColor(0xFF, 0xFF, 0xFF)
        C_TEXT      = RGBColor(0x1E, 0x29, 0x3B)
        C_SUBTLE    = RGBColor(0x64, 0x74, 0x8B)
        C_GOLD      = RGBColor(0xD9, 0x77, 0x06)
        C_GREEN     = RGBColor(0x05, 0x96, 0x69)
        C_RED       = RGBColor(0xDC, 0x26, 0x26)
        C_BLUE      = RGBColor(0x3B, 0x82, 0xF6)
        C_ORANGE    = RGBColor(0xEA, 0x58, 0x0C)
        C_TEAL      = RGBColor(0x0D, 0x94, 0x8B)
        C_BORDER    = RGBColor(0xE2, 0xE8, 0xF0)
        C_ZEBRA     = RGBColor(0xF1, 0xF5, 0xF9)

        FONT = "Segoe UI"

        paleta_modulos = [C_BLUE, C_GREEN, C_ACCENT2, C_ORANGE, C_TEAL, C_ACCENT, C_GOLD]

        from datetime import date as date_type

        # ── Funciones helper para construir la presentación ──

        def _set_bg(slide, color):
            slide.background.fill.solid()
            slide.background.fill.fore_color.rgb = color

        def _no_line(shape):
            shape.line.fill.background()

        def _no_shadow(shape):
            try:
                shape.shadow.inherit = False
            except Exception:
                pass

        def _rounded_rect(slide, x, y, w, h, fill_color=C_CARD_BG, border_color=None, radius=0.12):
            shape = slide.shapes.add_shape(
                MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h)
            )
            shape.fill.solid()
            shape.fill.fore_color.rgb = fill_color
            if border_color:
                shape.line.color.rgb = border_color
                shape.line.width = Pt(0.75)
            else:
                _no_line(shape)
            try:
                shape.adjustments[0] = radius
            except Exception:
                pass
            _no_shadow(shape)
            return shape

        def _rect(slide, x, y, w, h, fill_color, border_color=None):
            shape = slide.shapes.add_shape(
                MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h)
            )
            shape.fill.solid()
            shape.fill.fore_color.rgb = fill_color
            if border_color:
                shape.line.color.rgb = border_color
                shape.line.width = Pt(0.75)
            else:
                _no_line(shape)
            _no_shadow(shape)
            return shape

        def _text_box(slide, x, y, w, h, text, size=13, color=C_TEXT, bold=False,
                      align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, word_wrap=True,
                      line_spacing=1.0, italic=False):
            tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
            tf = tb.text_frame
            tf.word_wrap = word_wrap
            tf.margin_left = 0
            tf.margin_right = 0
            tf.margin_top = 0
            tf.margin_bottom = 0
            tf.vertical_anchor = anchor
            p = tf.paragraphs[0]
            p.alignment = align
            p.line_spacing = line_spacing
            r = p.add_run()
            r.text = str(text)
            r.font.size = Pt(size)
            r.font.color.rgb = color
            r.font.bold = bold
            r.font.italic = italic
            r.font.name = FONT
            return tb, tf

        def _multi_text(slide, x, y, w, h, lines, size=13, color=C_TEXT, bold_first=False,
                        align=PP_ALIGN.LEFT, line_spacing=1.15):
            tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
            tf = tb.text_frame
            tf.word_wrap = True
            tf.margin_left = 0
            tf.margin_right = 0
            tf.margin_top = 0
            tf.margin_bottom = 0
            for i, txt in enumerate(lines):
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                p.alignment = align
                p.line_spacing = line_spacing
                r = p.add_run()
                r.text = str(txt)
                r.font.size = Pt(size)
                r.font.color.rgb = color
                r.font.bold = (bold_first and i == 0)
                r.font.name = FONT
            return tb, tf

        def _progress_bar(slide, x, y, w, pct, color):
            h = 0.26
            _rounded_rect(slide, x, y, w, h, RGBColor(0xE8, 0xEC, 0xF1), radius=0.5)
            pct_clamped = max(0.0, min(100.0, float(pct) if pct else 0.0)) / 100.0
            if pct_clamped > 0:
                fill_w = max(0.06, (w - 0.06) * pct_clamped)
                _rounded_rect(slide, x + 0.03, y + 0.03, fill_w, h - 0.06, color, radius=0.5)

        def _set_cell(cell, text, size=10, color=C_TEXT, bold=False, align=PP_ALIGN.LEFT,
                      fill=None, anchor=MSO_ANCHOR.MIDDLE):
            cell.text = ""
            tf = cell.text_frame
            tf.word_wrap = True
            cell.vertical_anchor = anchor
            cell.margin_left = Inches(0.09)
            cell.margin_right = Inches(0.09)
            cell.margin_top = Inches(0.05)
            cell.margin_bottom = Inches(0.05)
            if fill is not None:
                cell.fill.solid()
                cell.fill.fore_color.rgb = fill
            for i, ln in enumerate(str(text).split("\n")):
                p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
                p.alignment = align
                p.line_spacing = 1.0
                r = p.add_run()
                r.text = ln
                r.font.size = Pt(size)
                r.font.color.rgb = color
                r.font.bold = bold
                r.font.name = FONT

        def _add_table(slide, x, y, w, headers, rows, col_widths, header_fill=C_PRIMARY,
                       font_size=10, row_height=0.4, aligns=None):
            rows_count = len(rows) + 1
            cols = len(headers)
            tbl_shape = slide.shapes.add_table(
                rows_count, cols, Inches(x), Inches(y), Inches(w), Inches(row_height * rows_count)
            )
            tbl = tbl_shape.table
            tbl.first_row = False
            tbl.horz_banding = False
            total = sum(col_widths)
            for i, cw in enumerate(col_widths):
                tbl.columns[i].width = Inches(w * cw / total)
            for j, htxt in enumerate(headers):
                _set_cell(tbl.cell(0, j), htxt, size=font_size, color=C_WHITE, bold=True,
                          align=PP_ALIGN.LEFT, fill=header_fill)
            for i, row in enumerate(rows):
                fill = C_WHITE if i % 2 == 0 else C_ZEBRA
                for j, val in enumerate(row):
                    a = aligns[j] if aligns else PP_ALIGN.LEFT
                    _set_cell(tbl.cell(i + 1, j), val, size=font_size, fill=fill, align=a)
            tbl.rows[0].height = Inches(0.4)
            for i in range(1, rows_count):
                tbl.rows[i].height = Inches(row_height)
            return tbl

        def _add_header(slide, title, accent_color, subtitle=None, module_num=None):
            _rect(slide, 0, 0, 13.333, 1.25, C_BG_DARK)
            _rect(slide, 0, 1.25, 13.333, 0.05, accent_color)

            if module_num:
                badge = slide.shapes.add_shape(
                    MSO_SHAPE.OVAL, Inches(0.6), Inches(0.2), Inches(0.85), Inches(0.85)
                )
                badge.fill.solid(); badge.fill.fore_color.rgb = accent_color
                _no_line(badge); _no_shadow(badge)
                btf = badge.text_frame; btf.word_wrap = False
                btf.vertical_anchor = MSO_ANCHOR.MIDDLE
                btf.margin_left = 0; btf.margin_right = 0; btf.margin_top = 0; btf.margin_bottom = 0
                btf.paragraphs[0].alignment = PP_ALIGN.CENTER
                br = btf.paragraphs[0].add_run(); br.text = str(module_num)
                br.font.size = Pt(20); br.font.color.rgb = C_WHITE; br.font.bold = True
                br.font.name = FONT

            title_x = 1.7 if module_num else 0.8
            _text_box(slide, title_x, 0.16, 11.2, 0.6, title, 22, C_WHITE, True,
                      anchor=MSO_ANCHOR.MIDDLE)
            if subtitle:
                _text_box(slide, title_x, 0.78, 11.2, 0.35, subtitle, 11, C_SUBTLE)

        def _add_kpi(slide, x, y, w, value, label, color):
            _rounded_rect(slide, x, y, w, 1.4, C_CARD_BG, C_BORDER)
            _rect(slide, x + 0.15, y + 0.08, w - 0.3, 0.05, color)
            _text_box(slide, x + 0.2, y + 0.3, w - 0.4, 0.55, str(value), 26, color, True,
                      PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
            _text_box(slide, x + 0.2, y + 0.9, w - 0.4, 0.4, label, 10, C_SUBTLE, False,
                      PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)

        def _workshop_card(slide, x, y, w, taller, ejecutadas, completadas, eficacia, color):
            try:
                ef_num = float(eficacia)
            except (TypeError, ValueError):
                ef_num = 0.0
            _rounded_rect(slide, x, y, w, 1.5, C_CARD_BG, C_BORDER)
            _rect(slide, x, y + 0.08, 0.06, 1.34, color)
            _text_box(slide, x + 0.25, y + 0.12, w - 0.5, 0.4, taller, 14, C_TEXT, True,
                      anchor=MSO_ANCHOR.MIDDLE)
            _text_box(slide, x + 0.25, y + 0.55, w - 0.5, 0.3,
                      f"{ejecutadas} ejecutadas  ·  {completadas} completadas", 10, C_SUBTLE)
            _progress_bar(slide, x + 0.25, y + 0.95, w - 0.5, ef_num, color)
            _text_box(slide, x + w - 0.95, y + 0.9, 0.7, 0.32, f"{ef_num:.0f}%",
                      10, color, True, PP_ALIGN.RIGHT, MSO_ANCHOR.MIDDLE)

        def _section_title(slide, x, y, w, text, color=C_PRIMARY, size=14):
            _text_box(slide, x, y, w, 0.35, text, size, color, True, anchor=MSO_ANCHOR.MIDDLE)

        def _rich_text(slide, x, y, w, h, runs, size=11, align=PP_ALIGN.LEFT,
                       anchor=MSO_ANCHOR.TOP, line_spacing=1.05):
            tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
            tf = tb.text_frame
            tf.word_wrap = True
            tf.margin_left = 0; tf.margin_right = 0; tf.margin_top = 0; tf.margin_bottom = 0
            tf.vertical_anchor = anchor
            p = tf.paragraphs[0]
            p.alignment = align
            p.line_spacing = line_spacing
            for (txt, bold, color) in runs:
                r = p.add_run()
                r.text = txt
                r.font.size = Pt(size)
                r.font.bold = bold
                r.font.color.rgb = color
                r.font.name = FONT
            return tb, tf

        def _bars(slide, x, y, w, items, value_suffix=""):
            if not items:
                return
            max_v = 0.0
            for it in items:
                try:
                    max_v = max(max_v, float(it[1]))
                except (TypeError, ValueError):
                    pass
            cy = y
            for it in items:
                label, v = it[0], it[1]
                clr = it[2] if len(it) > 2 else C_BLUE
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    fv = 0.0
                pct = (fv / max_v * 100) if max_v > 0 else 0
                _text_box(slide, x, cy, 2.0, 0.3, str(label)[:16], 11, C_TEXT, True,
                          anchor=MSO_ANCHOR.MIDDLE)
                _progress_bar(slide, x + 2.05, cy + 0.03, w - 3.15, pct, clr)
                _text_box(slide, x + w - 1.05, cy, 1.0, 0.3, f"{v}{value_suffix}", 11, C_TEXT, True,
                          PP_ALIGN.RIGHT, MSO_ANCHOR.MIDDLE)
                cy += 0.5

        def _add_list_slide(title, subtitle, accent, items, module_num):
            s = prs.slides.add_slide(prs.slide_layouts[6])
            _set_bg(s, C_LIGHT_BG)
            _add_header(s, title, accent, subtitle, module_num)
            cy = 1.7
            max_items = 8
            for i, it in enumerate(items[:max_items]):
                txt = str(it).strip()
                if not txt:
                    continue
                card_h = 0.58
                _rounded_rect(s, 0.6, cy, 12.1, card_h, C_CARD_BG, C_BORDER)
                _rect(s, 0.6, cy + 0.06, 0.06, card_h - 0.12, accent)
                _text_box(s, 0.85, cy, 0.5, card_h, str(i + 1), 12, accent, True,
                          PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
                _text_box(s, 1.45, cy, 11.0, card_h, txt[:260], 12, C_TEXT, False,
                          PP_ALIGN.LEFT, MSO_ANCHOR.MIDDLE, True, 1.05)
                cy += card_h + 0.12
            return s

        def _add_picture_fitted(slide, data_bytes, x, y, max_w, max_h):
            try:
                from PIL import Image as PILImage
                with PILImage.open(BytesIO(data_bytes)) as im:
                    iw, ih = im.size
            except Exception:
                iw, ih = 4, 3
            if not iw or not ih:
                iw, ih = 4, 3
            ratio = iw / ih
            w = max_w
            h = w / ratio
            if h > max_h:
                h = max_h
                w = h * ratio
            px = x + (max_w - w) / 2
            py = y + (max_h - h) / 2
            return slide.shapes.add_picture(
                BytesIO(data_bytes), Inches(px), Inches(py), width=Inches(w), height=Inches(h)
            )

        # ═══════════════════════════════
        # S1 — PORTADA (título, metadatos y total de actividades)
        # ═══════════════════════════════
        s1 = prs.slides.add_slide(prs.slide_layouts[6])
        _set_bg(s1, C_BG_DARK)
        _rect(s1, 0, 0, 13.333, 0.08, C_ACCENT)
        _rect(s1, 0, 7.42, 13.333, 0.08, C_ACCENT)

        for i, c in enumerate([C_ACCENT, C_ACCENT2, C_BLUE, C_GREEN]):
            o = s1.shapes.add_shape(MSO_SHAPE.OVAL, Inches(1.0 + i*0.35), Inches(1.7), Inches(0.09), Inches(0.09))
            o.fill.solid(); o.fill.fore_color.rgb = c; _no_line(o); _no_shadow(o)

        logo_shape = s1.shapes.add_shape(
            MSO_SHAPE.ROUNDED_RECTANGLE, Inches(1.0), Inches(2.2), Inches(0.8), Inches(0.8)
        )
        logo_shape.fill.solid(); logo_shape.fill.fore_color.rgb = C_ACCENT
        _no_line(logo_shape); _no_shadow(logo_shape)
        lt = logo_shape.text_frame; lt.word_wrap = False
        lt.vertical_anchor = MSO_ANCHOR.MIDDLE
        lt.margin_left = 0; lt.margin_right = 0; lt.margin_top = 0; lt.margin_bottom = 0
        lt.paragraphs[0].alignment = PP_ALIGN.CENTER
        lr = lt.paragraphs[0].add_run(); lr.text = "AI"
        lr.font.size = Pt(24); lr.font.color.rgb = C_WHITE; lr.font.bold = True; lr.font.name = FONT

        _text_box(s1, 2.0, 2.15, 9.3, 0.4, "REPORTE DE ANÁLISIS DE GUARDIA", 14, C_ACCENT, True)
        _text_box(s1, 2.0, 2.55, 9.3, 0.9, informe["titulo"] or "Informe sin título", 30, C_WHITE, True,
                  anchor=MSO_ANCHOR.MIDDLE)
        _rect(s1, 2.0, 3.55, 2.2, 0.04, C_ACCENT)

        fecha = informe.get("fecha_guardia")
        fecha_str = fecha.strftime("%d/%m/%Y") if fecha else "N/A"
        meta_lines = [
            ("Fecha de Guardia", fecha_str),
            ("Fiscalizador", informe.get("fiscalizador") or "N/A"),
            ("Documento", f"{informe.get('nombre_archivo', '')}  ({str(informe.get('tipo_archivo', '')).upper()})"),
        ]
        my = 3.85
        for lbl, val in meta_lines:
            _text_box(s1, 2.0, my, 2.4, 0.35, lbl.upper(), 11, C_SUBTLE, True)
            _text_box(s1, 4.5, my, 7.0, 0.35, val, 13, C_WHITE, False)
            my += 0.48

        mod1s = data.get("modulo1_resumen_ejecutivo") or {}
        big_num = mod1s.get("total_actividades", "—")
        _text_box(s1, 10.3, 2.6, 2.4, 0.9, str(big_num), 48, C_ACCENT, True, PP_ALIGN.CENTER,
                  MSO_ANCHOR.MIDDLE)
        _text_box(s1, 10.3, 3.5, 2.4, 0.7, "ACTIVIDADES\nEJECUTADAS", 11, C_SUBTLE, False,
                  PP_ALIGN.CENTER, MSO_ANCHOR.TOP)

        _rect(s1, 0, 6.45, 13.333, 0.97, C_PRIMARY)
        _text_box(s1, 1.0, 6.62, 11.333, 0.35,
                  f"SIGGO — Integrated Management System for Guards and Operations   |   {date_type.today().strftime('%d/%m/%Y')}",
                  11, C_SUBTLE, False, PP_ALIGN.CENTER)
        _text_box(s1, 1.0, 7.0, 11.333, 0.3, "Análisis potenciado por Inteligencia Artificial",
                  10, C_SUBTLE, False, PP_ALIGN.CENTER)

        # ═══════════════════════════════
        # S2 — MÓDULO 1: RESUMEN EJECUTIVO (KPIs principales y resumen textual)
        # ═══════════════════════════════
        mod1 = data.get("modulo1_resumen_ejecutivo") or {}
        s2 = prs.slides.add_slide(prs.slide_layouts[6])
        _set_bg(s2, C_LIGHT_BG)
        _add_header(s2, "Resumen Ejecutivo de Guardia", C_ACCENT, "MÓDULO 1 — Indicadores consolidados", 1)

        kpis_config = [
            (mod1.get("total_actividades", "—"), "Total Actividades\nEjecutadas", C_BLUE),
            (mod1.get("total_produccion_recuperada_bopd", "—"), "Prod. Recuperada\nCNP (BOPD)", C_GREEN),
            (mod1.get("total_trabajos_criticos", "—"), "Trabajos\nCríticos", C_GOLD),
            (mod1.get("eficacia_general", "—"), "Eficacia\nGeneral", C_ACCENT2),
        ]
        for i, (val, lbl, clr) in enumerate(kpis_config):
            _add_kpi(s2, 0.6 + i*3.15, 1.7, 2.95, val, lbl, clr)

        resumen = (data.get("resumen") or "No se generó resumen.").strip()
        resumen = re.sub(r"[ \t]*\n{3,}", "\n\n", resumen)
        _rounded_rect(s2, 0.6, 3.4, 12.1, 3.8, C_CARD_BG, C_BORDER)
        _rect(s2, 0.6, 3.4, 0.06, 3.8, C_ACCENT)
        _text_box(s2, 0.9, 3.55, 1.6, 0.35, "RESUMEN", 11, C_ACCENT, True)
        _text_box(s2, 0.9, 3.95, 11.5, 3.1, resumen[:1400], 12, C_TEXT, False,
                  PP_ALIGN.LEFT, MSO_ANCHOR.TOP, True, 1.15)

        # ═══════════════════════════════
        # S3 — MÓDULO 2: EFICACIA POR TALLER (tarjetas con barra de progreso)
        # ═══════════════════════════════
        eficacia = data.get("modulo2_eficacia_taller") or []
        if eficacia:
            s3 = prs.slides.add_slide(prs.slide_layouts[6])
            _set_bg(s3, C_LIGHT_BG)
            _add_header(s3, "Control de Actividades y Eficacia por Taller", C_BLUE, "MÓDULO 2 — Desempeño operativo por especialidad", 2)

            cols = 3 if len(eficacia) <= 9 else 4
            gap = 0.35
            margin_x = 0.6
            total_w = 13.333 - 2 * margin_x
            card_w = (total_w - (cols - 1) * gap) / cols
            card_h = 1.5
            row_gap = 0.35
            max_rows = 3
            max_items = cols * max_rows

            for i, row in enumerate(eficacia[:max_items]):
                col = i % cols
                fila = i // cols
                cx = margin_x + col * (card_w + gap)
                cy = 1.7 + fila * (card_h + row_gap)
                taller_name = row.get("taller", f"Taller {i+1}")
                color = paleta_modulos[i % len(paleta_modulos)]
                ej = row.get("ejecutadas", 0)
                comp = row.get("completadas", 0)
                ef = row.get("eficacia", 0)
                _workshop_card(s3, cx, cy, card_w, taller_name, ej, comp, ef, color)

            if len(eficacia) > max_items:
                _text_box(s3, 0.6, 7.0, 12.1, 0.3,
                          f"Mostrando {max_items} de {len(eficacia)} talleres.", 11, C_SUBTLE, False,
                          PP_ALIGN.CENTER)

        # ═══════════════════════════════
        # S4 — MÓDULO 3: MATRIZ DE TRABAJOS IMPORTANTES (críticos / amarillos)
        # ═══════════════════════════════
        importantes = data.get("modulo3_trabajos_importantes") or []
        if importantes:
            def _render_importantes(slide, chunk, start_idx):
                cy = 1.95
                for k, r in enumerate(chunk):
                    taller = r.get("taller", "") or ""
                    pozo = r.get("pozo", "") or ""
                    falla = r.get("falla", "") or ""
                    solucion = r.get("solucion", "") or ""
                    card_h = 1.15
                    _rounded_rect(slide, 0.6, cy, 12.1, card_h, C_CARD_BG, C_BORDER)
                    _rect(slide, 0.6, cy + 0.06, 0.06, card_h - 0.12, C_GOLD)

                    num = slide.shapes.add_shape(
                        MSO_SHAPE.OVAL, Inches(0.95), Inches(cy + 0.14), Inches(0.42), Inches(0.42)
                    )
                    num.fill.solid(); num.fill.fore_color.rgb = C_GOLD
                    _no_line(num); _no_shadow(num)
                    ntf = num.text_frame; ntf.word_wrap = False
                    ntf.vertical_anchor = MSO_ANCHOR.MIDDLE
                    ntf.margin_left = 0; ntf.margin_right = 0; ntf.margin_top = 0; ntf.margin_bottom = 0
                    ntf.paragraphs[0].alignment = PP_ALIGN.CENTER
                    nr = ntf.paragraphs[0].add_run(); nr.text = str(start_idx + k + 1)
                    nr.font.size = Pt(12); nr.font.color.rgb = C_WHITE; nr.font.bold = True; nr.font.name = FONT

                    enc = taller + (f"  ·  Pozo {pozo}" if pozo else "")
                    _text_box(slide, 1.6, cy + 0.08, 11.0, 0.32, enc, 14, C_GOLD, True,
                              anchor=MSO_ANCHOR.MIDDLE)
                    _rich_text(slide, 1.6, cy + 0.42, 11.0, 0.32,
                               [("Falla:  ", True, C_SUBTLE), (falla[:100], False, C_TEXT)])
                    _rich_text(slide, 1.6, cy + 0.76, 11.0, 0.32,
                               [("Solución:  ", True, C_GREEN), (solucion[:100], False, C_TEXT)])
                    cy += card_h + 0.12

            s4 = prs.slides.add_slide(prs.slide_layouts[6])
            _set_bg(s4, C_LIGHT_BG)
            _add_header(s4, "Trabajos Importantes", C_GOLD, "MÓDULO 3 — Trabajos críticos / celdas amarillas", 3)

            _rounded_rect(s4, 0.6, 1.55, 12.1, 0.45, RGBColor(0xFF, 0xFB, 0xEB), C_GOLD)
            _text_box(s4, 0.9, 1.58, 11.5, 0.4,
                      f"⚠  {len(importantes)} trabajos críticos identificados que requieren atención prioritaria",
                      13, C_GOLD, True, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)

            per_page = 4
            _render_importantes(s4, importantes[:per_page], 0)
            for pg in range(per_page, len(importantes), per_page):
                s4b = prs.slides.add_slide(prs.slide_layouts[6])
                _set_bg(s4b, C_LIGHT_BG)
                _add_header(s4b, "Trabajos Importantes (cont.)", C_GOLD, None, 3)
                _render_importantes(s4b, importantes[pg:pg + per_page], pg)

        # ═══════════════════════════════
        # S5 — MÓDULO 4: RESUMEN OPERATIVO (producción recuperada y clasificación)
        # ═══════════════════════════════
        ops = data.get("modulo4_resumen_operativo") or {}
        bopd_data = ops.get("bopd_por_taller") or {}
        tipos_data = ops.get("trabajos_por_tipo") or {}
        if bopd_data or tipos_data:
            s5 = prs.slides.add_slide(prs.slide_layouts[6])
            _set_bg(s5, C_LIGHT_BG)
            _add_header(s5, "Resumen Operativo", C_GREEN, "MÓDULO 4 — Producción recuperada y clasificación de trabajos", 4)

            type_colors = {"CNP": C_GREEN, "MC": C_GOLD, "PV": C_BLUE, "SOP": C_ACCENT,
                           "OP": C_ACCENT2, "SUS": C_RED}

            if bopd_data and tipos_data:
                total_bopd = sum(float(v) for v in bopd_data.values() if v)
                _section_title(s5, 0.6, 1.65, 5.9, "PRODUCCIÓN RECUPERADA POR TALLER (BOPD)", C_GREEN, 12)
                _add_kpi(s5, 0.6, 2.1, 5.9, f"{total_bopd:.1f}", "Total CNP Recuperado (BOPD)", C_GREEN)
                _bars(s5, 0.6, 3.85, 5.9, [(k, v, C_GREEN) for k, v in bopd_data.items()], " BOPD")

                _section_title(s5, 7.0, 1.65, 5.7, "TRABAJOS POR TIPO", C_BLUE, 12)
                _bars(s5, 7.0, 2.35, 5.7,
                      [(t, c, type_colors.get(str(t).upper(), C_ACCENT)) for t, c in tipos_data.items()])

            elif bopd_data:
                total_bopd = sum(float(v) for v in bopd_data.values() if v)
                _section_title(s5, 3.7, 1.65, 5.9, "PRODUCCIÓN RECUPERADA POR TALLER (BOPD)", C_GREEN, 12)
                _add_kpi(s5, 3.7, 2.1, 5.9, f"{total_bopd:.1f}", "Total CNP Recuperado (BOPD)", C_GREEN)
                _bars(s5, 3.7, 3.85, 5.9, [(k, v, C_GREEN) for k, v in bopd_data.items()], " BOPD")

            else:
                _section_title(s5, 3.7, 1.65, 5.9, "TRABAJOS POR TIPO", C_BLUE, 12)
                _bars(s5, 3.7, 2.35, 5.9,
                      [(t, c, type_colors.get(str(t).upper(), C_ACCENT)) for t, c in tipos_data.items()])

        # ═══════════════════════════════
        # S6 — VISUALIZACIÓN DE KPIs POR GRÁFICAS (barras horizontales)
        # ═══════════════════════════════
        graficas = data.get("graficas") or []
        if graficas:
            s6 = prs.slides.add_slide(prs.slide_layouts[6])
            _set_bg(s6, C_LIGHT_BG)
            _add_header(s6, "Visualización de Datos", C_ACCENT2, "KPIs del análisis operativo", None)

            cy_cursor = 1.7
            for gidx, g in enumerate(graficas):
                titulo = g.get("titulo", f"Gráfica {gidx+1}")
                labels = g.get("labels", [])
                datasets = g.get("datasets", [])
                if not datasets or not labels:
                    continue

                ds = datasets[0]
                valores = ds.get("data", [])
                if not valores:
                    continue
                paleta = ds.get("backgroundColor", []) or [
                    "#3b82f6", "#22c55e", "#f59e0b", "#8b5cf6", "#ef4444", "#06b6d4", "#f97316", "#64748b"
                ]
                max_val = max(float(v) for v in valores) if any(valores) else 0

                block_h = 0.45 + len(labels) * 0.4
                if cy_cursor + block_h > 7.1:
                    s6 = prs.slides.add_slide(prs.slide_layouts[6])
                    _set_bg(s6, C_LIGHT_BG)
                    _add_header(s6, "Visualización de Datos (cont.)", C_ACCENT2, None, None)
                    cy_cursor = 1.6

                _section_title(s6, 0.8, cy_cursor, 12.0, titulo, C_PRIMARY, 14)
                cy_cursor += 0.45

                for li, (label, val) in enumerate(zip(labels, valores)):
                    try:
                        fval = float(val)
                    except (TypeError, ValueError):
                        fval = 0.0
                    pct = (fval / max_val * 100) if max_val > 0 else 0
                    bar_color = RGBColor(
                        int(paleta[li % len(paleta)][1:3], 16),
                        int(paleta[li % len(paleta)][3:5], 16),
                        int(paleta[li % len(paleta)][5:7], 16)
                    )
                    _text_box(s6, 0.8, cy_cursor, 2.6, 0.28, str(label)[:22], 10, C_TEXT, True,
                              anchor=MSO_ANCHOR.MIDDLE)
                    _progress_bar(s6, 3.5, cy_cursor + 0.02, 8.2, pct, bar_color)
                    _text_box(s6, 11.85, cy_cursor, 0.9, 0.28, str(val), 10, C_TEXT, True,
                              PP_ALIGN.RIGHT, MSO_ANCHOR.MIDDLE)
                    cy_cursor += 0.4
                cy_cursor += 0.15

        # ═══════════════════════════════
        # S7 — MÓDULO 5: PENDIENTES (tareas en espera con punto de estado)
        # ═══════════════════════════════
        pendientes = data.get("modulo5_pendientes") or []
        if pendientes:
            def _render_pendientes(slide, chunk, start_idx):
                cy = 2.15
                for k, r in enumerate(chunk):
                    taller = r.get("taller", "") or ""
                    pozo = r.get("pozo", "") or ""
                    req = r.get("requerimiento", "") or ""
                    estado = r.get("estado", "Pendiente") or "Pendiente"
                    es_proceso = str(estado).lower() in ("pendiente", "en proceso", "en_proceso")
                    dot_color = C_GOLD if es_proceso else C_RED
                    _rounded_rect(slide, 0.6, cy, 12.1, 0.62, C_CARD_BG, C_BORDER)
                    dot = slide.shapes.add_shape(
                        MSO_SHAPE.OVAL, Inches(0.9), Inches(cy + 0.2), Inches(0.22), Inches(0.22)
                    )
                    dot.fill.solid(); dot.fill.fore_color.rgb = dot_color
                    _no_line(dot); _no_shadow(dot)
                    enc = taller + (f"  ·  Pozo {pozo}" if pozo else "")
                    _text_box(slide, 1.35, cy + 0.04, 2.7, 0.28, enc, 11, C_TEXT, True,
                              anchor=MSO_ANCHOR.MIDDLE)
                    _text_box(slide, 4.1, cy + 0.04, 6.3, 0.55, req[:120], 10, C_SUBTLE, False,
                              anchor=MSO_ANCHOR.MIDDLE)
                    _text_box(slide, 10.5, cy + 0.04, 2.0, 0.55, estado.upper(), 11, dot_color, True,
                              PP_ALIGN.RIGHT, MSO_ANCHOR.MIDDLE)
                    cy += 0.7

            s7 = prs.slides.add_slide(prs.slide_layouts[6])
            _set_bg(s7, C_LIGHT_BG)
            _add_header(s7, "Reporte de Actividades Pendientes", C_RED, "MÓDULO 5 — Tareas en espera registradas", 5)

            _rounded_rect(s7, 0.6, 1.55, 12.1, 0.45, RGBColor(0xFE, 0xF2, 0xF2), C_RED)
            _text_box(s7, 0.9, 1.58, 11.5, 0.4,
                      f"⚠  {len(pendientes)} actividades pendientes que requieren seguimiento inmediato",
                      13, C_RED, True, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)

            per_page = 7
            _render_pendientes(s7, pendientes[:per_page], 0)
            for pg in range(per_page, len(pendientes), per_page):
                s7b = prs.slides.add_slide(prs.slide_layouts[6])
                _set_bg(s7b, C_LIGHT_BG)
                _add_header(s7b, "Reporte de Actividades Pendientes (cont.)", C_RED, None, 5)
                _render_pendientes(s7b, pendientes[pg:pg + per_page], pg)

        # ═══════════════════════════════
        # S8 — HALLAZGOS CLAVE (aspectos relevantes del análisis)
        # ═══════════════════════════════
        hallazgos = data.get("hallazgos") or []
        if hallazgos:
            s8 = prs.slides.add_slide(prs.slide_layouts[6])
            _set_bg(s8, C_LIGHT_BG)
            _add_header(s8, "Hallazgos Clave", C_GOLD, "Aspectos relevantes identificados en el análisis", 6)

            cy = 1.65
            for i, h in enumerate(hallazgos[:6]):
                card_h = 0.72
                _rounded_rect(s8, 0.6, cy, 12.1, card_h, C_CARD_BG, C_BORDER)
                _rect(s8, 0.6, cy + 0.06, 0.06, card_h - 0.12, C_GOLD)

                num = s8.shapes.add_shape(
                    MSO_SHAPE.OVAL, Inches(0.95), Inches(cy + (card_h - 0.42) / 2), Inches(0.42), Inches(0.42)
                )
                num.fill.solid(); num.fill.fore_color.rgb = C_GOLD
                _no_line(num); _no_shadow(num)
                ntf = num.text_frame; ntf.word_wrap = False
                ntf.vertical_anchor = MSO_ANCHOR.MIDDLE
                ntf.margin_left = 0; ntf.margin_right = 0; ntf.margin_top = 0; ntf.margin_bottom = 0
                ntf.paragraphs[0].alignment = PP_ALIGN.CENTER
                nr = ntf.paragraphs[0].add_run(); nr.text = str(i + 1)
                nr.font.size = Pt(12); nr.font.color.rgb = C_WHITE; nr.font.bold = True; nr.font.name = FONT

                _text_box(s8, 1.6, cy, 10.9, card_h, str(h)[:280], 12, C_TEXT, False,
                          PP_ALIGN.LEFT, MSO_ANCHOR.MIDDLE, True, 1.1)
                cy += card_h + 0.16

        # ═══════════════════════════════
        # S9 — RECOMENDACIONES (acciones sugeridas)
        # ═══════════════════════════════
        recomendaciones = data.get("recomendaciones") or []
        if recomendaciones:
            s9 = prs.slides.add_slide(prs.slide_layouts[6])
            _set_bg(s9, C_LIGHT_BG)
            _add_header(s9, "Recomendaciones", C_GREEN, "Acciones sugeridas con base en los resultados", 7)

            cy = 1.65
            for i, rec in enumerate(recomendaciones[:6]):
                card_h = 0.72
                _rounded_rect(s9, 0.6, cy, 12.1, card_h, C_CARD_BG, C_BORDER)
                _rect(s9, 0.6, cy + 0.06, 0.06, card_h - 0.12, C_GREEN)

                num = s9.shapes.add_shape(
                    MSO_SHAPE.OVAL, Inches(0.95), Inches(cy + (card_h - 0.42) / 2), Inches(0.42), Inches(0.42)
                )
                num.fill.solid(); num.fill.fore_color.rgb = C_GREEN
                _no_line(num); _no_shadow(num)
                ntf = num.text_frame; ntf.word_wrap = False
                ntf.vertical_anchor = MSO_ANCHOR.MIDDLE
                ntf.margin_left = 0; ntf.margin_right = 0; ntf.margin_top = 0; ntf.margin_bottom = 0
                ntf.paragraphs[0].alignment = PP_ALIGN.CENTER
                nr = ntf.paragraphs[0].add_run(); nr.text = str(i + 1)
                nr.font.size = Pt(12); nr.font.color.rgb = C_WHITE; nr.font.bold = True; nr.font.name = FONT

                _text_box(s9, 1.6, cy, 10.9, card_h, str(rec)[:280], 12, C_TEXT, False,
                          PP_ALIGN.LEFT, MSO_ANCHOR.MIDDLE, True, 1.1)
                cy += card_h + 0.16

        # ═══════════════════════════════
        # S10 — ANÁLISIS DE IMÁGENES Y ELEMENTOS VISUALES (imágenes incrustadas)
        # ═══════════════════════════════
        imagenes_desc = data.get("analisis_imagenes") or []
        if imagenes_desc and isinstance(imagenes_desc[0], dict):
            desc_list = imagenes_desc
        else:
            desc_list = [{"descripcion": str(x)} for x in imagenes_desc]

        if imagenes_archivo or desc_list:
            total = max(len(imagenes_archivo), len(desc_list))

            def _render_imagen(slide, idx, x, y):
                d = desc_list[idx] if idx < len(desc_list) else {}
                referencia = d.get("referencia", "") or f"Imagen {idx + 1}"
                descripcion = d.get("descripcion", "") or ""
                _text_box(slide, x, y, 5.6, 0.32, str(referencia), 12, C_ACCENT, True,
                          anchor=MSO_ANCHOR.MIDDLE)
                if idx < len(imagenes_archivo):
                    try:
                        _add_picture_fitted(slide, imagenes_archivo[idx]["bytes"], x, y + 0.4, 5.6, 3.9)
                    except Exception:
                        _rounded_rect(slide, x, y + 0.4, 5.6, 3.9, C_LIGHT_BG, C_BORDER)
                else:
                    _rounded_rect(slide, x, y + 0.4, 5.6, 3.9, C_LIGHT_BG, C_BORDER)
                _text_box(slide, x, y + 4.4, 5.6, 1.2, str(descripcion)[:280], 11, C_TEXT, False,
                          anchor=MSO_ANCHOR.TOP, line_spacing=1.05)

            s_img = prs.slides.add_slide(prs.slide_layouts[6])
            _set_bg(s_img, C_LIGHT_BG)
            _add_header(s_img, "Análisis de Imágenes y Elementos Visuales", C_ACCENT,
                        "Fotografías y evidencias visuales contenidas en el documento", 8)
            _render_imagen(s_img, 0, 0.8, 1.6)
            if total > 1:
                _render_imagen(s_img, 1, 7.0, 1.6)

            for base in range(2, total, 2):
                s_img2 = prs.slides.add_slide(prs.slide_layouts[6])
                _set_bg(s_img2, C_LIGHT_BG)
                _add_header(s_img2, "Análisis de Imágenes y Elementos Visuales (cont.)", C_ACCENT,
                            None, 8)
                _render_imagen(s_img2, base, 0.8, 1.6)
                if base + 1 < total:
                    _render_imagen(s_img2, base + 1, 7.0, 1.6)

        # ═══════════════════════════════
        # S9 — DIAPOSITIVA DE CIERRE
        # ═══════════════════════════════
        s_end = prs.slides.add_slide(prs.slide_layouts[6])
        _set_bg(s_end, C_BG_DARK)
        _rect(s_end, 0, 0, 13.333, 0.08, C_ACCENT)
        _rect(s_end, 0, 7.42, 13.333, 0.08, C_ACCENT)
        _rect(s_end, 5.55, 2.8, 2.2, 0.04, C_ACCENT)

        _text_box(s_end, 1.5, 3.1, 10.3, 1.0, "FIN DEL REPORTE", 44, C_WHITE, True,
                  PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
        _text_box(s_end, 1.5, 4.1, 10.3, 0.5,
                  "Análisis generado automáticamente con Inteligencia Artificial",
                  14, C_SUBTLE, False, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE)
        _text_box(s_end, 1.5, 4.8, 10.333, 0.5,
                  "SIGGO — Integrated Management System for Guards and Operations",
                  13, C_SUBTLE, False, PP_ALIGN.CENTER, MSO_ANCHOR.MIDDLE, True, 1.0, True)

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
