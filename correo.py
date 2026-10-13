"""Envío de correos electrónicos vía SMTP (Microsoft 365 / Exchange)."""

import os
import smtplib
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


def enviar_correo(destinatario, asunto, cuerpo_html, imagenes_inline=None):
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

    imagenes = [(r, c) for r, c in (imagenes_inline or []) if r and os.path.exists(r)]

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
