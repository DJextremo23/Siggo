"""Tarea programada: alerta diaria al administrador sobre habilitación de vacaciones.

Recorre a los fiscalizadores activos y notifica por correo a los administradores
cuando hay fiscalizadores habilitados (cumplieron su aniversario de servicio) o
próximos a habilitarse (dentro de los próximos días).
"""

import os
import time
import threading
import logging
from datetime import date, datetime, timedelta

from conexion import conexion as _obtener_conexion
conexion = _obtener_conexion()
from correo import enviar_correo, enviar_correo_y_confirmar, construir_correo
from helpers import DIAS_ES, ZONA_HORARIA, hoy_local

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
            f'<td class="mail-list-cell">{f["nombre"]}</td>'
            f'<td class="mail-list-cell-right">{_formatear_fecha(f["fecha_vacaciones"])}</td>'
            '</tr>'
        )
    return filas


def _filas_proximos(proximos):
    filas = ""
    for f in proximos:
        filas += (
            '<tr>'
            f'<td class="mail-list-cell">{f["nombre"]}</td>'
            f'<td class="mail-list-cell-right">{_formatear_fecha(f["fecha_vacaciones"])}</td>'
            f'<td class="mail-list-cell-right">En {f["dias_faltantes"]} día(s)</td>'
            '</tr>'
        )
    return filas


def _construir_cuerpo(nombre_admin, listos, proximos):
    """Construye el HTML del correo de alerta de vacaciones."""
    seccion_listos = ""
    if listos:
        seccion_listos = f"""
        <p class="mail-section-title">Fiscalizadores habilitados para tomar vacaciones</p>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="mail-list">
          <tr>
            <td class="mail-list-pad">
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
        <p class="mail-section-title">Fiscalizadores próximos a habilitarse</p>
        <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="mail-list">
          <tr>
            <td class="mail-list-pad">
              <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                {_filas_proximos(proximos)}
              </table>
            </td>
          </tr>
        </table>
        """

    cuerpo = f"""
                  <p class="mail-greeting">Estimado(a) <strong>{nombre_admin}</strong>:</p>
                  <p class="mail-message">Se le informa sobre la habilitación de vacaciones de los fiscalizadores activos. A continuación se detallan los casos correspondientes:</p>
                  {seccion_listos}
                  {seccion_proximos}
                  <p class="mail-info">Para mayor detalle, puede consultar la información en el sistema SIGGO.</p>
    """
    pie = '<p class="mail-footer-note">Este es un mensaje automático. No responda a este correo.</p>'

    return construir_correo("Alerta de vacaciones", "mail-header-blue", "mail-subtitle-blue", cuerpo, pie)


def _reclamar_dia_vacaciones():
    hoy = hoy_local()
    cursor = conexion.cursor()
    try:
        cursor.execute("INSERT IGNORE INTO alertas_vacaciones_diarias (fecha) VALUES (%s)", (hoy,))
        conexion.commit()
        return cursor.rowcount == 1
    finally:
        cursor.close()


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

    if not _reclamar_dia_vacaciones():
        _logger.info("[ALERTA VACACIONES] La alerta de hoy ya fue enviada.")
        return

    asunto = "Alerta de vacaciones: fiscalizadores habilitados o próximos"

    for admin in administradores:
        cuerpo = _construir_cuerpo(admin["nombre"], listos, proximos)
        enviar_correo(admin["correo"], asunto, cuerpo)


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


_NOTAS_RECORDATORIO = {
    1: "Hoy tienes guardia; recuerda marcar tu asistencia.",
    2: "Aún no has marcado tu asistencia de hoy.",
    3: "Última oportunidad: tu guardia de ayer sigue sin marcar.",
}


def _obtener_horas_recordatorio():
    """Devuelve las horas (time) de los tres recordatorios desde variables de entorno."""
    def _hora(env, defecto):
        valor = os.getenv(env, defecto).strip()
        try:
            return datetime.strptime(valor, "%H:%M").time()
        except ValueError:
            return datetime.strptime(defecto, "%H:%M").time()

    return (
        _hora("ALERTA_ASISTENCIA_H1", "08:00"),
        _hora("ALERTA_ASISTENCIA_H2", "20:00"),
        _hora("ALERTA_ASISTENCIA_H3", "08:00"),
    )


def _recordatorio_correspondiente(fecha_guardia, ahora, h1, h2, h3):
    """Devuelve el número de recordatorio (1, 2 o 3) que corresponde ahora, o None."""
    if isinstance(fecha_guardia, datetime):
        fecha_guardia = fecha_guardia.date()

    hoy = ahora.date()
    hora_actual = ahora.time()

    if fecha_guardia == hoy:
        if hora_actual >= h2:
            return 2
        if hora_actual >= h1:
            return 1
        return None

    if fecha_guardia == hoy - timedelta(days=1):
        if hora_actual >= h3:
            return 3
        return None

    return None


def _obtener_pendientes_asistencia():
    """Devuelve las guardias sin asistencia dentro de la ventana de 48 h."""
    cursor = conexion.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT
                g.id_guardia,
                g.fecha_guardia,
                g.tipo,
                u.correo,
                CONCAT(u.nombre,' ',u.apellidos) AS nombre
            FROM guardias g
            INNER JOIN usuarios u ON g.id_usuario = u.id_usuario
            INNER JOIN usuarios_roles ur ON u.id_usuario = ur.id_usuario
            INNER JOIN roles r ON ur.id_rol = r.id_rol
            LEFT JOIN asistencia a ON g.id_guardia = a.id_guardia
            WHERE r.nombre_rol = 'fiscalizador'
              AND a.id_guardia IS NULL
              AND g.fecha_guardia BETWEEN (CURDATE() - INTERVAL 1 DAY) AND CURDATE()
              AND u.estado = 'activo'
              AND u.correo IS NOT NULL
              AND u.correo <> ''
        """)
        return cursor.fetchall()
    finally:
        cursor.close()


def _reclamar_recordatorio(id_guardia, numero):
    cursor = conexion.cursor()
    try:
        cursor.execute(
            "INSERT IGNORE INTO recordatorios_asistencia (id_guardia, numero_recordatorio) VALUES (%s, %s)",
            (id_guardia, numero),
        )
        conexion.commit()
        return cursor.rowcount == 1
    finally:
        cursor.close()


def _liberar_recordatorios(avisos):
    if not avisos:
        return
    cursor = conexion.cursor()
    try:
        for aviso in avisos:
            cursor.execute(
                "DELETE FROM recordatorios_asistencia WHERE id_guardia = %s AND numero_recordatorio = %s",
                (aviso["id_guardia"], aviso["numero"]),
            )
        conexion.commit()
    finally:
        cursor.close()


def _construir_cuerpo_recordatorio(nombre_fiscalizador, avisos):
    filas = ""
    for aviso in avisos:
        fecha = aviso["fecha"]
        if isinstance(fecha, datetime):
            fecha = fecha.date()
        fecha_str = fecha.strftime("%d/%m/%Y") if isinstance(fecha, date) else (str(fecha) if fecha else "—")
        dia = DIAS_ES[fecha.weekday()] if isinstance(fecha, date) else "—"
        tipo = "Soporte" if aviso["tipo"] == "soporte" else "Guardia"
        nota = _NOTAS_RECORDATORIO.get(aviso["numero"], "")
        filas += f"""
                  <tr>
                    <td class="mail-item">
                      <p class="mail-item-title">{fecha_str} ({dia})</p>
                      <p class="mail-item-note">{tipo} · {nota}</p>
                    </td>
                  </tr>
        """

    cuerpo = f"""
                  <p class="mail-greeting">Estimado(a) <strong>{nombre_fiscalizador}</strong>:</p>
                  <p class="mail-message">Te recordamos que tienes guardias pendientes de marcar asistencia:</p>
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
        {filas}
                  </table>
                  <p class="mail-info">Puedes marcar tu asistencia en el sistema SIGGO antes de que venza la ventana de registro.</p>
    """
    pie = '<p class="mail-footer-note">Este es un mensaje automático. No responda a este correo.</p>'

    return construir_correo("Recordatorio de asistencia", "mail-header-blue", "mail-subtitle-blue", cuerpo, pie)


def _enviar_recordatorios_asistencia():
    h1, h2, h3 = _obtener_horas_recordatorio()
    ahora = datetime.now(ZONA_HORARIA)

    pendientes = _obtener_pendientes_asistencia()
    if not pendientes:
        return

    avisos_por_correo = {}
    for p in pendientes:
        numero = _recordatorio_correspondiente(p["fecha_guardia"], ahora, h1, h2, h3)
        if numero is None:
            continue
        if not _reclamar_recordatorio(p["id_guardia"], numero):
            continue

        grupo = avisos_por_correo.setdefault(p["correo"], {"nombre": p["nombre"], "avisos": []})
        grupo["avisos"].append({
            "id_guardia": p["id_guardia"],
            "numero": numero,
            "fecha": p["fecha_guardia"],
            "tipo": p["tipo"],
        })

    for correo, grupo in avisos_por_correo.items():
        cuerpo = _construir_cuerpo_recordatorio(grupo["nombre"], grupo["avisos"])
        enviado = enviar_correo_y_confirmar(correo, "Recordatorio: marca tu asistencia de guardia", cuerpo)
        if not enviado:
            _liberar_recordatorios(grupo["avisos"])


def _bucle_asistencia(intervalo_seg, arranque_seg):
    if arranque_seg > 0:
        time.sleep(arranque_seg)
    while True:
        try:
            _enviar_recordatorios_asistencia()
        except Exception as e:
            _logger.error("[ALERTA ASISTENCIA] Error en recordatorios: %s", e)
        try:
            _registrar_no_marcadas()
            _procesar_pendientes_notificaciones()
        except Exception as e:
            _logger.error("[ALERTA ASISTENCIA] Error en avisos al administrador: %s", e)
        time.sleep(intervalo_seg)


def iniciar_alerta_asistencia_diaria():
    """Inicia la tarea en segundo plano que envía recordatorios de asistencia."""
    intervalo = int(os.getenv("ALERTA_ASISTENCIA_INTERVALO_SEG", "1800"))
    arranque = int(os.getenv("ALERTA_ASISTENCIA_ARRANQUE_SEG", "60"))
    if intervalo <= 0:
        _logger.info("[ALERTA ASISTENCIA] Tarea deshabilitada (ALERTA_ASISTENCIA_INTERVALO_SEG <= 0).")
        return

    hilo = threading.Thread(
        target=_bucle_asistencia,
        args=(intervalo, arranque),
        daemon=True,
        name="alerta-asistencia",
    )
    hilo.start()
    _logger.info("[ALERTA ASISTENCIA] Tarea iniciada (intervalo %ss, arranque %ss).", intervalo, arranque)


_ACCIONES_ADMIN_ASISTENCIA = {
    "marcada": {
        "titulo": "Fiscalizador marcó su asistencia",
        "clase_header": "mail-header-blue",
        "clase_subtitulo": "mail-subtitle-blue",
        "mensaje": "Se le informa que un fiscalizador marcó su asistencia. A continuación se detallan los datos:",
    },
    "no_marcada": {
        "titulo": "Asistencia no registrada (plazo vencido)",
        "clase_header": "mail-header-red",
        "clase_subtitulo": "mail-subtitle-red",
        "mensaje": "Se le informa que un fiscalizador no registró su asistencia dentro del plazo. Por favor revise y registre la asistencia correspondiente:",
    },
}


def _construir_cuerpo_admin_asistencia(nombre_admin, cfg, fiscalizador_nombre, fecha_guardia, tipo):
    fecha = fecha_guardia
    if isinstance(fecha, datetime):
        fecha = fecha.date()
    fecha_str = fecha.strftime("%d/%m/%Y") if isinstance(fecha, date) else (str(fecha) if fecha else "—")
    dia = DIAS_ES[fecha.weekday()] if isinstance(fecha, date) else "—"
    tipo_str = "Soporte" if tipo == "soporte" else "Guardia"

    cuerpo = f"""
                  <p class="mail-greeting">Estimado(a) <strong>{nombre_admin}</strong>:</p>
                  <p class="mail-message">{cfg['mensaje']}</p>
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="mail-detail">
                    <tr>
                      <td class="mail-detail-pad">
                        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                          <tr><td class="mail-label">Fiscalizador</td></tr>
                          <tr><td class="mail-value">{fiscalizador_nombre}</td></tr>
                          <tr><td class="mail-label">Fecha de la guardia</td></tr>
                          <tr><td class="mail-value">{fecha_str}</td></tr>
                          <tr><td class="mail-label">Día de la guardia</td></tr>
                          <tr><td class="mail-value">{dia}</td></tr>
                          <tr><td class="mail-label">Tipo de guardia</td></tr>
                          <tr><td class="mail-value-last">{tipo_str}</td></tr>
                        </table>
                      </td>
                    </tr>
                  </table>
                  <p class="mail-info">Para mayor detalle, puede consultar la información en el sistema SIGGO.</p>
    """
    pie = '<p class="mail-footer-note">Este es un mensaje automático. No responda a este correo.</p>'

    return construir_correo(cfg["titulo"], cfg["clase_header"], cfg["clase_subtitulo"], cuerpo, pie)


def notificar_admins_asistencia(id_guardia, tipo):
    """Registra el aviso (una sola vez) e intenta enviarlo de inmediato."""
    if tipo not in ("marcada", "no_marcada"):
        return

    try:
        id_guardia = int(id_guardia)
    except (TypeError, ValueError):
        return

    cursor = conexion.cursor()
    try:
        cursor.execute(
            "INSERT IGNORE INTO notificaciones_asistencia_admin (id_guardia, tipo) VALUES (%s, %s)",
            (id_guardia, tipo),
        )
        conexion.commit()
    finally:
        cursor.close()

    _intentar_enviar_notificacion_asistencia(id_guardia, tipo)


def _intentar_enviar_notificacion_asistencia(id_guardia, tipo):
    cursor = conexion.cursor(dictionary=True)
    try:
        cursor.execute(
            "SELECT enviado FROM notificaciones_asistencia_admin WHERE id_guardia = %s AND tipo = %s",
            (id_guardia, tipo),
        )
        fila = cursor.fetchone()
        if not fila or fila["enviado"]:
            return

        cursor.execute("""
            SELECT g.fecha_guardia, g.tipo, CONCAT(u.nombre,' ',u.apellidos) AS nombre
            FROM guardias g
            INNER JOIN usuarios u ON g.id_usuario = u.id_usuario
            WHERE g.id_guardia = %s
        """, (id_guardia,))
        info = cursor.fetchone()
    finally:
        cursor.close()

    if not info:
        return

    administradores = _obtener_administradores()
    if not administradores:
        return

    cfg = _ACCIONES_ADMIN_ASISTENCIA[tipo]
    exito = False
    for admin in administradores:
        cuerpo = _construir_cuerpo_admin_asistencia(
            admin["nombre"], cfg, info["nombre"], info["fecha_guardia"], info["tipo"]
        )
        if enviar_correo_y_confirmar(admin["correo"], cfg["titulo"], cuerpo):
            exito = True

    if exito:
        cursor = conexion.cursor()
        try:
            cursor.execute(
                "UPDATE notificaciones_asistencia_admin SET enviado = 1, enviado_en = NOW() WHERE id_guardia = %s AND tipo = %s",
                (id_guardia, tipo),
            )
            conexion.commit()
        finally:
            cursor.close()


def _registrar_no_marcadas():
    dias_retro = int(os.getenv("ALERTA_ASISTENCIA_NO_MARCADA_DIAS", "7"))
    hoy = hoy_local()
    hasta = hoy - timedelta(days=2)
    desde = hoy - timedelta(days=dias_retro)

    cursor = conexion.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT g.id_guardia
            FROM guardias g
            INNER JOIN usuarios u ON g.id_usuario = u.id_usuario
            INNER JOIN usuarios_roles ur ON u.id_usuario = ur.id_usuario
            INNER JOIN roles r ON ur.id_rol = r.id_rol
            LEFT JOIN asistencia a ON g.id_guardia = a.id_guardia
            WHERE r.nombre_rol = 'fiscalizador'
              AND a.id_guardia IS NULL
              AND g.fecha_guardia BETWEEN %s AND %s
              AND u.estado = 'activo'
        """, (desde, hasta))
        ids = [fila["id_guardia"] for fila in cursor.fetchall()]
    finally:
        cursor.close()

    cursor = conexion.cursor()
    try:
        for id_guardia in ids:
            cursor.execute(
                "INSERT IGNORE INTO notificaciones_asistencia_admin (id_guardia, tipo) VALUES (%s, 'no_marcada')",
                (id_guardia,),
            )
        conexion.commit()
    finally:
        cursor.close()


def _procesar_pendientes_notificaciones():
    cursor = conexion.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id_guardia, tipo FROM notificaciones_asistencia_admin WHERE enviado = 0")
        pendientes = cursor.fetchall()
    finally:
        cursor.close()

    for p in pendientes:
        _intentar_enviar_notificacion_asistencia(p["id_guardia"], p["tipo"])
