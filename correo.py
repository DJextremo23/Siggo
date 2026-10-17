"""Envío de correos electrónicos vía SMTP (Microsoft 365 / Exchange)."""

import os
import smtplib
import threading
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage

# Subtype MIME según la extensión del archivo de imagen
_EXT_SUBTYPE = {
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".png": "png",
    ".gif": "gif",
    ".webp": "webp",
}

_BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LOGO_SIGGO = os.path.join(_BASE_DIR, "static", "img", "logo.png")
LOGO_OIG = os.path.join(_BASE_DIR, "static", "img", "logo-oig.png")
IMAGENES_CORREO = [(LOGO_SIGGO, "logo_siggo"), (LOGO_OIG, "logo_oig")]

_CSS_CORREO_PATH = os.path.join(_BASE_DIR, "static", "correo.css")
_CSS_CORREO = None


def _css_correo():
    global _CSS_CORREO
    if _CSS_CORREO is None:
        try:
            with open(_CSS_CORREO_PATH, "r", encoding="utf-8") as archivo:
                _CSS_CORREO = archivo.read()
        except OSError:
            _CSS_CORREO = ""
    return _CSS_CORREO


def construir_correo(titulo, clase_header, clase_subtitulo, cuerpo, pie):
    """Devuelve el HTML completo de un correo con el CSS y los logos ya embebidos."""
    css = _css_correo()
    return f"""<!DOCTYPE html>
<html>
  <head>
    <meta charset="utf-8">
    <style>{css}</style>
  </head>
  <body style="margin:0;padding:0;">
    <div class="mail-wrap">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="mail-table">
        <tr>
          <td align="center">
            <table role="presentation" width="600" cellpadding="0" cellspacing="0" class="mail-card">
              <tr>
                <td class="mail-header {clase_header}">
                  <table role="presentation" cellpadding="0" cellspacing="0">
                    <tr>
                      <td class="mail-logo-cell"><img src="cid:logo_siggo" width="44" height="44" alt="SIGGO" class="mail-logo-siggo"></td>
                      <td class="mail-logo-cell-pad"><img src="cid:logo_oig" width="124" height="30" alt="OIG" class="mail-logo-oig"></td>
                    </tr>
                  </table>
                  <div class="mail-subtitle {clase_subtitulo}">{titulo}</div>
                </td>
              </tr>
              <tr>
                <td class="mail-body">{cuerpo}</td>
              </tr>
              <tr>
                <td class="mail-footer">{pie}</td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
    </div>
  </body>
</html>"""


def enviar_correo_y_confirmar(destinatario, asunto, cuerpo_html, imagenes_inline=None):
    """Envía un correo vía SMTP de Microsoft 365 (mejor esfuerzo).

    Configuración mediante variables de entorno:
      SMTP_HOST      (por defecto smtp.office365.com)
      SMTP_PORT      (por defecto 587)
      SMTP_USER      (cuenta remitente)
      SMTP_PASSWORD  (contraseña de aplicación)
      SMTP_FROM      (remitente visible; si no se define usa SMTP_USER)

    imagenes_inline: lista opcional de tuplas (ruta, cid). Cada imagen existente
    se incrusta como imagen inline (Content-ID) y se referencia en el HTML con
    cid:<cid>.

    Nunca lanza excepciones: si algo falla, solo registra el error y retorna False.
    """
    host = os.getenv("SMTP_HOST", "smtp.office365.com")
    port = int(os.getenv("SMTP_PORT", "587"))
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "")
    sender = (os.getenv("SMTP_FROM", "") or user).strip()

    if not user or not password or not destinatario:
        print("[EMAIL] Configuración SMTP incompleta; no se envió el correo.")
        return False

    if imagenes_inline is None:
        imagenes_inline = IMAGENES_CORREO

    imagenes = [(r, c) for r, c in imagenes_inline if r and os.path.exists(r)]

    try:
        if imagenes:
            mensaje = MIMEMultipart("related")
        else:
            mensaje = MIMEMultipart("alternative")
        mensaje["Subject"] = asunto
        mensaje["From"] = sender
        mensaje["To"] = destinatario

        if imagenes:
            alternativa = MIMEMultipart("alternative")
            mensaje.attach(alternativa)
            alternativa.attach(MIMEText(cuerpo_html, "html", "utf-8"))

            for ruta, cid in imagenes:
                ext = os.path.splitext(ruta)[1].lower()
                subtype = _EXT_SUBTYPE.get(ext, "octet-stream")
                with open(ruta, "rb") as f:
                    imagen = MIMEImage(f.read(), _subtype=subtype)
                imagen.add_header("Content-ID", f"<{cid}>")
                imagen.add_header("Content-Disposition", "inline", filename=os.path.basename(ruta))
                mensaje.attach(imagen)
        else:
            mensaje.attach(MIMEText(cuerpo_html, "html", "utf-8"))

        with smtplib.SMTP(host, port, timeout=15) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(user, password)
            server.sendmail(sender, [destinatario], mensaje.as_string())

        return True
    except Exception as e:
        print(f"[EMAIL] Error al enviar correo a {destinatario}: {e}")
        return False


def enviar_correo(destinatario, asunto, cuerpo_html, imagenes_inline=None):
    """Envía el correo en un hilo en segundo plano (no bloquea la petición)."""
    threading.Thread(
        target=enviar_correo_y_confirmar,
        args=(destinatario, asunto, cuerpo_html, imagenes_inline),
        daemon=True,
    ).start()
