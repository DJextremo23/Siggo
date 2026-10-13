"""Tarea programada: alerta diaria al administrador sobre habilitación de vacaciones.

Recorre a los fiscalizadores activos y notifica por correo a los administradores
cuando hay fiscalizadores habilitados (cumplieron su aniversario de servicio) o
próximos a habilitarse (dentro de los próximos días).
"""

import os
import time
import threading
import logging
from datetime import date

from conexion import conexion as _obtener_conexion
conexion = _obtener_conexion()
from correo import enviar_correo

# Logos incrustados en los correos de notificación
LOGO_SIGGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "img", "logo.png")
LOGO_SMALL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "ppt", "logo_small.jpg")

# Días de anticipación para considerar a un fiscalizador como "próximo"
DIAS_PREAVISO = 15

_logger = logging.getLogger(__name__)


def _formatear_fecha(valor):
    if isinstance(valor, date):
        return valor.strftime("%d/%m/%Y")
    return str(valor) if valor else "—"


def _obtener_fiscalizadores_alerta():
    """Devuelve (listos, próximos) según la antigüedad de cada fiscalizador activo."""
    cursor = conexion.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT
                CONCAT(u.nombre,' ',u.apellidos) AS nombre,
                DATE_ADD(u.fecha_ingreso, INTERVAL GREATEST(1, TIMESTAMPDIFF(YEAR, u.fecha_ingreso, CURDATE())) YEAR) AS fecha_vacaciones,
                DATEDIFF(
                    DATE_ADD(u.fecha_ingreso, INTERVAL GREATEST(1, TIMESTAMPDIFF(YEAR, u.fecha_ingreso, CURDATE())) YEAR),
                    CURDATE()
                ) AS dias_faltantes
            FROM usuarios u
            INNER JOIN usuarios_roles ur ON u.id_usuario = ur.id_usuario
            INNER JOIN roles r ON ur.id_rol = r.id_rol
            WHERE r.nombre_rol = 'fiscalizador'
              AND u.estado = 'activo'
            ORDER BY dias_faltantes ASC
        """)
        filas = cursor.fetchall()
    finally:
        cursor.close()

    listos = [f for f in filas if (f["dias_faltantes"] or 0) <= 0]
    proximos = [f for f in filas if 1 <= (f["dias_faltantes"] or 0) <= DIAS_PREAVISO]
    return listos, proximos


def _obtener_administradores():
    """Devuelve los administradores activos con correo válido."""
    cursor = conexion.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT DISTINCT u.correo, CONCAT(u.nombre,' ',u.apellidos) AS nombre
            FROM usuarios u
            INNER JOIN usuarios_roles ur ON u.id_usuario = ur.id_usuario
            INNER JOIN roles r ON ur.id_rol = r.id_rol
            WHERE r.nombre_rol = 'admin'
              AND u.estado = 'activo'
              AND u.correo IS NOT NULL
              AND u.correo <> ''
        """)
        return cursor.fetchall()
    finally:
        cursor.close()


def _filas_listos(listos):
    filas = ""
    for f in listos:
        filas += (
            '<tr>'
            f'<td style="padding:8px 0; font-size:14px; color:#0f172a;">{f["nombre"]}</td>'
            f'<td style="padding:8px 0; font-size:14px; color:#0f172a; text-align:right;">{_formatear_fecha(f["fecha_vacaciones"])}</td>'
            '</tr>'
        )
    return filas


def _filas_proximos(proximos):
    filas = ""
    for f in proximos:
        filas += (
            '<tr>'
            f'<td style="padding:8px 0; font-size:14px; color:#0f172a;">{f["nombre"]}</td>'
            f'<td style="padding:8px 0; font-size:14px; color:#0f172a; text-align:right;">{_formatear_fecha(f["fecha_vacaciones"])}</td>'
            f'<td style="padding:8px 0; font-size:14px; color:#0f172a; text-align:right;">En {f["dias_faltantes"]} día(s)</td>'
            '</tr>'
        )
    return filas


def _construir_cuerpo(nombre_admin, listos, proximos):
    """Construye el HTML del correo de alerta de vacaciones."""
    seccion_listos = ""
    if listos:
        seccion_listos = f"""
        <p style="margin:16px 0 6px; font-size:13px; font-weight:700; color:#0f172a;">Fiscalizadores habilitados para tomar vacaciones</p>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f8fafc; border:1px solid #e2e8f0; border-radius:8px;">
          <tr>
            <td style="padding:12px 16px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                {_filas_listos(listos)}
              </table>
            </td>
          </tr>
        </table>
        """

    seccion_proximos = ""
    if proximos:
        seccion_proximos = f"""
        <p style="margin:16px 0 6px; font-size:13px; font-weight:700; color:#0f172a;">Fiscalizadores próximos a habilitarse</p>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f8fafc; border:1px solid #e2e8f0; border-radius:8px;">
          <tr>
            <td style="padding:12px 16px;">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                {_filas_proximos(proximos)}
              </table>
            </td>
          </tr>
        </table>
        """

    return f"""
    <div style="margin:0; padding:0; background-color:#f1f5f9;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f1f5f9; padding:24px 0;">
        <tr>
          <td align="center">
            <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px; width:100%; background-color:#ffffff; border-radius:10px; overflow:hidden; border:1px solid #e2e8f0;">
              <tr>
                <td style="background-color:#1e3a8a; padding:20px 24px;">
                  <table role="presentation" cellpadding="0" cellspacing="0">
                    <tr>
                      <td style="vertical-align:middle;">
                        <img src="cid:logo_siggo" width="44" height="44" alt="SIGGO" style="display:block; border-radius:8px; background-color:#ffffff;">
                      </td>
                      <td style="vertical-align:middle; padding-left:12px;">
                        <img src="cid:logo_small" width="124" height="30" alt="" style="display:block;">
                      </td>
                    </tr>
                  </table>
                  <div style="font-family:'Segoe UI',Arial,sans-serif; color:#bfdbfe; font-size:12px; margin-top:10px;">Alerta de vacaciones</div>
                </td>
              </tr>
              <tr>
                <td style="padding:24px; font-family:'Segoe UI',Arial,sans-serif; color:#0f172a; line-height:1.6; font-size:14px;">
                  <p style="margin:0 0 12px;">Estimado(a) <strong>{nombre_admin}</strong>:</p>
                  <p style="margin:0 0 16px;">Se le informa sobre la habilitación de vacaciones de los fiscalizadores activos. A continuación se detallan los casos correspondientes:</p>
                  {seccion_listos}
                  {seccion_proximos}
                  <p style="margin:16px 0 0;">Para mayor detalle, puede consultar la información en el sistema SIGGO.</p>
                </td>
              </tr>
              <tr>
                <td style="background-color:#f8fafc; border-top:1px solid #e2e8f0; padding:16px 24px; font-family:'Segoe UI',Arial,sans-serif; font-size:12px; color:#64748b;">
                  <p style="margin:10px 0 0; font-size:11px; color:#94a3b8;">Este es un mensaje automático. No responda a este correo.</p>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
    </div>
    """


def _enviar_alerta_vacaciones():
    """Consulta la habilitación de vacaciones y notifica por correo a los administradores."""
    listos, proximos = _obtener_fiscalizadores_alerta()
    if not listos and not proximos:
        _logger.info("[ALERTA VACACIONES] Sin fiscalizadores habilitados o próximos; no se envía correo.")
        return

    administradores = _obtener_administradores()
    if not administradores:
        _logger.info("[ALERTA VACACIONES] No hay administradores con correo registrado; no se envía correo.")
        return

    asunto = "Alerta de vacaciones: fiscalizadores habilitados o próximos"

    for admin in administradores:
        cuerpo = _construir_cuerpo(admin["nombre"], listos, proximos)
        enviar_correo(
            admin["correo"],
            asunto,
            cuerpo,
            imagenes_inline=[(LOGO_SIGGO, "logo_siggo"), (LOGO_SMALL, "logo_small")]
        )


def _bucle(intervalo_seg, arranque_seg):
    if arranque_seg > 0:
        time.sleep(arranque_seg)
    while True:
        try:
            _enviar_alerta_vacaciones()
        except Exception as e:
            _logger.error("[ALERTA VACACIONES] Error en la ejecución: %s", e)
        time.sleep(intervalo_seg)


def iniciar_alerta_vacaciones_diaria():
    """Inicia la tarea en segundo plano que envía la alerta de vacaciones cada día."""
    intervalo = int(os.getenv("ALERTA_VACACIONES_INTERVALO_SEG", "86400"))
    arranque = int(os.getenv("ALERTA_VACACIONES_ARRANQUE_SEG", "60"))
    if intervalo <= 0:
        _logger.info("[ALERTA VACACIONES] Tarea deshabilitada (ALERTA_VACACIONES_INTERVALO_SEG <= 0).")
        return

    hilo = threading.Thread(
        target=_bucle,
        args=(intervalo, arranque),
        daemon=True,
        name="alerta-vacaciones",
    )
    hilo.start()
    _logger.info("[ALERTA VACACIONES] Tarea diaria iniciada (intervalo %ss, arranque %ss).", intervalo, arranque)
