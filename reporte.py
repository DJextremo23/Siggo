from flask import Blueprint, render_template, request, send_file, session, redirect, url_for
from io import BytesIO
from datetime import date, datetime
from urllib.parse import urlencode
from conexion import conexion
from utils import acceso_no_autorizado
from openpyxl import Workbook
from openpyxl.styles import Alignment
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, HRFlowable
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from estilos_reporte import (
    COLOR_ACENTO,
    estilos_pdf, build_pdf_tabla,
    configurar_encabezado_excel, aplicar_estilo_datos_excel
)

# Blueprint para reportes del admin - resumen, detalle y vacaciones con exportación a PDF y Excel
reporte_bp = Blueprint("reporte_bp", __name__)


# Estado de compensación considerando si la fecha ya se disfrutó (compensado)
# o si está pendiente/futura (pendiente).
ESTADO_COMPENSACION_SQL = """
CASE
    WHEN a.estado = 'falta' THEN '❌ No cumple'
    WHEN a.estado = 'justificado' THEN '🟡 Justificado'
    WHEN a.estado = 'asistio' AND c.id_compensacion IS NULL THEN '⚠️ Pendiente'
    WHEN a.estado = 'asistio' AND c.fecha_compensacion > CURDATE() THEN '⏳ Pendiente'
    WHEN a.estado = 'asistio' THEN '✔ Compensado'
    ELSE '—'
END
"""


def _fecha(valor):
    """Formatea una fecha como día.mes.año (DD.MM.YYYY)."""
    if valor is None or valor == "":
        return ""
    if isinstance(valor, datetime):
        return valor.strftime("%d.%m.%Y")
    if isinstance(valor, date):
        return valor.strftime("%d.%m.%Y")
    s = str(valor).strip()
    if not s:
        return ""
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).strftime("%d.%m.%Y")
        except ValueError:
            continue
    return s


# ==========================
# UTILIDADES
# ==========================
# Filtro genérico de texto sobre cualquier campo de una lista de diccionarios
def filtrar_por_texto(data, texto):
    """Filtra una lista de diccionarios buscando el texto en cualquier campo (case-insensitive)."""
    if not texto:
        return data
    texto_lower = texto.lower()
    return [row for row in data if texto_lower in ' '.join(str(v) for v in row.values()).lower()]


# ==========================
# UTILIDAD (FILTRO BASE)
# ==========================
# Construye la cláusula WHERE y los parámetros para filtrar guardias por fecha y usuarios
def _filtro_fechas(anio=None, fecha_desde=None, fecha_hasta=None, alias_fecha="g.fecha_guardia"):
    """Cláusula 'AND ...' y parámetros para filtrar por año o rango de fechas."""
    clause = ""
    params = []

    if fecha_desde:
        clause += f" AND {alias_fecha} >= %s"
        params.append(fecha_desde)

    if fecha_hasta:
        clause += f" AND {alias_fecha} <= %s"
        params.append(fecha_hasta)

    if not fecha_desde and not fecha_hasta:
        if anio:
            clause += f" AND YEAR({alias_fecha}) = %s"
            params.append(int(anio))

    return clause, params


def construir_filtro(anio=None, fecha_desde=None, fecha_hasta=None, ids_usuarios=None, alias_fecha="g.fecha_guardia"):
    filtro = "WHERE 1=1"
    params = []

    clause, fechas_params = _filtro_fechas(anio, fecha_desde, fecha_hasta, alias_fecha)
    filtro += clause
    params.extend(fechas_params)

    if ids_usuarios:
        placeholders = ",".join(["%s"] * len(ids_usuarios))
        filtro += f" AND u.id_usuario IN ({placeholders})"
        params.extend(ids_usuarios)

    return filtro, params


# ==========================
# RESUMEN DE VACACIONES (ADMIN)
# ==========================
def _resumen_vacaciones_admin(cursor, anio=None, fecha_desde=None, fecha_hasta=None, ids_usuarios=None):
    """Resumen de vacaciones por fiscalizador respetando los filtros de año/rango.

    Los días tomados se atribuyen por período (FIFO) según el año filtrado:
    se descuentan primero de los períodos anteriores al año del filtro y
    luego del propio año filtrado. 'dias_tomados' refleja lo consumido del
    año filtrado; lo consumido de años anteriores se ve en
    'dias_pendientes_anteriores'.
    """
    hoy = date.today().strftime("%Y-%m-%d")
    periodo_inicio = fecha_desde or (f"{anio}-01-01" if anio else None)

    # Periodo totalmente en el futuro: aún no hay registros de vacaciones
    if periodo_inicio and periodo_inicio > hoy:
        return []

    # Año del filtro, para atribuir los días por período (FIFO)
    if anio:
        anio_filtro = int(anio)
    elif fecha_desde:
        anio_filtro = int(fecha_desde[:4])
    else:
        anio_filtro = date.today().year

    total_taken_sub = (
        "COALESCE((SELECT SUM(DATEDIFF(v3.fecha_fin, v3.fecha_inicio) + 1) "
        "FROM vacaciones v3 WHERE v3.id_usuario = u.id_usuario "
        "AND v3.fecha_inicio <= %s), 0)"
    )
    ent_antes = "GREATEST(0, (CAST(%s AS SIGNED) - CAST(YEAR(u.fecha_ingreso) AS SIGNED) - 1) * 30)"

    filtro_usuarios = ""
    params_usuarios = []
    if ids_usuarios:
        placeholders = ",".join(["%s"] * len(ids_usuarios))
        filtro_usuarios = f" AND u.id_usuario IN ({placeholders})"
        params_usuarios = list(ids_usuarios)

    sql = f"""
        SELECT
            CONCAT(u.nombre, ' ', u.apellidos) AS fiscalizador,
            u.foto,
            CASE WHEN %s <= YEAR(u.fecha_ingreso) THEN 0
                 ELSE LEAST(30, GREATEST(0, {total_taken_sub} - {ent_antes})) END AS dias_tomados,
            CASE WHEN %s <= YEAR(u.fecha_ingreso) THEN 0
                 ELSE GREATEST(0, 30 - LEAST(30, GREATEST(0, {total_taken_sub} - {ent_antes}))) END AS dias_pendientes,
            CASE WHEN %s <= YEAR(u.fecha_ingreso) THEN 0
                 ELSE GREATEST(0, {ent_antes} - {total_taken_sub}) END AS dias_pendientes_anteriores
        FROM usuarios u
        INNER JOIN usuarios_roles ur ON u.id_usuario = ur.id_usuario
        INNER JOIN roles r ON ur.id_rol = r.id_rol
        WHERE r.nombre_rol = 'fiscalizador'
          AND u.estado = 'activo'
        {filtro_usuarios}
        ORDER BY u.nombre, u.apellidos
    """

    params = (
        [anio_filtro, hoy, anio_filtro, anio_filtro, hoy, anio_filtro, anio_filtro, anio_filtro, hoy] +
        params_usuarios
    )

    cursor.execute(sql, params)
    return cursor.fetchall()


# ==========================
# REPORTE WEB
# ==========================
# Página principal de reportes: consulta resumen, detalle, vacaciones y resumen de vacaciones
@reporte_bp.route("/reportes")
def reporte():
    if "usuario" not in session:
        return redirect("/")
    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    anio = request.args.get("anio")
    fecha_desde = request.args.get("fecha_desde")
    fecha_hasta = request.args.get("fecha_hasta")
    ids_usuarios = request.args.getlist("id_usuario")
    buscar = request.args.get("buscar", "").strip()
    buscar_detalle = request.args.get("buscar_detalle", "").strip()
    buscar_vac = request.args.get("buscar_vac", "").strip()
    buscar_resumen_vac = request.args.get("buscar_resumen_vac", "").strip()

    # Por defecto, si no hay ningún filtro de fecha, se muestra el año actual
    if not anio and not fecha_desde and not fecha_hasta:
        args = request.args.to_dict(flat=False)
        args["anio"] = str(date.today().year)
        return redirect(url_for("reporte_bp.reporte") + "?" + urlencode(args, doseq=True))

    conn = None
    cursor = None
    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT u.id_usuario, u.nombre, u.apellidos
            FROM usuarios u
            INNER JOIN usuarios_roles ur ON u.id_usuario = ur.id_usuario
            INNER JOIN roles r ON ur.id_rol = r.id_rol
            WHERE r.nombre_rol = 'fiscalizador' AND u.estado = 'activo'
            ORDER BY u.nombre, u.apellidos
        """)
        usuarios = cursor.fetchall()

        filtro, params = construir_filtro(
            anio=anio,
            fecha_desde=fecha_desde, fecha_hasta=fecha_hasta,
            ids_usuarios=ids_usuarios
        )

        filtro_pend, params_pend = _filtro_fechas(anio, fecha_desde, fecha_hasta, "g2.fecha_guardia")

        # ==========================
        # RESUMEN
        # ==========================
        cursor.execute(f"""
            SELECT 
                u.id_usuario,
                u.foto,
                CONCAT(u.nombre,' ',u.apellidos) AS fiscalizador,
                COUNT(g.id_guardia) AS total_guardias,
                CAST(SUM(CASE WHEN f.id_feriado IS NOT NULL THEN 1 ELSE 0 END) AS UNSIGNED) AS guardias_feriado,
                SUM(CASE WHEN c.id_compensacion IS NOT NULL AND c.fecha_compensacion <= CURDATE() THEN 1 ELSE 0 END) AS compensaciones,
                (
                    SELECT COUNT(g2.id_guardia)
                    FROM guardias g2
                    LEFT JOIN asistencia a2 ON g2.id_guardia = a2.id_guardia
                    LEFT JOIN compensaciones c2 ON g2.id_guardia = c2.id_guardia
                    WHERE g2.id_usuario = u.id_usuario
                      AND a2.estado = 'asistio'
                      AND (c2.id_compensacion IS NULL OR c2.fecha_compensacion > CURDATE())
                      {filtro_pend}
                ) AS pendientes
            FROM guardias g
            LEFT JOIN usuarios u ON g.id_usuario = u.id_usuario
            LEFT JOIN feriados f ON g.id_feriado = f.id_feriado
            LEFT JOIN compensaciones c ON g.id_guardia = c.id_guardia
            {filtro}
            GROUP BY u.id_usuario
        """, params_pend + params)

        reporte_data = cursor.fetchall()

        # ==========================
        # DETALLE
        # ==========================
        cursor.execute(f"""
            SELECT 
                CONCAT(u.nombre,' ',u.apellidos) AS fiscalizador,
                g.fecha_guardia,
                g.tipo,
                COALESCE(f.descripcion, '—') AS feriado_descripcion,
                CASE 
                    WHEN a.estado = 'asistio' THEN '✔ Asistió'
                    WHEN a.estado = 'falta' THEN '❌ Falta'
                    WHEN a.estado = 'justificado' THEN '🟡 Justificado'
                    ELSE '—'
                END AS asistencia,
                c.fecha_compensacion,
                c.observacion,
                {ESTADO_COMPENSACION_SQL} AS estado_compensacion
            FROM guardias g
            LEFT JOIN usuarios u ON g.id_usuario = u.id_usuario
            LEFT JOIN asistencia a ON g.id_guardia = a.id_guardia
            LEFT JOIN compensaciones c ON g.id_guardia = c.id_guardia
            LEFT JOIN feriados f ON g.id_feriado = f.id_feriado
            {filtro}
            ORDER BY g.fecha_guardia DESC
        """, params)

        detalle = cursor.fetchall()

        # ==========================
        # VACACIONES
        # ==========================
        filtro_vac = "WHERE 1=1"
        params_vac = []

        if fecha_desde:
            filtro_vac += " AND v.fecha_fin >= %s"
            params_vac.append(fecha_desde)

        if fecha_hasta:
            filtro_vac += " AND v.fecha_inicio <= %s"
            params_vac.append(fecha_hasta)

        if not fecha_desde and not fecha_hasta:
            if anio:
                filtro_vac += " AND (YEAR(v.fecha_inicio) = %s OR YEAR(v.fecha_fin) = %s)"
                params_vac.extend([int(anio), int(anio)])

        if ids_usuarios:
            placeholders = ",".join(["%s"] * len(ids_usuarios))
            filtro_vac += f" AND v.id_usuario IN ({placeholders})"
            params_vac.extend(ids_usuarios)

        cursor.execute(f"""
            SELECT 
                CONCAT(u.nombre, ' ', u.apellidos) AS fiscalizador,
                v.fecha_inicio,
                v.fecha_fin,
                DATEDIFF(v.fecha_fin, v.fecha_inicio) + 1 AS dias_tomados,
                CASE
                    WHEN CURDATE() < v.fecha_inicio THEN 'pendiente'
                    WHEN CURDATE() BETWEEN v.fecha_inicio AND v.fecha_fin THEN 'en_curso'
                    ELSE 'finalizado'
                END AS estado
            FROM vacaciones v
            JOIN usuarios u ON u.id_usuario = v.id_usuario
            {filtro_vac}
            ORDER BY v.fecha_inicio DESC
        """, params_vac)

        vacaciones = cursor.fetchall()

        # ==========================
        # RESUMEN VACACIONES
        # ==========================
        resumen_vacaciones = _resumen_vacaciones_admin(
            cursor, anio, fecha_desde, fecha_hasta, ids_usuarios
        )
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()

    reporte_data = filtrar_por_texto(reporte_data, buscar)
    detalle = filtrar_por_texto(detalle, buscar_detalle)
    vacaciones = filtrar_por_texto(vacaciones, buscar_vac)
    resumen_vacaciones = filtrar_por_texto(resumen_vacaciones, buscar_resumen_vac)

    return render_template(
        "reporte.html",
        reporte=reporte_data,
        detalle=detalle,
        vacaciones=vacaciones,
        resumen_vacaciones=resumen_vacaciones,
        usuarios=usuarios
    )


# ==========================
# ESTILOS PDF / EXCEL
# ==========================

def _estilos_pdf():
    return estilos_pdf()


def _build_pdf_tabla(encabezados, filas, estilos, ancho_disponible, columnas_centradas=None):
    return build_pdf_tabla(encabezados, filas, estilos, ancho_disponible, columnas_centradas)


def _configurar_encabezado_excel(ws, columnas, titulo=None):
    return configurar_encabezado_excel(ws, columnas, titulo)


def _aplicar_estilo_datos_excel(ws, columnas, data_start):
    aplicar_estilo_datos_excel(ws, columnas, data_start)




# ==========================
# EXPORTAR PDF (RESUMEN)
# ==========================
# Genera y descarga un PDF con el resumen de guardias
@reporte_bp.route("/exportar/pdf")
def exportar_pdf():
    if "usuario" not in session:
        return redirect("/")
    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    anio = request.args.get("anio")
    fecha_desde = request.args.get("fecha_desde")
    fecha_hasta = request.args.get("fecha_hasta")
    ids_usuarios = request.args.getlist("id_usuario")
    buscar = request.args.get("buscar")

    conn = None
    cursor = None
    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        filtro, params = construir_filtro(
            anio=anio,
            fecha_desde=fecha_desde, fecha_hasta=fecha_hasta,
            ids_usuarios=ids_usuarios
        )

        filtro_pend, params_pend = _filtro_fechas(anio, fecha_desde, fecha_hasta, "g2.fecha_guardia")

        cursor.execute(f"""
            SELECT 
                CONCAT(u.nombre,' ',u.apellidos) AS fiscalizador,
                COUNT(g.id_guardia) AS total_guardias,
                SUM(CASE WHEN f.id_feriado IS NOT NULL THEN 1 ELSE 0 END) AS guardias_feriado,
                SUM(CASE WHEN c.id_compensacion IS NOT NULL AND c.fecha_compensacion <= CURDATE() THEN 1 ELSE 0 END) AS compensaciones,
                (
                    SELECT COUNT(g2.id_guardia)
                    FROM guardias g2
                    LEFT JOIN asistencia a2 ON g2.id_guardia = a2.id_guardia
                    LEFT JOIN compensaciones c2 ON g2.id_guardia = c2.id_guardia
                    WHERE g2.id_usuario = u.id_usuario
                      AND a2.estado = 'asistio'
                      AND (c2.id_compensacion IS NULL OR c2.fecha_compensacion > CURDATE())
                      {filtro_pend}
                ) AS pendientes
            FROM guardias g
            LEFT JOIN usuarios u ON g.id_usuario = u.id_usuario
            LEFT JOIN feriados f ON g.id_feriado = f.id_feriado
            LEFT JOIN compensaciones c ON g.id_guardia = c.id_guardia
            {filtro}
            GROUP BY u.id_usuario
        """, params_pend + params)

        data = filtrar_por_texto(cursor.fetchall(), buscar)
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()

    estilos = _estilos_pdf()
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4),
                            leftMargin=30, rightMargin=30, topMargin=30, bottomMargin=30)
    elementos = []

    # Título con acento azul (simulado con una línea)
    elementos.append(Paragraph("Reporte de Guardias", estilos['titulo']))
    elementos.append(Spacer(1, 2))
    elementos.append(HRFlowable(width='40', thickness=3, color=colors.HexColor(COLOR_ACENTO),
                                 spaceAfter=10, hAlign='LEFT'))

    # Subtítulo con filtros aplicados
    partes = []
    if anio: partes.append(f"Año: {anio}")
    if fecha_desde: partes.append(f"Desde: {fecha_desde}")
    if fecha_hasta: partes.append(f"Hasta: {fecha_hasta}")
    if partes:
        elementos.append(Paragraph(" | ".join(partes), estilos['subtitulo']))

    columnas = ["Fiscalizador", "Total de Guardias", "Total de Feriados",
                "Total Compensaciones", "Compensaciones Pendientes"]
    filas = [[
        d["fiscalizador"],
        str(d["total_guardias"]),
        str(d["guardias_feriado"]),
        str(d["compensaciones"]),
        str(d["pendientes"])
    ] for d in data]
    ancho = landscape(A4)[0] - 60

    table = _build_pdf_tabla(columnas, filas, estilos, ancho, columnas_centradas=[1, 2, 3, 4])
    elementos.append(table)
    doc.build(elementos)
    buffer.seek(0)

    return send_file(buffer, download_name="reporte.pdf", as_attachment=True)


# ==========================
# EXPORTAR EXCEL (RESUMEN)
# ==========================
# Genera y descarga un archivo Excel con el resumen de guardias
@reporte_bp.route("/exportar/excel")
def exportar_excel():
    if "usuario" not in session:
        return redirect("/")
    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    anio = request.args.get("anio")
    fecha_desde = request.args.get("fecha_desde")
    fecha_hasta = request.args.get("fecha_hasta")
    ids_usuarios = request.args.getlist("id_usuario")
    buscar = request.args.get("buscar")

    conn = None
    cursor = None
    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        filtro, params = construir_filtro(
            anio=anio,
            fecha_desde=fecha_desde, fecha_hasta=fecha_hasta,
            ids_usuarios=ids_usuarios
        )

        filtro_pend, params_pend = _filtro_fechas(anio, fecha_desde, fecha_hasta, "g2.fecha_guardia")

        cursor.execute(f"""
            SELECT 
                CONCAT(u.nombre,' ',u.apellidos) AS fiscalizador,
                COUNT(g.id_guardia) AS total_guardias,
                SUM(CASE WHEN f.id_feriado IS NOT NULL THEN 1 ELSE 0 END) AS guardias_feriado,
                SUM(CASE WHEN c.id_compensacion IS NOT NULL AND c.fecha_compensacion <= CURDATE() THEN 1 ELSE 0 END) AS compensaciones,
                (
                    SELECT COUNT(g2.id_guardia)
                    FROM guardias g2
                    LEFT JOIN asistencia a2 ON g2.id_guardia = a2.id_guardia
                    LEFT JOIN compensaciones c2 ON g2.id_guardia = c2.id_guardia
                    WHERE g2.id_usuario = u.id_usuario
                      AND a2.estado = 'asistio'
                      AND (c2.id_compensacion IS NULL OR c2.fecha_compensacion > CURDATE())
                      {filtro_pend}
                ) AS pendientes
            FROM guardias g
            LEFT JOIN usuarios u ON g.id_usuario = u.id_usuario
            LEFT JOIN feriados f ON g.id_feriado = f.id_feriado
            LEFT JOIN compensaciones c ON g.id_guardia = c.id_guardia
            {filtro}
            GROUP BY u.id_usuario
        """, params_pend + params)

        data = filtrar_por_texto(cursor.fetchall(), buscar)
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Resumen"
    columnas = ["Fiscalizador", "Total de Guardias", "Total de Feriados",
                "Total Compensaciones", "Compensaciones Pendientes"]
    data_start = _configurar_encabezado_excel(ws, columnas, titulo="Reporte de Guardias")
    for d in data:
        ws.append([
            d["fiscalizador"],
            d["total_guardias"],
            d["guardias_feriado"],
            d["compensaciones"],
            d["pendientes"]
        ])
    _aplicar_estilo_datos_excel(ws, columnas, data_start)
    # Alinear centro columnas numéricas
    for row in ws.iter_rows(min_row=data_start, max_row=ws.max_row, min_col=2, max_col=5):
        for cel in row:
            cel.alignment = Alignment(horizontal='center', vertical='center')

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(output, download_name="reporte.xlsx", as_attachment=True)


# ==========================
# EXPORTAR PDF DETALLE
# ==========================
# Genera y descarga un PDF con el detalle de guardias por fecha
@reporte_bp.route("/exportar/detalle_fecha/pdf")
def exportar_detalle_fecha_pdf():
    if "usuario" not in session:
        return redirect("/")
    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    anio = request.args.get("anio")
    fecha_desde = request.args.get("fecha_desde")
    fecha_hasta = request.args.get("fecha_hasta")
    ids_usuarios = request.args.getlist("id_usuario")
    buscar = request.args.get("buscar")

    conn = None
    cursor = None
    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        filtro, params = construir_filtro(
            anio=anio,
            fecha_desde=fecha_desde, fecha_hasta=fecha_hasta,
            ids_usuarios=ids_usuarios
        )

        cursor.execute(f"""
            SELECT 
                CONCAT(u.nombre,' ',u.apellidos) AS fiscalizador,
                g.fecha_guardia,
                g.tipo,
                COALESCE(f.descripcion, '—') AS feriado_descripcion,
                CASE 
                    WHEN a.estado = 'asistio' THEN '✔ Asistió'
                    WHEN a.estado = 'falta' THEN '❌ Falta'
                    WHEN a.estado = 'justificado' THEN '🟡 Justificado'
                    ELSE '—'
                END AS asistencia,
                c.fecha_compensacion,
                c.observacion,
                {ESTADO_COMPENSACION_SQL} AS estado_compensacion
            FROM guardias g
            LEFT JOIN usuarios u ON g.id_usuario = u.id_usuario
            LEFT JOIN asistencia a ON g.id_guardia = a.id_guardia
            LEFT JOIN compensaciones c ON g.id_guardia = c.id_guardia
            LEFT JOIN feriados f ON g.id_feriado = f.id_feriado
            {filtro}
            ORDER BY g.fecha_guardia DESC
        """, params)

        detalle = filtrar_por_texto(cursor.fetchall(), buscar)
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()

    estilos = _estilos_pdf()
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4),
                            leftMargin=25, rightMargin=25, topMargin=30, bottomMargin=30)
    elementos = []

    elementos.append(Paragraph("Detalle de Guardias", estilos['titulo']))
    elementos.append(Spacer(1, 2))
    elementos.append(HRFlowable(width='40', thickness=3, color=colors.HexColor(COLOR_ACENTO),
                                 spaceAfter=10, hAlign='LEFT'))

    partes = []
    if anio: partes.append(f"Año: {anio}")
    if fecha_desde: partes.append(f"Desde: {fecha_desde}")
    if fecha_hasta: partes.append(f"Hasta: {fecha_hasta}")
    if partes:
        elementos.append(Paragraph(" | ".join(partes), estilos['subtitulo']))

    columnas = ["Fiscalizador", "Fecha", "Tipo", "Feriado", "Asistencia", "Compensación", "Estado", "Observaciones"]
    filas = [[
        d["fiscalizador"],
        _fecha(d["fecha_guardia"]),
        "Soporte" if d["tipo"] == "soporte" else "Guardia",
        d["feriado_descripcion"],
        d["asistencia"],
        _fecha(d["fecha_compensacion"]) or "\u2014",
        d["estado_compensacion"],
        d["observacion"] or "\u2014"
    ] for d in detalle]
    ancho = landscape(A4)[0] - 50

    table = _build_pdf_tabla(columnas, filas, estilos, ancho)
    elementos.append(table)
    doc.build(elementos)
    buffer.seek(0)

    return send_file(buffer, download_name="detalle_fecha.pdf", as_attachment=True)


# ==========================
# EXPORTAR EXCEL DETALLE
# ==========================
# Genera y descarga un archivo Excel con el detalle de guardias por fecha
@reporte_bp.route("/exportar/detalle_fecha/excel")
def exportar_detalle_fecha_excel():
    if "usuario" not in session:
        return redirect("/")
    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    anio = request.args.get("anio")
    fecha_desde = request.args.get("fecha_desde")
    fecha_hasta = request.args.get("fecha_hasta")
    ids_usuarios = request.args.getlist("id_usuario")
    buscar = request.args.get("buscar")

    conn = None
    cursor = None
    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        filtro, params = construir_filtro(
            anio=anio,
            fecha_desde=fecha_desde, fecha_hasta=fecha_hasta,
            ids_usuarios=ids_usuarios
        )

        cursor.execute(f"""
            SELECT 
                CONCAT(u.nombre,' ',u.apellidos) AS fiscalizador,
                g.fecha_guardia,
                g.tipo,
                COALESCE(f.descripcion, '—') AS feriado_descripcion,
                CASE 
                    WHEN a.estado = 'asistio' THEN '✔ Asistió'
                    WHEN a.estado = 'falta' THEN '❌ Falta'
                    WHEN a.estado = 'justificado' THEN '🟡 Justificado'
                    ELSE '—'
                END AS asistencia,
                c.fecha_compensacion,
                c.observacion,
                {ESTADO_COMPENSACION_SQL} AS estado_compensacion
            FROM guardias g
            LEFT JOIN usuarios u ON g.id_usuario = u.id_usuario
            LEFT JOIN asistencia a ON g.id_guardia = a.id_guardia
            LEFT JOIN compensaciones c ON g.id_guardia = c.id_guardia
            LEFT JOIN feriados f ON g.id_feriado = f.id_feriado
            {filtro}
            ORDER BY g.fecha_guardia DESC
        """, params)

        detalle = filtrar_por_texto(cursor.fetchall(), buscar)
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Detalle Fecha"
    columnas = ["Fiscalizador", "Fecha", "Tipo", "Feriado", "Asistencia", "Compensación", "Estado", "Observaciones"]
    data_start = _configurar_encabezado_excel(ws, columnas, titulo="Detalle de Guardias")
    for d in detalle:
        ws.append([
            d["fiscalizador"],
            _fecha(d["fecha_guardia"]),
            "Soporte" if d["tipo"] == "soporte" else "Guardia",
            d["feriado_descripcion"],
            d["asistencia"],
            _fecha(d["fecha_compensacion"]),
            d["estado_compensacion"],
            d["observacion"] or ""
        ])
    _aplicar_estilo_datos_excel(ws, columnas, data_start)

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(output, download_name="detalle_fecha.xlsx", as_attachment=True)


# ==========================
# EXPORTAR PDF VACACIONES
# ==========================
# Genera y descarga un PDF con el detalle de vacaciones
@reporte_bp.route("/exportar/vacaciones/pdf")
def exportar_vacaciones_pdf():
    if "usuario" not in session:
        return redirect("/")
    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    anio = request.args.get("anio")
    fecha_desde = request.args.get("fecha_desde")
    fecha_hasta = request.args.get("fecha_hasta")
    ids_usuarios = request.args.getlist("id_usuario")
    buscar_vac = request.args.get("buscar_vac")

    conn = None
    cursor = None
    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        filtro_vac = "WHERE 1=1"
        params_vac = []

        if fecha_desde:
            filtro_vac += " AND v.fecha_fin >= %s"
            params_vac.append(fecha_desde)

        if fecha_hasta:
            filtro_vac += " AND v.fecha_inicio <= %s"
            params_vac.append(fecha_hasta)

        if not fecha_desde and not fecha_hasta:
            if anio:
                filtro_vac += " AND (YEAR(v.fecha_inicio) = %s OR YEAR(v.fecha_fin) = %s)"
                params_vac.extend([int(anio), int(anio)])

        if ids_usuarios:
            placeholders = ",".join(["%s"] * len(ids_usuarios))
            filtro_vac += f" AND v.id_usuario IN ({placeholders})"
            params_vac.extend(ids_usuarios)

        cursor.execute(f"""
            SELECT 
                CONCAT(u.nombre, ' ', u.apellidos) AS fiscalizador,
                v.fecha_inicio,
                v.fecha_fin,
                DATEDIFF(v.fecha_fin, v.fecha_inicio) + 1 AS dias_tomados,
                CASE
                    WHEN CURDATE() < v.fecha_inicio THEN 'pendiente'
                    WHEN CURDATE() BETWEEN v.fecha_inicio AND v.fecha_fin THEN 'en_curso'
                    ELSE 'finalizado'
                END AS estado
            FROM vacaciones v
            JOIN usuarios u ON u.id_usuario = v.id_usuario
            {filtro_vac}
            ORDER BY v.fecha_inicio DESC
        """, params_vac)

        data = filtrar_por_texto(cursor.fetchall(), buscar_vac)
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()

    estilos = _estilos_pdf()
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4),
                            leftMargin=30, rightMargin=30, topMargin=30, bottomMargin=30)
    elementos = []

    elementos.append(Paragraph("Detalle de Vacaciones", estilos['titulo']))
    elementos.append(Spacer(1, 2))
    elementos.append(HRFlowable(width='40', thickness=3, color=colors.HexColor(COLOR_ACENTO),
                                 spaceAfter=10, hAlign='LEFT'))

    partes = []
    if anio: partes.append(f"Año: {anio}")
    if fecha_desde: partes.append(f"Desde: {fecha_desde}")
    if fecha_hasta: partes.append(f"Hasta: {fecha_hasta}")
    if partes:
        elementos.append(Paragraph(" | ".join(partes), estilos['subtitulo']))

    columnas = ["Fiscalizador", "Inicio", "Fin", "Días Tomados", "Estado"]
    filas = [[
        v["fiscalizador"],
        _fecha(v["fecha_inicio"]),
        _fecha(v["fecha_fin"]),
        str(v["dias_tomados"]),
        v["estado"].replace('_', ' ').title()
    ] for v in data]
    ancho = landscape(A4)[0] - 60

    table = _build_pdf_tabla(columnas, filas, estilos, ancho, columnas_centradas=[3])
    elementos.append(table)
    doc.build(elementos)
    buffer.seek(0)

    return send_file(buffer, download_name="vacaciones.pdf", as_attachment=True)


# ==========================
# EXPORTAR EXCEL VACACIONES
# ==========================
# Genera y descarga un archivo Excel con el detalle de vacaciones
@reporte_bp.route("/exportar/vacaciones/excel")
def exportar_vacaciones_excel():
    if "usuario" not in session:
        return redirect("/")
    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    anio = request.args.get("anio")
    fecha_desde = request.args.get("fecha_desde")
    fecha_hasta = request.args.get("fecha_hasta")
    ids_usuarios = request.args.getlist("id_usuario")
    buscar_vac = request.args.get("buscar_vac")

    conn = None
    cursor = None
    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        filtro_vac = "WHERE 1=1"
        params_vac = []

        if fecha_desde:
            filtro_vac += " AND v.fecha_fin >= %s"
            params_vac.append(fecha_desde)

        if fecha_hasta:
            filtro_vac += " AND v.fecha_inicio <= %s"
            params_vac.append(fecha_hasta)

        if not fecha_desde and not fecha_hasta:
            if anio:
                filtro_vac += " AND (YEAR(v.fecha_inicio) = %s OR YEAR(v.fecha_fin) = %s)"
                params_vac.extend([int(anio), int(anio)])

        if ids_usuarios:
            placeholders = ",".join(["%s"] * len(ids_usuarios))
            filtro_vac += f" AND v.id_usuario IN ({placeholders})"
            params_vac.extend(ids_usuarios)

        cursor.execute(f"""
            SELECT 
                CONCAT(u.nombre, ' ', u.apellidos) AS fiscalizador,
                v.fecha_inicio,
                v.fecha_fin,
                DATEDIFF(v.fecha_fin, v.fecha_inicio) + 1 AS dias_tomados,
                CASE
                    WHEN CURDATE() < v.fecha_inicio THEN 'pendiente'
                    WHEN CURDATE() BETWEEN v.fecha_inicio AND v.fecha_fin THEN 'en_curso'
                    ELSE 'finalizado'
                END AS estado
            FROM vacaciones v
            JOIN usuarios u ON u.id_usuario = v.id_usuario
            {filtro_vac}
            ORDER BY v.fecha_inicio DESC
        """, params_vac)

        data = filtrar_por_texto(cursor.fetchall(), buscar_vac)
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Vacaciones"
    columnas = ["Fiscalizador", "Inicio", "Fin", "Días Tomados", "Estado"]
    data_start = _configurar_encabezado_excel(ws, columnas, titulo="Detalle de Vacaciones")
    for v in data:
        ws.append([
            v["fiscalizador"],
            _fecha(v["fecha_inicio"]),
            _fecha(v["fecha_fin"]),
            v["dias_tomados"],
            v["estado"].replace('_', ' ').title()
        ])
    _aplicar_estilo_datos_excel(ws, columnas, data_start)

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(output, download_name="vacaciones.xlsx", as_attachment=True)


# ==========================
# EXPORTAR PDF RESUMEN VACACIONES
# ==========================
# Genera y descarga un PDF con el resumen de vacaciones por fiscalizador
@reporte_bp.route("/exportar/resumen_vacaciones/pdf")
def exportar_resumen_vacaciones_pdf():
    if "usuario" not in session:
        return redirect("/")
    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    anio = request.args.get("anio")
    fecha_desde = request.args.get("fecha_desde")
    fecha_hasta = request.args.get("fecha_hasta")
    ids_usuarios = request.args.getlist("id_usuario")
    buscar_resumen_vac = request.args.get("buscar_resumen_vac")

    conn = None
    cursor = None
    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        data = filtrar_por_texto(
            _resumen_vacaciones_admin(cursor, anio, fecha_desde, fecha_hasta, ids_usuarios),
            buscar_resumen_vac
        )
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()

    estilos = _estilos_pdf()
    buffer = BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=landscape(A4),
                            leftMargin=30, rightMargin=30, topMargin=30, bottomMargin=30)
    elementos = []

    elementos.append(Paragraph("Resumen de Vacaciones", estilos['titulo']))
    elementos.append(Spacer(1, 2))
    elementos.append(HRFlowable(width='40', thickness=3, color=colors.HexColor(COLOR_ACENTO),
                                 spaceAfter=10, hAlign='LEFT'))

    partes = []
    if anio: partes.append(f"Año: {anio}")
    if fecha_desde: partes.append(f"Desde: {fecha_desde}")
    if fecha_hasta: partes.append(f"Hasta: {fecha_hasta}")
    if partes:
        elementos.append(Paragraph(" | ".join(partes), estilos['subtitulo']))

    columnas = ["Fiscalizador", "Días Tomados", "Días Pendientes", "Días Pend. Años Anteriores"]
    filas = [[
        r["fiscalizador"],
        str(r["dias_tomados"]),
        str(r["dias_pendientes"]),
        str(r["dias_pendientes_anteriores"])
    ] for r in data]
    ancho = landscape(A4)[0] - 60

    table = _build_pdf_tabla(columnas, filas, estilos, ancho, columnas_centradas=[1, 2, 3])
    elementos.append(table)
    doc.build(elementos)
    buffer.seek(0)

    return send_file(buffer, download_name="resumen_vacaciones.pdf", as_attachment=True)


# ==========================
# EXPORTAR EXCEL RESUMEN VACACIONES
# ==========================
# Genera y descarga un archivo Excel con el resumen de vacaciones por fiscalizador
@reporte_bp.route("/exportar/resumen_vacaciones/excel")
def exportar_resumen_vacaciones_excel():
    if "usuario" not in session:
        return redirect("/")
    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    anio = request.args.get("anio")
    fecha_desde = request.args.get("fecha_desde")
    fecha_hasta = request.args.get("fecha_hasta")
    ids_usuarios = request.args.getlist("id_usuario")
    buscar_resumen_vac = request.args.get("buscar_resumen_vac")

    conn = None
    cursor = None
    try:
        conn = conexion()
        cursor = conn.cursor(dictionary=True)

        data = filtrar_por_texto(
            _resumen_vacaciones_admin(cursor, anio, fecha_desde, fecha_hasta, ids_usuarios),
            buscar_resumen_vac
        )
    finally:
        if cursor is not None:
            cursor.close()
        if conn is not None:
            conn.close()

    wb = Workbook()
    ws = wb.active
    ws.title = "Resumen Vacaciones"
    columnas = ["Fiscalizador", "Días Tomados", "Días Pendientes", "Días Pend. Años Anteriores"]
    data_start = _configurar_encabezado_excel(ws, columnas, titulo="Resumen de Vacaciones")
    for r in data:
        ws.append([
            r["fiscalizador"],
            r["dias_tomados"],
            r["dias_pendientes"],
            r["dias_pendientes_anteriores"]
        ])
    _aplicar_estilo_datos_excel(ws, columnas, data_start)
    # Center numeric columns
    for row in ws.iter_rows(min_row=data_start, max_row=ws.max_row, min_col=2, max_col=4):
        for cel in row:
            cel.alignment = Alignment(horizontal='center', vertical='center')

    output = BytesIO()
    wb.save(output)
    output.seek(0)

    return send_file(output, download_name="resumen_vacaciones.xlsx", as_attachment=True)
