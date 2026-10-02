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
def _portada(prs, informe, titulo=None, etiqueta=None, contenido=None):
    s = prs.slides.add_slide(prs.slide_layouts[6])
    _set_bg(s, C_NAVY)

    _pic(s, FONDO, 1.37, 0.0, 11.96, 7.5)
    _pic(s, LOGO_PORTADA, 0.56, 0.49, 1.89, 0.41)

    supervisor = (informe.get("fiscalizador") or "").strip().upper()
    _text(s, 0.70, 2.05, 6.90, 0.28, f"SUPERVISOR. {supervisor}" if supervisor else "SUPERVISOR.",
          11, C_GREEN, True)

    _text(s, 0.65, 2.42, 8.90, 0.72, titulo or "Reporte de Guardia", 35, C_WHITE, True, font=FONT_TITLE)

    fecha_str = _fmt_fecha(informe.get("fecha_guardia"))
    _text(s, 0.70, 3.28, 4.5, 0.35, fecha_str, 17, C_LIGHT)

    card = _rrect(s, 0.70, 4.25, 8.30, 1.45, C_CARD_BG, C_CARD_BR, line_w=0.5)
    _fill_opacity(card, 88)
    _line_opacity(card, 65)

    _text(s, 1.00, 4.45, 4.0, 0.25, etiqueta or "Talleres supervisados", 11, C_GREEN, True, anchor=MSO_ANCHOR.MIDDLE)

    tb_tall, tf_tall = _text(s, 1.00, 4.85, 7.65, 0.55, contenido or TALLERES_SUPERVISADOS,
                             14, C_WHITE, anchor=MSO_ANCHOR.MIDDLE)
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


def _incidente(slide, x, y, w, evidencia, foto_bytes):
    """Dibuja un incidente: foto + fecha, lugar, ¿qué pasó? y descripción."""
    card_h = 2.3
    _rrect(slide, x, y, w, card_h, C_WHITE, C_SEP, radius=0.05)

    foto_w = 2.8
    foto_h = 1.9
    if foto_bytes:
        try:
            _add_picture_fitted(slide, foto_bytes, x + 0.15, y + 0.2, foto_w, foto_h)
        except Exception:
            _rrect(slide, x + 0.15, y + 0.2, foto_w, foto_h, C_ZEBRA, C_SEP, radius=0.05)
    else:
        _rrect(slide, x + 0.15, y + 0.2, foto_w, foto_h, C_ZEBRA, C_SEP, radius=0.05)

    tx = x + 0.15 + foto_w + 0.25
    tw = x + w - 0.15 - tx
    fecha, lugar, que_paso, descripcion = _campo_evidencia(evidencia)
    campos = [
        ("Fecha", fecha),
        ("Lugar", lugar),
        ("¿Qué pasó?", que_paso),
        ("Descripción de lo sucedido", descripcion),
    ]
    cy = y + 0.12
    for lbl, val in campos:
        _text(slide, tx, cy, 1.7, 0.25, lbl + ":", 9, C_TEAL, True)
        _text(slide, tx + 1.7, cy, tw - 1.7, 0.45, val if val else "—", 9, C_TEXT, wrap=True)
        cy += 0.5
    return card_h


# ── Generador principal ─────────────────────────────────────────────────────
def _as_list(v):
    return v if isinstance(v, list) else []


def _as_dict(v):
    return v if isinstance(v, dict) else {}


def _campo_evidencia(ev):
    """Devuelve (fecha, lugar, que_paso, descripcion) normalizados de una evidencia,
    soportando el esquema actual y el anterior (tipo/fecha/lugar/que_paso/descripcion)."""
    if not isinstance(ev, dict):
        ev = {}
    es_viejo = ("que_paso" in ev or "lugar" in ev or "fecha" in ev) \
        and "pozo_equipo" not in ev and "hallazgos" not in ev
    if es_viejo:
        return (ev.get("fecha", ""), ev.get("lugar", ""),
                ev.get("que_paso", ""), ev.get("descripcion", ""))
    return (ev.get("fecha_hora", ""), ev.get("pozo_equipo", ""),
            ev.get("descripcion", ""), ev.get("hallazgos", ""))


def _imagen_por_indice(imagenes_archivo, indice):
    """Devuelve los bytes de la imagen en la posición `indice` (1-based) o None."""
    if indice is None:
        return None
    try:
        i = int(indice) - 1
    except (TypeError, ValueError):
        return None
    if 0 <= i < len(imagenes_archivo):
        return imagenes_archivo[i].get("bytes")
    return None


# ── Renderizadores genéricos de secciones (el PPT es dirigido por el contenido) ──
def _render_parrafos(prs, mes_ano, num, titulo, parrafos):
    parrafos = _as_list(parrafos)
    if not parrafos:
        return num
    s = _nueva_contenido(prs, mes_ano, num)
    _titulo_contenido(s, titulo)
    num += 1
    y = 1.35
    for p in parrafos:
        p = str(p)
        n_lineas = max(1, (len(p) // 110) + 1)
        alto = n_lineas * 0.30 + 0.15
        if y + alto > 6.9:
            s = _nueva_contenido(prs, mes_ano, num)
            _titulo_contenido(s, titulo)
            num += 1
            y = 1.35
        _text(s, 0.6, y, 12.1, alto, p, 13, C_TEXT, wrap=True, spacing=1.15)
        y += alto + 0.1
    return num


def _render_lista(prs, mes_ano, num, titulo, items):
    items = _as_list(items)
    if not items:
        return num
    s = _nueva_contenido(prs, mes_ano, num)
    _titulo_contenido(s, titulo)
    num += 1
    y = 1.35
    for it in items:
        it = str(it)
        n_lineas = max(1, (len(it) // 100) + 1)
        alto = n_lineas * 0.28 + 0.15
        if y + alto > 6.9:
            s = _nueva_contenido(prs, mes_ano, num)
            _titulo_contenido(s, titulo)
            num += 1
            y = 1.35
        _text(s, 0.8, y, 0.25, 0.3, "•", 13, C_TEAL, True)
        _text(s, 1.1, y, 11.5, alto, it, 13, C_TEXT, wrap=True, spacing=1.1)
        y += alto + 0.08
    return num


def _render_tabla(prs, mes_ano, num, titulo, columnas, filas):
    columnas = [str(c) for c in _as_list(columnas)]
    filas = _as_list(filas)
    if not columnas or not filas:
        return num
    filas = [[str(v) if v is not None else "" for v in _as_list(f)] for f in filas]
    ratios = [1.0] * len(columnas)
    filas_por_pagina = 14
    chunks = [filas[i:i + filas_por_pagina] for i in range(0, len(filas), filas_por_pagina)]
    for chunk in chunks:
        s = _nueva_contenido(prs, mes_ano, num)
        _titulo_contenido(s, titulo)
        num += 1
        _add_table(s, 0.6, 1.35, 12.1, columnas, chunk, ratios, font_size=8, row_h=0.3)
    return num


def _render_indicadores(prs, mes_ano, num, titulo, indicadores):
    indicadores = _as_list(indicadores)
    if not indicadores:
        return num
    por_slide = 4
    chunks = [indicadores[i:i + por_slide] for i in range(0, len(indicadores), por_slide)]
    for chunk in chunks:
        s = _nueva_contenido(prs, mes_ano, num)
        _titulo_contenido(s, titulo)
        num += 1
        for i, ind in enumerate(chunk):
            ind = _as_dict(ind)
            col = i % 2
            fila = i // 2
            x = 0.6 + col * 6.2
            y = 1.6 + fila * 1.7
            nombre = ind.get("nombre") or "Indicador"
            valor = ind.get("valor") or "—"
            unidad = ind.get("unidad") or ""
            _rrect(s, x, y, 5.9, 1.25, C_ZEBRA, C_SEP, radius=0.08)
            _text(s, x + 0.2, y + 0.2, 5.5, 0.3, nombre, 10, C_MUTED, True)
            _text(s, x + 0.2, y + 0.55, 5.5, 0.4, f"{valor} {unidad}".strip(), 18, C_NAVY, True)
    return num


def _render_evidencias(prs, mes_ano, num, titulo, evidencias, imagenes_archivo):
    evidencias = _as_list(evidencias)
    if not evidencias:
        return num

    # Agrupar por el tipo/clasificación real de cada evidencia (preservando orden).
    grupos = {}
    for ev in evidencias:
        ev = _as_dict(ev)
        tipo = (ev.get("tipo") or "").strip()
        if not tipo:
            tipo = "Alerta de Seguridad"
        grupos.setdefault(tipo, []).append(ev)

    s = _nueva_contenido(prs, mes_ano, num)
    _titulo_contenido(s, titulo)
    num += 1
    cursor_y = 1.15
    for tipo in grupos:
        items = grupos[tipo]
        need_subtitulo = 0.55 + 2.4 * len(items)
        if cursor_y + need_subtitulo > 6.85:
            s = _nueva_contenido(prs, mes_ano, num)
            _titulo_contenido(s, titulo)
            num += 1
            cursor_y = 1.15
        _rrect(s, 0.6, cursor_y, 12.1, 0.4, C_NAVY, None, radius=0.06)
        _text(s, 0.75, cursor_y + 0.05, 11.8, 0.3, tipo.upper(), 11, C_WHITE, True, anchor=MSO_ANCHOR.MIDDLE)
        cursor_y += 0.55
        for ev in items:
            foto = _imagen_por_indice(imagenes_archivo, ev.get("imagen_indice"))
            if cursor_y + 2.4 > 6.85:
                s = _nueva_contenido(prs, mes_ano, num)
                _titulo_contenido(s, titulo)
                num += 1
                cursor_y = 1.15
            _incidente(s, 0.6, cursor_y, 12.1, ev, foto)
            cursor_y += 2.4
    return num


def _render_seccion(prs, mes_ano, num, seccion, imagenes_archivo):
    seccion = _as_dict(seccion)
    titulo = (seccion.get("titulo") or "Sección").strip() or "Sección"
    tipo = (seccion.get("tipo") or "parrafos").strip().lower()
    if tipo == "tabla":
        return _render_tabla(prs, mes_ano, num, titulo,
                             seccion.get("columnas"), seccion.get("filas"))
    if tipo == "indicadores":
        return _render_indicadores(prs, mes_ano, num, titulo, seccion.get("indicadores"))
    if tipo == "lista":
        return _render_lista(prs, mes_ano, num, titulo, seccion.get("items"))
    if tipo == "evidencias":
        return _render_evidencias(prs, mes_ano, num, titulo,
                                  seccion.get("evidencias"), imagenes_archivo)
    return _render_parrafos(prs, mes_ano, num, titulo, seccion.get("parrafos"))


def _render_secciones(prs, mes_ano, num, secciones, imagenes_archivo):
    for sec in secciones:
        num = _render_seccion(prs, mes_ano, num, sec, imagenes_archivo)
    return num


def _secciones_desde_registro(reg):
    """Construye una lista de `secciones` dinámicas a partir de `registro_guardia`.

    Se usa cuando el resultado (por ejemplo, cacheado con un prompt anterior) no
    trae `secciones`: se convierte la transcripción fiel en secciones para poder
    renderizar por la MISMA ruta dinámica, sin requerir re-análisis manual."""
    secciones = []
    cabecera = _as_dict(reg.get("cabecera"))
    talleres = _as_list(reg.get("talleres"))
    evidencias = _as_list(reg.get("evidencias"))
    pendientes_reg = _as_list(reg.get("pendientes"))

    cabecera_items = [
        ("Producción OIL (BPD)", cabecera.get("produccion_oil_bpd")),
        ("Prod. Perdida imputada", cabecera.get("prod_perdida_imputada")),
        ("Producción GAS (MPC)", cabecera.get("produccion_gas_mpc")),
        ("Producción recuperada", cabecera.get("produccion_recuperada")),
    ]
    indicadores = [{"nombre": lbl, "valor": val, "unidad": ""}
                   for lbl, val in cabecera_items if val not in (None, "")]
    if indicadores:
        secciones.append({"titulo": "Producción", "tipo": "indicadores", "indicadores": indicadores})

    if evidencias:
        secciones.append({"titulo": "Seguridad y Medio Ambiente", "tipo": "evidencias", "evidencias": evidencias})

    campos_taller = ["item", "pozo", "bateria", "produccion_bopd", "requerimiento", "estado",
                     "fecha_ejecucion", "tipo", "relevante", "cuadrilla", "actividad_ejecutada"]
    head_taller = ["Ítem", "Pozo", "Batería", "Prod (bopd)", "Requerimiento", "Estado",
                   "Fecha", "Tipo", "Relev.", "Cuadrilla", "Actividad Ejecutada"]
    for t in talleres:
        t = _as_dict(t)
        filas = _as_list(t.get("filas"))
        if not filas:
            continue
        nombre = t.get("taller") or "Taller"
        rows = [[_as_dict(f).get(c, "") for c in campos_taller] for f in filas]
        secciones.append({"titulo": nombre, "tipo": "tabla", "columnas": head_taller, "filas": rows})

    campos_pend = ["item", "pozo", "bateria", "produccion_bopd", "requerimiento", "estado",
                   "fecha_ejecucion", "actividad_ejecutada"]
    head_pend = ["Ítem", "Pozo", "Batería", "Prod (bopd)", "Requerimiento", "Estado", "Fecha", "Actividad Ejecutada"]
    for p in pendientes_reg:
        p = _as_dict(p)
        filas = _as_list(p.get("filas"))
        if not filas:
            continue
        nombre = p.get("taller") or "Pendientes"
        rows = [[_as_dict(f).get(c, "") for c in campos_pend] for f in filas]
        secciones.append({"titulo": f"{nombre} — Pendientes", "tipo": "tabla", "columnas": head_pend, "filas": rows})

    return secciones


def generar_informe_ppt(informe, data, imagenes_archivo=None):
    """Construye la presentación con el formato rv0.

    El PPT es dirigido por `data.secciones` (contenido estructurado que entrega
    Gemini). Si `secciones` no existe (resultado cacheado previo), se deriva
    automáticamente de `registro_guardia`. La identidad visual es siempre la misma."""
    imagenes_archivo = imagenes_archivo or []
    data = data or {}

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)

    reg = _as_dict(data.get("registro_guardia"))
    portada = _as_dict(data.get("portada"))
    secciones = _as_list(data.get("secciones"))

    mes_ano = _fmt_mes_ano(informe.get("fecha_guardia"))

    # Datos dinámicos de la portada (sin contenido fijo del informe de ejemplo).
    titulo = portada.get("titulo") or "Reporte de Guardia"
    etiqueta = portada.get("etiqueta") or "Talleres supervisados"
    temas = _as_list(portada.get("temas"))
    if not temas:
        nombres = []
        for t in _as_list(reg.get("talleres")):
            n = (_as_dict(t).get("taller") or "").strip()
            if n and n not in nombres:
                nombres.append(n)
        for p in _as_list(reg.get("pendientes")):
            n = (_as_dict(p).get("taller") or "").strip()
            if n and n not in nombres:
                nombres.append(n)
        temas = nombres
    contenido_portada = " · ".join(temas) if temas else TALLERES_SUPERVISADOS

    _portada(prs, informe, titulo, etiqueta, contenido_portada)

    if not secciones:
        secciones = _secciones_desde_registro(reg)

    num = _render_secciones(prs, mes_ano, 2, secciones, imagenes_archivo)

    _cierre(prs)

    return prs
