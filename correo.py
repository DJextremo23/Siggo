"""Envío de correos electrónicos vía SMTP (Microsoft 365 / Exchange)."""

import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


def enviar_correo(destinatario, asunto, cuerpo_html):
    """Envía un correo vía SMTP de Microsoft 365 (mejor esfuerzo).

    Configuración mediante variables de entorno:
      SMTP_HOST      (por defecto smtp.office365.com)
      SMTP_PORT      (por defecto 587)
      SMTP_USER      (cuenta remitente)
      SMTP_PASSWORD  (contraseña de aplicación)
      SMTP_FROM      (remitente visible; si no se define usa SMTP_USER)

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

    try:
        mensaje = MIMEMultipart("alternative")
        mensaje["Subject"] = asunto
        mensaje["From"] = sender
        mensaje["To"] = destinatario
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
