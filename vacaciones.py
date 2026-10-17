"""Rutas de gestión de vacaciones (admin y fiscalizador)."""

from flask import render_template, request, session, redirect, url_for, flash
from conexion import conexion as _obtener_conexion
conexion = _obtener_conexion()
from limiter_instance import limiter
from utils import (
    acceso_no_autorizado, datos_invalidos, no_encontrado, error_interno,
    guardar_filtros, redirigir_con_filtros, registrar_auditoria,
)
from correo import enviar_correo, construir_correo
from helpers import balance_vacaciones_fifo
import mysql.connector.errors
from datetime import datetime, date

_ACCIONES_VACACIONES = {
    "registrada": {
        "titulo": "Vacaciones registradas",
        "clase_header": "mail-header-blue",
        "clase_subtitulo": "mail-subtitle-blue",
        "mensaje": "Se le informa que se han registrado vacaciones a su nombre. A continuación se detallan los datos correspondientes:",
        "footer_label": "Registrado por",
    },
    "actualizada": {
        "titulo": "Vacaciones actualizadas",
        "clase_header": "mail-header-orange",
        "clase_subtitulo": "mail-subtitle-orange",
        "mensaje": "Se le informa que se han actualizado sus vacaciones. A continuación se detallan los datos correspondientes:",
        "footer_label": "Actualizado por",
    },
    "eliminada": {
        "titulo": "Vacaciones eliminadas",
        "clase_header": "mail-header-red",
        "clase_subtitulo": "mail-subtitle-red",
        "mensaje": "Se le informa que se han eliminado sus vacaciones. A continuación se detallan los datos correspondientes:",
        "footer_label": "Eliminado por",
    },
}


def _datos_admin(cursor):
    """Obtiene nombre y correo del administrador con sesión activa (mejor esfuerzo)."""
    cursor.execute("""
        SELECT correo, CONCAT(nombre,' ',apellidos) AS nombre_completo
        FROM usuarios
        WHERE id_usuario = %s
    """, (session.get("id_usuario"),))
    info = cursor.fetchone()
    if info:
        return info["nombre_completo"] or "Administración", info["correo"] or ""
    return "Administración", ""


def _enviar_correo_vacacion(destinatario, nombre_completo, fecha_inicio, fecha_fin,
                            accion, admin_nombre="", admin_correo=""):
    """Notifica al fiscalizador vacaciones registradas/actualizadas/eliminadas (mejor esfuerzo)."""
    if not destinatario:
        return

    cfg = _ACCIONES_VACACIONES.get(accion, _ACCIONES_VACACIONES["registrada"])
    asunto = cfg["titulo"]

    fecha_inicio_str = fecha_inicio.strftime("%d/%m/%Y") if isinstance(fecha_inicio, date) else (str(fecha_inicio) if fecha_inicio else "—")
    fecha_fin_str = fecha_fin.strftime("%d/%m/%Y") if isinstance(fecha_fin, date) else (str(fecha_fin) if fecha_fin else "—")

    if isinstance(fecha_inicio, date) and isinstance(fecha_fin, date):
        dias = (fecha_fin - fecha_inicio).days + 1
    else:
        dias = "—"

    cuerpo = f"""
                  <p class="mail-greeting">Estimado(a) <strong>{nombre_completo}</strong>:</p>
                  <p class="mail-message">{cfg['mensaje']}</p>
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="mail-detail">
                    <tr>
                      <td class="mail-detail-pad">
                        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                          <tr><td class="mail-label">Fecha de inicio</td></tr>
                          <tr><td class="mail-value">{fecha_inicio_str}</td></tr>
                          <tr><td class="mail-label">Fecha de fin</td></tr>
                          <tr><td class="mail-value">{fecha_fin_str}</td></tr>
                          <tr><td class="mail-label">Total de días</td></tr>
                          <tr><td class="mail-value-last">{dias}</td></tr>
                        </table>
                      </td>
                    </tr>
                  </table>
                  <p class="mail-info">Para mayor detalle, puede consultar la información en el sistema SIGGO.</p>
    """
    pie = f"""
                  <p class="mail-footer-label">{cfg['footer_label']}: <strong class="mail-footer-name">{admin_nombre}</strong></p>
                  <p class="mail-footer-email">{admin_correo}</p>
                  <p class="mail-footer-note">Este es un mensaje automático. No responda a este correo.</p>
    """

    cuerpo_html = construir_correo(cfg["titulo"], cfg["clase_header"], cfg["clase_subtitulo"], cuerpo, pie)
    enviar_correo(destinatario, asunto, cuerpo_html)


_MAX_LISTAR_ELIMINADAS = 20


def _enviar_correo_vacaciones_eliminadas(destinatario, nombre_completo, vacaciones,
                                         admin_nombre="", admin_correo=""):
    """Notifica al fiscalizador la eliminación masiva de sus vacaciones (un solo correo)."""
    if not destinatario:
        return

    ordenadas = sorted(vacaciones, key=lambda v: v["inicio"])
    total = len(ordenadas)

    fecha_min = ordenadas[0]["inicio"]
    fecha_max = ordenadas[-1]["fin"]

    total_dias = 0
    for v in ordenadas:
        if isinstance(v["inicio"], date) and isinstance(v["fin"], date):
            total_dias += (v["fin"] - v["inicio"]).days + 1

    def _fmt(fecha):
        if isinstance(fecha, datetime):
            fecha = fecha.date()
        return fecha.strftime("%d/%m/%Y") if isinstance(fecha, date) else (str(fecha) if fecha else "—")

    listado = ""
    if total <= _MAX_LISTAR_ELIMINADAS:
        for v in ordenadas:
            dias = (v["fin"] - v["inicio"]).days + 1 if isinstance(v["inicio"], date) and isinstance(v["fin"], date) else "—"
            listado += f"""
                        <tr>
                          <td class="mail-list-cell">{_fmt(v['inicio'])}</td>
                          <td class="mail-list-cell">{_fmt(v['fin'])}</td>
                          <td class="mail-list-cell-right">{dias}</td>
                        </tr>
        """

    seccion_listado = ""
    if listado:
        seccion_listado = f"""
                  <p class="mail-section-title">Detalle de las vacaciones eliminadas</p>
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="mail-list">
                    <tr>
                      <td class="mail-list-pad">
                        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                          <tr>
                            <td class="mail-list-head">Inicio</td>
                            <td class="mail-list-head">Fin</td>
                            <td class="mail-list-head-right">Días</td>
                          </tr>
        {listado}
                        </table>
                      </td>
                    </tr>
                  </table>
        """

    cuerpo = f"""
                  <p class="mail-greeting">Estimado(a) <strong>{nombre_completo}</strong>:</p>
                  <p class="mail-message">Se le informa que se han eliminado <strong>{total}</strong> de sus registros de vacaciones. A continuación el resumen correspondiente:</p>
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="mail-detail">
                    <tr>
                      <td class="mail-detail-pad">
                        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                          <tr><td class="mail-label">Total de registros eliminados</td></tr>
                          <tr><td class="mail-value">{total}</td></tr>
                          <tr><td class="mail-label">Rango de fechas</td></tr>
                          <tr><td class="mail-value">{_fmt(fecha_min)} — {_fmt(fecha_max)}</td></tr>
                          <tr><td class="mail-label">Total de días</td></tr>
                          <tr><td class="mail-value-last">{total_dias}</td></tr>
                        </table>
                      </td>
                    </tr>
                  </table>
        {seccion_listado}
                  <p class="mail-info">Para mayor detalle, puede consultar la información en el sistema SIGGO.</p>
    """
    pie = f"""
                  <p class="mail-footer-label">Eliminado por: <strong class="mail-footer-name">{admin_nombre}</strong></p>
                  <p class="mail-footer-email">{admin_correo}</p>
                  <p class="mail-footer-note">Este es un mensaje automático. No responda a este correo.</p>
    """

    cuerpo_html = construir_correo("Vacaciones eliminadas", "mail-header-red", "mail-subtitle-red", cuerpo, pie)
    enviar_correo(destinatario, "Vacaciones eliminadas", cuerpo_html)


def registrar_rutas(app):
    @app.route("/vacaciones")
    def vacaciones():

        if "usuario" not in session:
            return redirect(url_for("home"))

        if session.get("perfil_activo", "").lower() != "admin":
            return acceso_no_autorizado()

        guardar_filtros("filtro_vacaciones")

        cursor = None
        try:
            cursor = conexion.cursor(dictionary=True)
            cursor.execute("""
                SELECT DISTINCT
                    u.id_usuario,
                    CONCAT(u.nombre,' ',u.apellidos) AS nombre
                FROM usuarios u
                INNER JOIN usuarios_roles ur ON u.id_usuario = ur.id_usuario
                INNER JOIN roles r ON ur.id_rol = r.id_rol
                WHERE r.nombre_rol = 'fiscalizador'
                  AND u.estado = 'activo'
                ORDER BY nombre
            """)

            usuarios = cursor.fetchall()

            # =========================
            # FILTROS
            # =========================
            anio_vac = request.args.get("anio_vac", "").strip()
            desde_vac = request.args.get("desde_vac", "").strip()
            hasta_vac = request.args.get("hasta_vac", "").strip()
            fiscalizador_vac = request.args.get("fiscalizador_vac", "").strip()
            estado_vac = request.args.get("estado_vac", "").strip()

            # =========================
            # VACACIONES
            # =========================
            filtro_vac = ""
            params_vac = []

            if anio_vac:
                filtro_vac += " AND (YEAR(v.fecha_inicio) = %s OR YEAR(v.fecha_fin) = %s)"
                params_vac.extend([int(anio_vac), int(anio_vac)])
            if desde_vac:
                filtro_vac += " AND v.fecha_fin >= %s"
                params_vac.append(desde_vac)
            if hasta_vac:
                filtro_vac += " AND v.fecha_inicio <= %s"
                params_vac.append(hasta_vac)
            if fiscalizador_vac:
                filtro_vac += " AND v.id_usuario = %s"
                params_vac.append(int(fiscalizador_vac))
            if estado_vac == "Pendiente":
                filtro_vac += " AND CURDATE() < v.fecha_inicio"
            elif estado_vac == "En curso":
                filtro_vac += " AND CURDATE() BETWEEN v.fecha_inicio AND v.fecha_fin"
            elif estado_vac == "Finalizado":
                filtro_vac += " AND CURDATE() > v.fecha_fin"

            cursor.execute(f"""
                SELECT 
                    v.id_vacacion,
                    v.id_usuario,
                    v.fecha_inicio,
                    v.fecha_fin,
                    CONCAT(u.nombre,' ',u.apellidos) AS nombre,
                    u.fecha_ingreso,

                    DATEDIFF(v.fecha_fin, v.fecha_inicio) + 1 AS dias,

                    CASE
                        WHEN CURDATE() < v.fecha_inicio THEN 'pendiente'
                        WHEN CURDATE() BETWEEN v.fecha_inicio AND v.fecha_fin THEN 'en_curso'
                        ELSE 'finalizado'
                    END AS estado

                FROM vacaciones v
                INNER JOIN usuarios u ON v.id_usuario = u.id_usuario
                WHERE 1=1 {filtro_vac}
                ORDER BY v.fecha_inicio DESC
            """, params_vac)

            vacaciones = cursor.fetchall()

            # =========================
            # CONTADORES (totales del año actual, independientes de los filtros)
            # =========================
            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM vacaciones
                WHERE YEAR(fecha_inicio) = YEAR(CURDATE())
                   OR YEAR(fecha_fin) = YEAR(CURDATE())
            """)
            total_registros = cursor.fetchone()["total"]

            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM vacaciones
                WHERE (YEAR(fecha_inicio) = YEAR(CURDATE())
                   OR YEAR(fecha_fin) = YEAR(CURDATE()))
                  AND CURDATE() > fecha_fin
            """)
            total_finalizadas = cursor.fetchone()["total"]

            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM vacaciones
                WHERE (YEAR(fecha_inicio) = YEAR(CURDATE())
                   OR YEAR(fecha_fin) = YEAR(CURDATE()))
                  AND CURDATE() < fecha_inicio
            """)
            total_pendientes = cursor.fetchone()["total"]

            cursor.execute("""
                SELECT COUNT(*) AS total
                FROM vacaciones
                WHERE (YEAR(fecha_inicio) = YEAR(CURDATE())
                   OR YEAR(fecha_fin) = YEAR(CURDATE()))
                  AND CURDATE() BETWEEN fecha_inicio AND fecha_fin
            """)
            total_en_curso = cursor.fetchone()["total"]

            return render_template(
                "vacaciones.html",
                vacaciones=vacaciones,
                usuarios=usuarios,
                total_registros=total_registros,
                total_finalizadas=total_finalizadas,
                total_pendientes=total_pendientes,
                total_en_curso=total_en_curso
            )
        except Exception as e:
            print("ERROR vacaciones:", e)
            return error_interno()
        finally:
            if cursor is not None:
                cursor.close()

    @app.route("/alerta_vacaciones")
    def alerta_vacaciones():

        if "usuario" not in session:
            return redirect(url_for("home"))

        if session.get("perfil_activo", "").lower() != "admin":
            return acceso_no_autorizado()

        cursor = None
        try:
            cursor = conexion.cursor(dictionary=True)

            cursor.execute("""
                SELECT DISTINCT
                    u.id_usuario,
                    CONCAT(u.nombre,' ',u.apellidos) AS nombre
                FROM usuarios u
                INNER JOIN usuarios_roles ur ON u.id_usuario = ur.id_usuario
                INNER JOIN roles r ON ur.id_rol = r.id_rol
                WHERE r.nombre_rol = 'fiscalizador'
                  AND u.estado = 'activo'
                ORDER BY nombre
            """)
            usuarios = cursor.fetchall()

            # =========================
            # FILTROS
            # =========================
            anio_alerta = request.args.get("anio_alerta", "").strip()
            desde_alerta = request.args.get("desde_alerta", "").strip()
            hasta_alerta = request.args.get("hasta_alerta", "").strip()
            fiscalizador_alerta = request.args.get("fiscalizador_alerta", "").strip()
            estado_alerta = request.args.get("estado_alerta", "").strip()

            filtro_alerta = ""
            params_alerta = []

            if anio_alerta:
                filtro_alerta += " AND YEAR(DATE_ADD(u.fecha_ingreso, INTERVAL GREATEST(1, TIMESTAMPDIFF(YEAR, u.fecha_ingreso, CURDATE())) YEAR)) = %s"
                params_alerta.append(int(anio_alerta))
            if desde_alerta:
                filtro_alerta += " AND DATE_ADD(u.fecha_ingreso, INTERVAL GREATEST(1, TIMESTAMPDIFF(YEAR, u.fecha_ingreso, CURDATE())) YEAR) >= %s"
                params_alerta.append(desde_alerta)
            if hasta_alerta:
                filtro_alerta += " AND DATE_ADD(u.fecha_ingreso, INTERVAL GREATEST(1, TIMESTAMPDIFF(YEAR, u.fecha_ingreso, CURDATE())) YEAR) <= %s"
                params_alerta.append(hasta_alerta)
            if fiscalizador_alerta:
                filtro_alerta += " AND u.id_usuario = %s"
                params_alerta.append(int(fiscalizador_alerta))
            if estado_alerta == "LISTO":
                filtro_alerta += " AND DATEDIFF(DATE_ADD(u.fecha_ingreso, INTERVAL GREATEST(1, TIMESTAMPDIFF(YEAR, u.fecha_ingreso, CURDATE())) YEAR), CURDATE()) <= 0"
            elif estado_alerta == "PRÓXIMO":
                filtro_alerta += " AND DATEDIFF(DATE_ADD(u.fecha_ingreso, INTERVAL GREATEST(1, TIMESTAMPDIFF(YEAR, u.fecha_ingreso, CURDATE())) YEAR), CURDATE()) BETWEEN 1 AND 15"
            elif estado_alerta == "NORMAL":
                filtro_alerta += " AND DATEDIFF(DATE_ADD(u.fecha_ingreso, INTERVAL GREATEST(1, TIMESTAMPDIFF(YEAR, u.fecha_ingreso, CURDATE())) YEAR), CURDATE()) > 15"

            cursor.execute(f"""
                SELECT 
                    u.id_usuario,
                    YEAR(u.fecha_ingreso) AS anio_ingreso,
                    CONCAT(u.nombre,' ',u.apellidos) AS nombre,
                    DATE_ADD(u.fecha_ingreso, INTERVAL GREATEST(1, TIMESTAMPDIFF(YEAR, u.fecha_ingreso, CURDATE())) YEAR) AS fecha_vacaciones,
                    DATEDIFF(
                        DATE_ADD(u.fecha_ingreso, INTERVAL GREATEST(1, TIMESTAMPDIFF(YEAR, u.fecha_ingreso, CURDATE())) YEAR),
                        CURDATE()
                    ) AS dias_faltantes,
                    COALESCE(
                        (SELECT SUM(DATEDIFF(v.fecha_fin, v.fecha_inicio) + 1)
                         FROM vacaciones v
                         WHERE v.id_usuario = u.id_usuario AND v.fecha_inicio <= CURDATE()), 0
                    ) AS dias_tomados_total
                FROM usuarios u
                INNER JOIN usuarios_roles ur ON u.id_usuario = ur.id_usuario
                INNER JOIN roles r ON ur.id_rol = r.id_rol
                WHERE r.nombre_rol = 'fiscalizador' AND u.estado = 'activo' {filtro_alerta}
                ORDER BY dias_faltantes ASC
            """, params_alerta)

            alertas = cursor.fetchall()

            for a in alertas:
                a["dias_tomados"], a["dias_pendientes_este_anio"], a["dias_pendientes_anteriores"] = \
                    balance_vacaciones_fifo(a["anio_ingreso"], a["dias_tomados_total"])

            return render_template("alerta_vacaciones.html", alertas=alertas, usuarios=usuarios)
        except Exception as e:
            print("ERROR alerta_vacaciones:", e)
            return error_interno()
        finally:
            if cursor is not None:
                cursor.close()

    @app.route("/editar_vacacion/<int:id>")
    def editar_vacacion(id):

        if "usuario" not in session:
            return redirect(url_for("home"))

        if session.get("perfil_activo", "").lower() != "admin":
            return acceso_no_autorizado()

        cursor = None
        try:
            cursor = conexion.cursor(dictionary=True)

            cursor.execute("""
                SELECT id_vacacion, fecha_inicio, fecha_fin
                FROM vacaciones
                WHERE id_vacacion = %s
            """, (id,))

            data = cursor.fetchone()

            if not data:
                return no_encontrado("Vacación no encontrada")

            return render_template("editar_vacacion.html", data=data)
        except Exception as e:
            print("ERROR editar_vacacion:", e)
            return error_interno()
        finally:
            if cursor is not None:
                cursor.close()

    @app.route("/actualizar_vacacion/<int:id>", methods=["POST"])
    @limiter.limit("10 per minute")
    def actualizar_vacacion(id):

        if "usuario" not in session:
            return redirect(url_for("home"))

        if session.get("perfil_activo", "").lower() != "admin":
            return acceso_no_autorizado()

        inicio = request.form.get("fecha_inicio", "").strip()
        fin = request.form.get("fecha_fin", "").strip()

        if not inicio or not fin:
            flash("Todos los campos son obligatorios", "error")
            return redirigir_con_filtros("vacaciones", "filtro_vacaciones")

        try:
            inicio_dt = datetime.strptime(inicio, "%Y-%m-%d").date()
            fin_dt = datetime.strptime(fin, "%Y-%m-%d").date()
        except ValueError:
            flash("Formato de fecha inválido. Use YYYY-MM-DD", "error")
            return redirigir_con_filtros("vacaciones", "filtro_vacaciones")

        if inicio_dt > fin_dt:
            flash("Fecha inválida: inicio mayor que fin", "error")
            return redirigir_con_filtros("vacaciones", "filtro_vacaciones")

        cursor = conexion.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT id_usuario, fecha_inicio AS fecha_inicio_ant, fecha_fin AS fecha_fin_ant,
                       (SELECT CONCAT(nombre,' ',apellidos) FROM usuarios WHERE id_usuario = vacaciones.id_usuario) AS nombre_completo,
                       (SELECT correo FROM usuarios WHERE id_usuario = vacaciones.id_usuario) AS correo
                FROM vacaciones
                WHERE id_vacacion = %s
            """, (id,))
            old_data = cursor.fetchone()

            if not old_data:
                return no_encontrado("Vacación no encontrada")

            id_usuario = old_data["id_usuario"]

            # =========================
            # VALIDAR ESTADO Y 1 AÑO DE ANTIGÜEDAD
            # =========================
            cursor.execute("SELECT fecha_ingreso, estado FROM usuarios WHERE id_usuario = %s", (id_usuario,))
            user_ant = cursor.fetchone()

            if not user_ant:
                flash("Usuario no encontrado", "error")
                return redirigir_con_filtros("vacaciones", "filtro_vacaciones")

            if user_ant.get("estado") != "activo":
                flash("No se pueden actualizar vacaciones para un usuario inactivo", "error")
                return redirigir_con_filtros("vacaciones", "filtro_vacaciones")

            if user_ant.get("fecha_ingreso"):
                fecha_ingreso = user_ant["fecha_ingreso"]
                try:
                    fecha_habil = fecha_ingreso.replace(year=fecha_ingreso.year + 1)
                except ValueError:
                    # 29 de febrero en un año no bisiesto: se toma el 28 de febrero
                    fecha_habil = fecha_ingreso.replace(year=fecha_ingreso.year + 1, day=28)

                if datetime.now().date() < fecha_habil:
                    flash("El usuario aún no cumple 1 año de antigüedad para solicitar vacaciones", "error")
                    return redirigir_con_filtros("vacaciones", "filtro_vacaciones")

            # Validar que no haya guardias en el nuevo rango de fechas
            cursor.execute("""
                SELECT 1
                FROM guardias
                WHERE id_usuario = %s
                AND fecha_guardia BETWEEN %s AND %s
                LIMIT 1
            """, (id_usuario, inicio, fin))

            if cursor.fetchone():
                flash("No se puede actualizar: el usuario tiene guardias en ese rango de fechas", "error")
                return redirigir_con_filtros("vacaciones", "filtro_vacaciones")

            # Validar cruce con otras vacaciones (excluyendo la vacación que se está editando)
            cursor.execute("""
                SELECT 1
                FROM vacaciones
                WHERE id_usuario = %s AND id_vacacion != %s
                AND (
                    (%s BETWEEN fecha_inicio AND fecha_fin)
                    OR (%s BETWEEN fecha_inicio AND fecha_fin)
                    OR (fecha_inicio BETWEEN %s AND %s)
                )
                LIMIT 1
            """, (id_usuario, id, inicio, fin, inicio, fin))

            if cursor.fetchone():
                flash("Ya tiene vacaciones registradas en ese rango de fechas", "error")
                return redirigir_con_filtros("vacaciones", "filtro_vacaciones")

            # =========================
            # VALIDAR LÍMITE DE 30 DÍAS POR AÑO + PENDIENTES ACUMULADOS
            # =========================
            cursor.execute("""
                SELECT TIMESTAMPDIFF(YEAR, fecha_ingreso, CURDATE()) * 30 AS total_acumulado
                FROM usuarios
                WHERE id_usuario = %s
            """, (id_usuario,))
            total_acumulado = cursor.fetchone()["total_acumulado"] or 0

            cursor.execute("""
                SELECT COALESCE(SUM(DATEDIFF(fecha_fin, fecha_inicio) + 1), 0) AS total_tomados
                FROM vacaciones
                WHERE id_usuario = %s AND id_vacacion != %s
            """, (id_usuario, id))
            dias_tomados_total = cursor.fetchone()["total_tomados"] or 0

            dias_nuevos = (fin_dt - inicio_dt).days + 1
            dias_disponibles = total_acumulado - dias_tomados_total

            if dias_nuevos > dias_disponibles:
                flash(
                    f"Excede los días disponibles: tiene {dias_disponibles} días de vacaciones "
                    f"(30 días por año de servicio + pendientes de años anteriores).",
                    "error"
                )
                return redirigir_con_filtros("vacaciones", "filtro_vacaciones")

            cursor.execute("""
                UPDATE vacaciones
                SET fecha_inicio = %s, fecha_fin = %s
                WHERE id_vacacion = %s
            """, (inicio, fin, id))

            if old_data:
                cursor.execute("""
                    INSERT INTO notificaciones (id_usuario, titulo, mensaje)
                    VALUES (%s, %s, %s)
                """, (
                    old_data['id_usuario'],
                    "Vacaciones actualizadas",
                    f"Vacaciones modificadas: del {inicio_dt.strftime('%d/%m/%Y')} al {fin_dt.strftime('%d/%m/%Y')} (antes: {old_data['fecha_inicio_ant'].strftime('%d/%m/%Y')} al {old_data['fecha_fin_ant'].strftime('%d/%m/%Y')}) para {old_data['nombre_completo']}."
                ))

                nombre_admin, correo_admin = _datos_admin(cursor)
                _enviar_correo_vacacion(
                    old_data["correo"],
                    old_data["nombre_completo"],
                    inicio_dt,
                    fin_dt,
                    "actualizada",
                    admin_nombre=nombre_admin,
                    admin_correo=correo_admin,
                )

            conexion.commit()

            registrar_auditoria("vacacion_actualizada", f"Vacaciones actualizadas: ID {id}")
            flash("Vacaciones actualizadas correctamente", "success")
            return redirigir_con_filtros("vacaciones", "filtro_vacaciones")

        except Exception as e:
            conexion.rollback()
            print("ERROR actualizar_vacacion:", e)
            return error_interno()

        finally:
            cursor.close()
    # ADMIN — Guardar nueva vacación (validando antigüedad, cruces y guardias)
    @app.route("/guardar_vacacion", methods=["POST"])
    @limiter.limit("10 per minute")
    def guardar_vacacion():

        if "usuario" not in session:
            return redirect(url_for("home"))

        if session.get("perfil_activo", "").lower() != "admin":
            return acceso_no_autorizado()

        id_usuario = request.form.get("id_usuario", "").strip()
        inicio = request.form.get("fecha_inicio", "").strip()
        fin = request.form.get("fecha_fin", "").strip()

        if not id_usuario or not inicio or not fin:
            flash("Todos los campos son obligatorios", "error")
            return redirigir_con_filtros("vacaciones", "filtro_vacaciones")

        try:
            inicio_dt = datetime.strptime(inicio, "%Y-%m-%d").date()
            fin_dt = datetime.strptime(fin, "%Y-%m-%d").date()
        except ValueError:
            flash("Formato de fecha inválido. Use YYYY-MM-DD", "error")
            return redirigir_con_filtros("vacaciones", "filtro_vacaciones")

        if inicio_dt > fin_dt:
            flash("Fecha inválida: inicio mayor que fin", "error")
            return redirigir_con_filtros("vacaciones", "filtro_vacaciones")

        cursor = conexion.cursor(dictionary=True)

        try:

            # =========================
            # OBTENER USUARIO
            # =========================
            cursor.execute("""
                SELECT fecha_ingreso, estado
                FROM usuarios
                WHERE id_usuario = %s
            """, (id_usuario,))

            user = cursor.fetchone()

            if not user:
                flash("Usuario no encontrado", "error")
                return redirigir_con_filtros("vacaciones", "filtro_vacaciones")

            if user["estado"] != "activo":
                flash("No se pueden registrar vacaciones para un usuario inactivo", "error")
                return redirigir_con_filtros("vacaciones", "filtro_vacaciones")

            fecha_ingreso = user["fecha_ingreso"]

            # =========================
            # VALIDAR 1 AÑO DE ANTIGÜEDAD
            # =========================
            try:
                fecha_habil = fecha_ingreso.replace(year=fecha_ingreso.year + 1)
            except ValueError:
                # 29 de febrero en un año no bisiesto: se toma el 28 de febrero
                fecha_habil = fecha_ingreso.replace(year=fecha_ingreso.year + 1, day=28)

            if datetime.now().date() < fecha_habil:
                flash("El usuario aún no cumple 1 año de antigüedad para solicitar vacaciones", "error")
                return redirigir_con_filtros("vacaciones", "filtro_vacaciones")

            # =========================
            # VALIDAR GUARDIAS EN RANGO
            # =========================
            cursor.execute("""
                SELECT 1
                FROM guardias
                WHERE id_usuario = %s
                AND fecha_guardia BETWEEN %s AND %s
                LIMIT 1
            """, (id_usuario, inicio, fin))

            if cursor.fetchone():
                flash("No se puede registrar: el usuario tiene guardias en ese rango de fechas", "error")
                return redirigir_con_filtros("vacaciones", "filtro_vacaciones")

            # =========================
            # VALIDAR CRUCE DE VACACIONES
            # =========================
            cursor.execute("""
                SELECT 1
                FROM vacaciones
                WHERE id_usuario = %s
                AND (
                    (%s BETWEEN fecha_inicio AND fecha_fin)
                    OR (%s BETWEEN fecha_inicio AND fecha_fin)
                    OR (fecha_inicio BETWEEN %s AND %s)
                )
                LIMIT 1
            """, (id_usuario, inicio, fin, inicio, fin))

            if cursor.fetchone():
                flash("Ya tiene vacaciones registradas en ese rango de fechas", "error")
                return redirigir_con_filtros("vacaciones", "filtro_vacaciones")

            # =========================
            # VALIDAR LÍMITE DE 30 DÍAS POR AÑO + PENDIENTES ACUMULADOS
            # =========================
            cursor.execute("""
                SELECT TIMESTAMPDIFF(YEAR, fecha_ingreso, CURDATE()) * 30 AS total_acumulado
                FROM usuarios
                WHERE id_usuario = %s
            """, (id_usuario,))
            total_acumulado = cursor.fetchone()["total_acumulado"] or 0

            cursor.execute("""
                SELECT COALESCE(SUM(DATEDIFF(fecha_fin, fecha_inicio) + 1), 0) AS total_tomados
                FROM vacaciones
                WHERE id_usuario = %s
            """, (id_usuario,))
            dias_tomados_total = cursor.fetchone()["total_tomados"] or 0

            dias_nuevos = (fin_dt - inicio_dt).days + 1
            dias_disponibles = total_acumulado - dias_tomados_total

            if dias_nuevos > dias_disponibles:
                flash(
                    f"Excede los días disponibles: tiene {dias_disponibles} días de vacaciones "
                    f"(30 días por año de servicio + pendientes de años anteriores).",
                    "error"
                )
                return redirigir_con_filtros("vacaciones", "filtro_vacaciones")

            # =========================
            # INSERTAR
            # =========================
            cursor.execute("""
                INSERT INTO vacaciones (
                    id_usuario,
                    fecha_inicio,
                    fecha_fin
                )
                VALUES (%s, %s, %s)
            """, (id_usuario, inicio, fin))

            # =========================
            # NOTIFICACIÓN AL USUARIO
            # =========================
            cursor.execute("""
                SELECT CONCAT(nombre,' ',apellidos) AS nombre_completo, correo
                FROM usuarios
                WHERE id_usuario = %s
            """, (id_usuario,))
            user_info = cursor.fetchone()
            if user_info:
                cursor.execute("""
                    INSERT INTO notificaciones (id_usuario, titulo, mensaje)
                    VALUES (%s, %s, %s)
                """, (
                    id_usuario,
                    "Vacaciones registradas",
                    f"Se han registrado vacaciones del {inicio_dt.strftime('%d/%m/%Y')} al {fin_dt.strftime('%d/%m/%Y')} para {user_info['nombre_completo']}."
                ))

                nombre_admin, correo_admin = _datos_admin(cursor)
                _enviar_correo_vacacion(
                    user_info["correo"],
                    user_info["nombre_completo"],
                    inicio_dt,
                    fin_dt,
                    "registrada",
                    admin_nombre=nombre_admin,
                    admin_correo=correo_admin,
                )

            conexion.commit()

            registrar_auditoria("vacacion_creada", f"Vacaciones registradas: usuario {id_usuario}, {inicio} al {fin}")
            flash("Vacaciones registradas correctamente", "success")
            return redirigir_con_filtros("vacaciones", "filtro_vacaciones")

        except mysql.connector.errors.OperationalError as e:
            conexion.rollback()
            if e.errno == 1205:
                flash("La base de datos está ocupada, intente nuevamente en unos segundos", "error")
            else:
                flash("Error de conexión con la base de datos, intente nuevamente", "error")
            return redirigir_con_filtros("vacaciones", "filtro_vacaciones")

        except Exception as e:
            conexion.rollback()
            print("ERROR guardar_vacacion:", e)
            flash("Ocurrió un error al registrar las vacaciones. Intente nuevamente.", "error")
            return redirigir_con_filtros("vacaciones", "filtro_vacaciones")

        finally:
            cursor.close()

    # ADMIN — Eliminar vacación (con notificación al usuario)
    @app.route("/eliminar_vacacion/<int:id>", methods=["POST"])
    @limiter.limit("10 per minute")
    def eliminar_vacacion(id):

        if "usuario" not in session:
            return redirect(url_for("home"))

        if session.get("perfil_activo", "").lower() != "admin":
            return acceso_no_autorizado()

        cursor = conexion.cursor(dictionary=True)

        try:
            # Obtener datos antes de eliminar
            cursor.execute("""
                SELECT v.id_usuario, v.fecha_inicio, v.fecha_fin,
                       CONCAT(u.nombre,' ',u.apellidos) AS nombre_completo,
                       u.correo
                FROM vacaciones v
                INNER JOIN usuarios u ON v.id_usuario = u.id_usuario
                WHERE v.id_vacacion = %s
            """, (id,))
            vac_data = cursor.fetchone()

            cursor.execute("""
                DELETE FROM vacaciones
                WHERE id_vacacion = %s
            """, (id,))

            # Insertar notificación
            if vac_data:
                cursor.execute("""
                    INSERT INTO notificaciones (id_usuario, titulo, mensaje)
                    VALUES (%s, %s, %s)
                """, (
                    vac_data['id_usuario'],
                    "Vacaciones eliminadas",
                    f"Se han eliminado las vacaciones del {vac_data['fecha_inicio'].strftime('%d/%m/%Y')} al {vac_data['fecha_fin'].strftime('%d/%m/%Y')} de {vac_data['nombre_completo']}."
                ))

                nombre_admin, correo_admin = _datos_admin(cursor)
                _enviar_correo_vacacion(
                    vac_data["correo"],
                    vac_data["nombre_completo"],
                    vac_data["fecha_inicio"],
                    vac_data["fecha_fin"],
                    "eliminada",
                    admin_nombre=nombre_admin,
                    admin_correo=correo_admin,
                )

            conexion.commit()

            registrar_auditoria("vacacion_eliminada", f"Vacaciones eliminadas: ID {id}")
            flash("Vacaciones eliminadas correctamente", "success")
            return redirigir_con_filtros("vacaciones", "filtro_vacaciones")

        except Exception as e:
            conexion.rollback()
            print("ERROR eliminar_vacacion:", e)
            return error_interno()

        finally:
            cursor.close()

    @app.route("/eliminar_vacaciones", methods=["POST"])
    @limiter.limit("10 per minute")
    def eliminar_vacaciones():

        if "usuario" not in session:
            return redirect(url_for("home"))

        if session.get("perfil_activo", "").lower() != "admin":
            return acceso_no_autorizado()

        ids = request.form.getlist("ids_vacacion")
        if not ids:
            flash("No seleccionó ninguna vacación", "error")
            return redirigir_con_filtros("vacaciones", "filtro_vacaciones")

        ids_int = []
        for valor in ids:
            try:
                ids_int.append(int(valor))
            except (TypeError, ValueError):
                return datos_invalidos("Identificador de vacación inválido")

        cursor = conexion.cursor(dictionary=True)

        try:
            placeholders = ",".join(["%s"] * len(ids_int))
            cursor.execute(f"""
                SELECT v.id_usuario, v.fecha_inicio, v.fecha_fin,
                       CONCAT(u.nombre,' ',u.apellidos) AS nombre_completo,
                       u.correo
                FROM vacaciones v
                INNER JOIN usuarios u ON v.id_usuario = u.id_usuario
                WHERE v.id_vacacion IN ({placeholders})
            """, tuple(ids_int))
            vacaciones_data = cursor.fetchall()

            cursor.execute(
                f"DELETE FROM vacaciones WHERE id_vacacion IN ({placeholders})",
                tuple(ids_int),
            )

            nombre_admin, correo_admin = _datos_admin(cursor)

            for vac in vacaciones_data:
                cursor.execute("""
                    INSERT INTO notificaciones (id_usuario, titulo, mensaje)
                    VALUES (%s, %s, %s)
                """, (
                    vac["id_usuario"],
                    "Vacaciones eliminadas",
                    f"Se han eliminado las vacaciones del {vac['fecha_inicio'].strftime('%d/%m/%Y')} al {vac['fecha_fin'].strftime('%d/%m/%Y')} de {vac['nombre_completo']}."
                ))

            por_correo = {}
            for vac in vacaciones_data:
                if not vac["correo"]:
                    continue
                grupo = por_correo.setdefault(vac["correo"], {"nombre": vac["nombre_completo"], "vacaciones": []})
                grupo["vacaciones"].append({"inicio": vac["fecha_inicio"], "fin": vac["fecha_fin"]})

            for correo, grupo in por_correo.items():
                try:
                    _enviar_correo_vacaciones_eliminadas(
                        correo,
                        grupo["nombre"] or "Fiscalizador",
                        grupo["vacaciones"],
                        admin_nombre=nombre_admin,
                        admin_correo=correo_admin,
                    )
                except Exception as e:
                    print("ERROR AL NOTIFICAR ELIMINACIÓN MASIVA DE VACACIONES:", e)

            conexion.commit()
            registrar_auditoria("vacaciones_eliminadas", f"Eliminadas {len(vacaciones_data)} vacaciones")
            flash(f"Se eliminaron {len(vacaciones_data)} vacaciones correctamente", "success")
        except Exception as e:
            conexion.rollback()
            print("ERROR ELIMINAR VACACIONES:", e)
            flash("Ocurrió un error al eliminar las vacaciones", "error")
        finally:
            cursor.close()

        return redirigir_con_filtros("vacaciones", "filtro_vacaciones")



    # ==========================
    # PANEL DE FISCALIZADOR
    # ==========================
    # Dashboard del fiscalizador: guardias, notificaciones, alertas de vacaciones y contadores



    @app.route("/mis_vacaciones")
    def mis_vacaciones():

        # =========================
        # VALIDACIÓN DE SESIÓN
        # =========================
        if "usuario" not in session:
            return redirect(url_for("home"))

        if session.get("perfil_activo", "").lower() != "fiscalizador":
            return acceso_no_autorizado()

        # La conexión ya es un objeto singleton (no se usa ())
        conn = conexion
        cursor = conn.cursor(dictionary=True)

        try:

            # =========================
            # FILTROS
            # =========================
            anio = request.args.get("anio", "").strip()
            fecha_desde = request.args.get("fecha_desde", "").strip()
            fecha_hasta = request.args.get("fecha_hasta", "").strip()
            estado = request.args.get("estado", "").strip()

            conditions = ["v.id_usuario = %s"]
            params = [session["id_usuario"]]

            if anio:
                conditions.append("(YEAR(v.fecha_inicio) = %s OR YEAR(v.fecha_fin) = %s)")
                params.append(int(anio))
                params.append(int(anio))

            if fecha_desde:
                conditions.append("v.fecha_fin >= %s")
                params.append(fecha_desde)

            if fecha_hasta:
                conditions.append("v.fecha_inicio <= %s")
                params.append(fecha_hasta)

            if estado == "pendiente":
                conditions.append("CURDATE() < v.fecha_inicio")
            elif estado == "en_curso":
                conditions.append("CURDATE() BETWEEN v.fecha_inicio AND v.fecha_fin")
            elif estado == "finalizado":
                conditions.append("CURDATE() > v.fecha_fin")

            where_clause = " AND ".join(conditions)

            # =========================
            # VACACIONES DEL USUARIO
            # =========================
            cursor.execute(f"""
                SELECT 
                    v.id_vacacion,
                    v.fecha_inicio,
                    v.fecha_fin,

                    DATEDIFF(v.fecha_fin, v.fecha_inicio) + 1 AS dias,

                    CASE
                        WHEN CURDATE() < v.fecha_inicio THEN 'pendiente'
                        WHEN CURDATE() BETWEEN v.fecha_inicio AND v.fecha_fin THEN 'en_curso'
                        ELSE 'finalizado'
                    END AS estado

                FROM vacaciones v
                WHERE {where_clause}
                ORDER BY v.fecha_inicio DESC
            """, params)

            vacaciones = cursor.fetchall()

            # =========================
            # DIAS PENDIENTES (30 días por año cumplido)
            # =========================
            cursor.execute("""
                SELECT YEAR(fecha_ingreso) AS anio_ingreso
                FROM usuarios WHERE id_usuario = %s
            """, (session["id_usuario"],))

            user = cursor.fetchone()
            dias_pendientes = 0
            dias_pendientes_este_anio = 0
            dias_pendientes_anteriores = 0

            if user is not None:
                cursor.execute("""
                    SELECT COALESCE(SUM(DATEDIFF(v.fecha_fin, v.fecha_inicio) + 1), 0) AS total
                    FROM vacaciones v
                    WHERE v.id_usuario = %s
                      AND v.fecha_inicio <= CURDATE()
                """, (session["id_usuario"],))

                result = cursor.fetchone()
                dias_tomados_total = result["total"] if result else 0

                _, dias_pendientes_este_anio, dias_pendientes_anteriores = \
                    balance_vacaciones_fifo(user["anio_ingreso"], dias_tomados_total)
                dias_pendientes = dias_pendientes_este_anio + dias_pendientes_anteriores

            # =========================
            # RENDER
            # =========================
            return render_template(
                "mis_vacaciones.html",
                vacaciones=vacaciones,
                dias_pendientes=dias_pendientes,
                dias_pendientes_este_anio=dias_pendientes_este_anio,
                dias_pendientes_anteriores=dias_pendientes_anteriores
            )

        finally:
            cursor.close()
            # NOTA: NO cerrar 'conexion' — es el singleton compartido por toda la app
