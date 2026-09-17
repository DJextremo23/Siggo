"""
Generador de presentación PPTX con el formato "Reporte de Guardia" (rv0).

Reproduce la estructura y el diseño del archivo `0. Reporte PPT prueba rv0.pptx`:
   1. Portada (fondo corporativo, supervisor, título, fecha y talleres supervisados).
   2. Páginas de contenido (barra superior, logo, pie y número de página) rellenas
      con la información analizada por la IA.
   3. Cierre ("Muchas Gracias").

Los recursos gráficos (fondo y logos) provienen de `static/ppt/`.
"""

import os
from io import BytesIO

from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN, MSO_ANCHOR
from pptx.enum.shapes import MSO_SHAPE
from pptx.oxml.ns import qn

# ── Recursos gráficos (logos corporativos OIG y diseño de fondo) ────────────
_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ASSETS = os.path.join(_BASE, "static", "ppt")
FONDO = os.path.join(_ASSETS, "fondo.png")
LOGO_PORTADA = os.path.join(_ASSETS, "logo.png")
LOGO_CONTENIDO = os.path.join(_ASSETS, "logo_small.jpg")

# ── Paleta y tipografía del formato rv0 ─────────────────────────────────────
C_NAVY = RGBColor(0x10, 0x2A, 0x43)   # 102A43 fondo oscuro
C_CONTENIDO_BG = RGBColor(0xF7, 0xFA, 0xFC)  # F7FAFC fondo de páginas de contenido
C_TEAL = RGBColor(0x00, 0xA6, 0xA6)   # 00A6A6 barra superior / acentos
C_GREEN = RGBColor(0x5D, 0xD3, 0x9E)  # 5DD39E acento verde
C_WHITE = RGBColor(0xFF, 0xFF, 0xFF)
C_LIGHT = RGBColor(0xD8, 0xE2, 0xEA)  # D8E2EA gris claro
C_MUTED = RGBColor(0x64, 0x74, 0x8B)  # 64748B gris apagado (pie)
C_CARD_BG = RGBColor(0x17, 0x3A, 0x5E)  # 173A5E tarjeta portada
C_CARD_BR = RGBColor(0x2B, 0x56, 0x7B)  # 2B567B borde tarjeta
C_SEP = RGBColor(0xCB, 0xD5, 0xE1)     # CBD5E1 separador pie
C_TEXT = RGBColor(0x1E, 0x29, 0x3B)     # texto contenido
C_ZEBRA = RGBColor(0xF1, 0xF5, 0xF9)    # filas alternas tablas

FONT_TITLE = "Aptos Display"
FONT_BODY = "Aptos"

_MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio",
          "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"]

# Lista fija de talleres supervisados (igual a la del formato del PPT).
TALLERES_SUPERVISADOS = ("Compresión · Generación · Movimiento de suelos / Remoción · "
                         "Gasfitería, ductos y tanques · Energía / Montaje / Flota / "
                         "Mecánica / Instrumentación")


def _fmt_fecha(fecha):
    """Formatea una fecha de guardia a 'd de Mes aaaa' (o cadena vacía)."""
    if not fecha:
        return ""
    try:
        d = fecha if hasattr(fecha, "day") else None
        if d is None:
            return str(fecha)
        return f"{d.day} de {_MESES[d.month - 1]} {d.year}"
    except Exception:
        return str(fecha)


def _fmt_mes_ano(fecha):
    """Devuelve 'Mes aaaa' para el pie de página."""
    if not fecha:
        return ""
    try:
        if not hasattr(fecha, "month"):
            return str(fecha)
        return f"{_MESES[fecha.month - 1]} {fecha.year}"
    except Exception:
        return str(fecha)


# ── Helpers de bajo nivel ───────────────────────────────────────────────────
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


def _rect(slide, x, y, w, h, fill, line=None, line_w=None):
    shape = slide.shapes.add_shape(MSO_SHAPE.RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    if line is not None:
        shape.line.color.rgb = line
        shape.line.width = Pt(line_w or 0.75)
    else:
        _no_line(shape)
    _no_shadow(shape)
    return shape


def _rrect(slide, x, y, w, h, fill, line=None, line_w=None, radius=0.12):
    shape = slide.shapes.add_shape(MSO_SHAPE.ROUNDED_RECTANGLE, Inches(x), Inches(y), Inches(w), Inches(h))
    shape.fill.solid()
    shape.fill.fore_color.rgb = fill
    if line is not None:
        shape.line.color.rgb = line
        shape.line.width = Pt(line_w or 0.75)
    else:
        _no_line(shape)
    try:
        shape.adjustments[0] = radius
    except Exception:
        pass
    _no_shadow(shape)
    return shape


def _fill_opacity(shape, opacity_pct):
    """Aplica opacidad (0-100) al relleno sólido de una forma."""
    try:
        spPr = shape._element.find(qn('p:spPr'))
        sf = spPr.find(qn('a:solidFill'))
        srgb = sf.find(qn('a:srgbClr'))
        for a in srgb.findall(qn('a:alpha')):
            srgb.remove(a)
        a = srgb.makeelement(qn('a:alpha'), {'val': str(int(opacity_pct * 1000))})
        srgb.append(a)
    except Exception:
        pass


def _line_opacity(shape, opacity_pct):
    """Aplica opacidad (0-100) a la línea de una forma."""
    try:
        spPr = shape._element.find(qn('p:spPr'))
        ln = spPr.find(qn('a:ln'))
        sf = ln.find(qn('a:solidFill'))
        srgb = sf.find(qn('a:srgbClr'))
        for a in srgb.findall(qn('a:alpha')):
            srgb.remove(a)
        a = srgb.makeelement(qn('a:alpha'), {'val': str(int(opacity_pct * 1000))})
        srgb.append(a)
    except Exception:
        pass


def _picture_opacity(picture, opacity_pct):
    """Aplica opacidad (0-100) a una imagen (alphaModFix sobre el blip)."""
    try:
        blip = picture._element.find(qn('p:blipFill')).find(qn('a:blip'))
        amf = blip.makeelement(qn('a:alphaModFix'), {'amt': str(int(opacity_pct * 1000))})
        blip.append(amf)
    except Exception:
        pass


def _autofit_norm(tf, lnspc_reduction=10000):
    """Aplica autoajuste 'normal' (normAutofit) al marco de texto, como el formato."""
    try:
        bodyPr = tf._txBody.find(qn('a:bodyPr'))
        if bodyPr is None:
            return
        for tag in ('a:spAutoFit', 'a:normAutofit', 'a:noAutofit'):
            for el in bodyPr.findall(qn(tag)):
                bodyPr.remove(el)
        norm = bodyPr.makeelement(qn('a:normAutofit'), {'lnSpcReduction': str(lnspc_reduction)})
        bodyPr.append(norm)
    except Exception:
        pass


def _text(slide, x, y, w, h, text, size=13, color=C_TEXT, bold=False,
          align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, font=FONT_BODY,
          wrap=True, spacing=1.0):
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = wrap
    tf.margin_left = 0
    tf.margin_right = 0
    tf.margin_top = 0
    tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    p = tf.paragraphs[0]
    p.alignment = align
    p.line_spacing = spacing
    r = p.add_run()
    r.text = str(text)
    r.font.size = Pt(size)
    r.font.color.rgb = color
    r.font.bold = bold
    r.font.name = font
    return tb, tf


def _pic(slide, path, x, y, w, h):
    if not path or not os.path.exists(path):
        return None
    return slide.shapes.add_picture(path, Inches(x), Inches(y), width=Inches(w), height=Inches(h))


def _add_picture_fitted(slide, data_bytes, x, y, max_w, max_h):
    """Inserta una imagen (desde bytes) ajustándola a un recuadro sin deformarla."""
    if not data_bytes:
        return None
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
    return slide.shapes.add_picture(BytesIO(data_bytes), Inches(px), Inches(py), width=Inches(w), height=Inches(h))


def _cell(cell, text, size, color, bold, fill, align=PP_ALIGN.LEFT):
    cell.fill.solid()
    cell.fill.fore_color.rgb = fill
    cell.margin_left = Inches(0.04)
    cell.margin_right = Inches(0.04)
    cell.margin_top = Inches(0.02)
    cell.margin_bottom = Inches(0.02)
    cell.vertical_anchor = MSO_ANCHOR.MIDDLE
    tf = cell.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = align
    r = p.add_run()
    r.text = str(text)
    r.font.size = Pt(size)
    r.font.bold = bold
    r.font.color.rgb = color
    r.font.name = FONT_BODY


def _add_table(slide, x, y, w, headers, rows, col_ratios, font_size=8, row_h=0.3):
    n_rows = len(rows) + 1
    n_cols = len(headers)
    shape = slide.shapes.add_table(n_rows, n_cols, Inches(x), Inches(y), Inches(w), Inches(row_h * n_rows))
    tbl = shape.table
    tbl.first_row = False
    tbl.horz_banding = False
    total = sum(col_ratios)
    for j in range(n_cols):
        tbl.columns[j].width = Inches(w * col_ratios[j] / total)
    for j, h in enumerate(headers):
        _cell(tbl.cell(0, j), h, font_size, C_WHITE, True, C_NAVY)
    for i, row in enumerate(rows):
        fill = C_WHITE if i % 2 == 0 else C_ZEBRA
        for j, val in enumerate(row):
            _cell(tbl.cell(i + 1, j), val if val is not None else "", font_size, C_TEXT, False, fill)
    tbl.rows[0].height = Inches(0.32)
    for i in range(1, n_rows):
        tbl.rows[i].height = Inches(row_h)
    return shape


# ── Bloques de diapositivas ─────────────────────────────────────────────────
def _portada(prs, informe):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    _set_bg(s, C_NAVY)

    _pic(s, FONDO, 1.37, 0.0, 11.96, 7.5)
    _pic(s, LOGO_PORTADA, 0.56, 0.49, 1.89, 0.41)

    supervisor = (informe.get("fiscalizador") or "").strip().upper()
    _text(s, 0.70, 2.05, 6.90, 0.28, f"SUPERVISOR. {supervisor}" if supervisor else "SUPERVISOR.",
          11, C_GREEN, True)

    _text(s, 0.65, 2.42, 8.90, 0.72, "Reporte de Guardia", 35, C_WHITE, True, font=FONT_TITLE)

    fecha_str = _fmt_fecha(informe.get("fecha_guardia"))
    _text(s, 0.70, 3.28, 4.5, 0.35, fecha_str, 17, C_LIGHT)

    card = _rrect(s, 0.70, 4.25, 8.30, 1.45, C_CARD_BG, C_CARD_BR, line_w=0.5)
    _fill_opacity(card, 88)
    _line_opacity(card, 65)

    _text(s, 1.00, 4.45, 4.0, 0.25, "Talleres supervisados", 11, C_GREEN, True, anchor=MSO_ANCHOR.MIDDLE)

    tb_tall, tf_tall = _text(s, 1.00, 4.85, 7.65, 0.55, TALLERES_SUPERVISADOS, 14, C_WHITE,
                             anchor=MSO_ANCHOR.MIDDLE)
    _autofit_norm(tf_tall)

    _text(s, 0.70, 6.86, 1.50, 0.23, "oigperu.com", 10.5, C_LIGHT)
    return s


def _encabezado_contenido(s, mes_ano, num_pagina):
    """Barra superior, logo y pie de página de una diapositiva de contenido."""
    _rect(s, 0.0, 0.0, 13.333, 0.04, C_TEAL)
    _pic(s, LOGO_CONTENIDO, 11.25, 0.22, 1.45, 0.35)
    # Separador inferior del pie
    _rect(s, 0.55, 7.05, 12.25, 0.015, C_SEP)
    _text(s, 0.65, 7.13, 5.5, 0.20, f"Reporte de Guardia | {mes_ano}", 7.5, C_MUTED)
    _text(s, 12.25, 7.10, 0.45, 0.20, str(num_pagina).zfill(2), 7.5, C_MUTED, align=PP_ALIGN.RIGHT)


def _nueva_contenido(prs, mes_ano, num_pagina):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    _set_bg(s, C_CONTENIDO_BG)
    _encabezado_contenido(s, mes_ano, num_pagina)
    return s


def _titulo_contenido(s, texto):
    _rect(s, 0.6, 0.45, 0.06, 0.42, C_TEAL)
    _text(s, 0.82, 0.42, 12.0, 0.5, texto, 20, C_NAVY, True, font=FONT_TITLE, anchor=MSO_ANCHOR.MIDDLE)


def _cierre(prs):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    _set_bg(s, C_NAVY)
    pic = _pic(s, FONDO, 5.53, 0.0, 7.80, 5.20)
    if pic is not None:
        _picture_opacity(pic, 85)
    _text(s, 0.70, 3.42, 5.5, 0.5, "Muchas Gracias", 30, C_WHITE, True, font=FONT_TITLE)
    _text(s, 0.72, 6.92, 1.5, 0.22, "oigperu.com", 10, C_LIGHT)
    return s


def _evidencia_card(slide, x, y, w, evidencia, foto_bytes):
    """Dibuja una evidencia: subtítulo (tipo) + foto + descripción/hallazgos/acción."""
    card_h = 4.6
    _rrect(slide, x, y, w, card_h, C_WHITE, C_SEP, radius=0.05)

    # Subtítulo (categoría): Alerta de Seguridad / Incidente Ambiental / Sustracción
    tipo = (evidencia.get("tipo") or "").strip()
    if tipo:
        _rect(slide, x + 0.3, y + 0.16, 0.06, 0.32, C_TEAL)
        _text(slide, x + 0.5, y + 0.15, w - 0.9, 0.34, tipo.upper(), 15, C_TEAL, True)

    # Metadatos
    meta = []
    n = evidencia.get("n_evidencia", "")
    item_ref = evidencia.get("item_ref", "")
    pozo = evidencia.get("pozo_equipo", "")
    responsable = evidencia.get("responsable", "")
    fecha = evidencia.get("fecha_hora", "")
    if n != "":
        meta.append(f"Evidencia {n}")
    if item_ref:
        meta.append(f"Ítem Ref. {item_ref}")
    if pozo:
        meta.append(f"Pozo/Equipo: {pozo}")
    if responsable:
        meta.append(f"Responsable: {responsable}")
    if fecha:
        meta.append(f"Fecha/Hora: {fecha}")
    meta_line = "  ·  ".join(meta)
    if meta_line:
        _text(slide, x + 0.3, y + 0.6, w - 0.6, 0.28, meta_line, 10, C_MUTED)

    # Foto
    foto_w = 3.4
    foto_h = 3.6
    foto_x = x + 0.3
    foto_y = y + 0.95
    if foto_bytes:
        try:
            _add_picture_fitted(slide, foto_bytes, foto_x, foto_y, foto_w, foto_h)
        except Exception:
            _rrect(slide, foto_x, foto_y, foto_w, foto_h, C_ZEBRA, C_SEP, radius=0.05)
    else:
        _rrect(slide, foto_x, foto_y, foto_w, foto_h, C_ZEBRA, C_SEP, radius=0.05)

    # Campos de texto (derecha)
    tx = foto_x + foto_w + 0.3
    tw = x + w - 0.3 - tx
    campos = [
        ("Descripción", evidencia.get("descripcion", "")),
        ("Hallazgos", evidencia.get("hallazgos", "")),
        ("Acción Tomada", evidencia.get("accion_tomada", "")),
    ]
    cy = foto_y
    for lbl, val in campos:
        _text(slide, tx, cy, tw, 0.28, lbl, 11, C_TEAL, True)
        _text(slide, tx, cy + 0.3, tw, 0.85, val if val else "", 11, C_TEXT, wrap=True)
        cy += 1.25
    return card_h


def _seccion_seguridad(prs, mes_ano, num, evidencias, imagenes_archivo):
    """Construye las diapositivas 'Seguridad y Medio Ambiente' (una evidencia por hoja)."""
    total = max(len(evidencias), len(imagenes_archivo))
    if total == 0:
        s = _nueva_contenido(prs, mes_ano, num)
        _titulo_contenido(s, "Seguridad y Medio Ambiente")
        _evidencia_card(s, 0.6, 1.4, 12.1, {}, None)
        return num + 1

    for i in range(total):
        ev = evidencias[i] if i < len(evidencias) else {}
        foto = imagenes_archivo[i].get("bytes") if i < len(imagenes_archivo) else None
        s = _nueva_contenido(prs, mes_ano, num)
        _titulo_contenido(s, "Seguridad y Medio Ambiente")
        _evidencia_card(s, 0.6, 1.3, 12.1, ev, foto)
        num += 1
    return num


# ── Generador principal ─────────────────────────────────────────────────────
def generar_reporte_ppt(informe, data, imagenes_archivo=None):
    """Construye la presentación con el formato rv0 rellena con los datos de la IA."""
    imagenes_archivo = imagenes_archivo or []
    data = data or {}

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    reg = data.get("registro_guardia") or {}
    cabecera = reg.get("cabecera") or {}
    talleres = reg.get("talleres") or []
    evidencias = reg.get("evidencias") or []
    pendientes_reg = reg.get("pendientes") or []

    mes_ano = _fmt_mes_ano(informe.get("fecha_guardia"))

    # 1) Portada
    _portada(prs, informe)

    # 2) Seguridad y Medio Ambiente (segunda hoja)
    num = _seccion_seguridad(prs, mes_ano, 2, evidencias, imagenes_archivo)

    # 3) Producción (resumen del formato)
    cabecera_items = [
        ("Producción OIL (BPD)", cabecera.get("produccion_oil_bpd")),
        ("Prod. Perdida imputada", cabecera.get("prod_perdida_imputada")),
        ("Producción GAS (MPC)", cabecera.get("produccion_gas_mpc")),
        ("Producción recuperada", cabecera.get("produccion_recuperada")),
    ]
    if any(v not in (None, "") for _, v in cabecera_items):
        s = _nueva_contenido(prs, mes_ano, num)
        _titulo_contenido(s, "Producción")
        num += 1
        for i, (lbl, val) in enumerate(cabecera_items):
            col = i % 2
            fila = i // 2
            x = 0.6 + col * 6.2
            y = 1.6 + fila * 1.7
            _rrect(s, x, y, 5.9, 1.25, C_ZEBRA, C_SEP, radius=0.08)
            _text(s, x + 0.2, y + 0.2, 5.5, 0.3, lbl, 10, C_MUTED, True)
            _text(s, x + 0.2, y + 0.55, 5.5, 0.4, str(val if val not in (None, "") else "—"), 18, C_NAVY, True)

    # 4) Talleres (tablas)
    COLS_TALLER = ["Ítem", "Pozo", "Batería", "Prod (bopd)", "Requerimiento", "Estado",
                   "Fecha", "Tipo", "Relev.", "Cuadrilla", "Actividad Ejecutada"]
    RATIOS_TALLER = [0.5, 1.3, 1.0, 0.9, 2.6, 0.8, 1.0, 0.9, 0.7, 0.9, 2.8]
    campos_taller = ["item", "pozo", "bateria", "produccion_bopd", "requerimiento", "estado",
                     "fecha_ejecucion", "tipo", "relevante", "cuadrilla", "actividad_ejecutada"]

    s_actual = None
    cursor_y = 0.0
    for t in talleres:
        filas = t.get("filas") or []
        if not filas:
            continue
        nombre = t.get("taller") or "Taller"
        rows = [[f.get(c, "") for c in campos_taller] for f in filas]
        alto_necesario = 0.5 + 0.32 + len(rows) * 0.32
        if s_actual is None or cursor_y + alto_necesario > 6.85:
            s_actual = _nueva_contenido(prs, mes_ano, num)
            _titulo_contenido(s_actual, "Trabajos de Guardia — Talleres")
            num += 1
            cursor_y = 1.15
        _text(s_actual, 0.6, cursor_y, 8.0, 0.3, nombre.upper(), 12, C_TEAL, True)
        _add_table(s_actual, 0.6, cursor_y + 0.35, 12.1, COLS_TALLER, rows, RATIOS_TALLER, font_size=7, row_h=0.3)
        cursor_y += 0.5 + 0.32 + len(rows) * 0.30 + 0.25

    # 5) Pendientes
    if pendientes_reg:
        COLS_PEND = ["Ítem", "Pozo", "Batería", "Prod (bopd)", "Requerimiento", "Estado", "Fecha", "Actividad Ejecutada"]
        RATIOS_PEND = [0.5, 1.4, 1.0, 1.0, 3.0, 0.9, 1.1, 3.0]
        campos_pend = ["item", "pozo", "bateria", "produccion_bopd", "requerimiento", "estado",
                       "fecha_ejecucion", "actividad_ejecutada"]
        s_actual = None
        cursor_y = 0.0
        for p in pendientes_reg:
            filas = p.get("filas") or []
            if not filas:
                continue
            nombre = p.get("taller") or "Pendientes"
            rows = [[f.get(c, "") for c in campos_pend] for f in filas]
            alto_necesario = 0.5 + 0.32 + len(rows) * 0.32
            if s_actual is None or cursor_y + alto_necesario > 6.85:
                s_actual = _nueva_contenido(prs, mes_ano, num)
                _titulo_contenido(s_actual, "Trabajos Pendientes")
                num += 1
                cursor_y = 1.15
            _text(s_actual, 0.6, cursor_y, 8.0, 0.3, nombre.upper(), 12, C_GREEN, True)
            _add_table(s_actual, 0.6, cursor_y + 0.35, 12.1, COLS_PEND, rows, RATIOS_PEND, font_size=8, row_h=0.3)
            cursor_y += 0.5 + 0.32 + len(rows) * 0.30 + 0.25

    # 6) Cierre
    _cierre(prs)

    return prs
