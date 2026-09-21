from flask import Blueprint, request, render_template, redirect, session, url_for
from datetime import datetime, date
from db import conexion
from constantes import DIAS_ES, MESES_ABREV
from utils import acceso_no_autorizado, error_interno

"""
Blueprint de dashboards: panel del administrador y panel del fiscalizador.
"""

dashboard_bp = Blueprint("dashboard", __name__)


# ==========================
# PANEL DE ADMINISTRADOR
# ==========================
# Dashboard principal con estadísticas y alertas de vacaciones
@dashboard_bp.route("/administrador")
def administrador():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    cursor = None
    try:
        cursor = conexion.cursor(dictionary=True)

        # Alertas de vacaciones
        cursor.execute("""
            SELECT
                u.id_usuario,
                CONCAT(u.nombre,' ',u.apellidos) AS nombre,
                DATE_ADD(u.fecha_ingreso, INTERVAL GREATEST(1, YEAR(CURDATE()) - YEAR(u.fecha_ingreso)) YEAR) AS fecha_vacaciones,
                DATEDIFF(
                    DATE_ADD(u.fecha_ingreso, INTERVAL GREATEST(1, YEAR(CURDATE()) - YEAR(u.fecha_ingreso)) YEAR),
                    CURDATE()
                ) AS dias_faltantes,
                COALESCE(
                    (SELECT SUM(DATEDIFF(v.fecha_fin, v.fecha_inicio) + 1)
                     FROM vacaciones v
                     WHERE v.id_usuario = u.id_usuario
                       AND v.fecha_inicio >= DATE_ADD(u.fecha_ingreso, INTERVAL TIMESTAMPDIFF(YEAR, u.fecha_ingreso, CURDATE()) YEAR) AND v.fecha_inicio <= CURDATE()
                    ), 0
                ) AS dias_tomados,
                TIMESTAMPDIFF(YEAR, u.fecha_ingreso, CURDATE()) * 30 AS total_acumulado,
                COALESCE(
                    (SELECT SUM(DATEDIFF(v.fecha_fin, v.fecha_inicio) + 1)
                     FROM vacaciones v
                     WHERE v.id_usuario = u.id_usuario AND v.fecha_inicio <= CURDATE()), 0
                ) AS dias_tomados_total
            FROM usuarios u
            INNER JOIN usuarios_roles ur ON u.id_usuario = ur.id_usuario
            INNER JOIN roles r ON ur.id_rol = r.id_rol
            WHERE r.nombre_rol = 'fiscalizador'
              AND u.estado = 'activo'
        """)

        alertas = cursor.fetchall()

        for a in alertas:
            pendientes_este_anio = max(0, 30 - a["dias_tomados"])
            a["dias_pendientes_este_anio"] = pendientes_este_anio
            a["dias_pendientes_anteriores"] = max(
                0,
                (a["total_acumulado"] - a["dias_tomados_total"]) - pendientes_este_anio
            )

        # Totales del dashboard
        cursor.execute("SELECT COUNT(*) AS total FROM usuarios u INNER JOIN usuarios_roles ur ON u.id_usuario = ur.id_usuario INNER JOIN roles r ON ur.id_rol = r.id_rol WHERE r.nombre_rol = 'fiscalizador' AND u.estado = 'activo'")
        total_usuarios = cursor.fetchone()["total"]

        cursor.execute("SELECT COUNT(*) AS total FROM guardias WHERE YEAR(fecha_guardia) = YEAR(CURDATE())")
        total_guardias = cursor.fetchone()["total"]

        cursor.execute("SELECT COUNT(*) AS total FROM informes WHERE estado = 'activo' AND YEAR(fecha_subida) = YEAR(CURDATE())")
        total_informes = cursor.fetchone()["total"]

        cursor.execute("SELECT COUNT(*) AS total FROM compensaciones WHERE YEAR(fecha_compensacion) = YEAR(CURDATE())")
        total_compensaciones = cursor.fetchone()["total"]

        # Resumen operativo: métricas operativas del año (feriados, asistencias, faltas, pendientes)
        cursor.execute("""
            SELECT
                (SELECT COUNT(*) FROM feriados WHERE YEAR(fecha) = YEAR(CURDATE())) AS feriados,
                (SELECT COUNT(*) FROM guardias g LEFT JOIN asistencia a ON g.id_guardia = a.id_guardia
                  WHERE a.estado = 'asistio' AND YEAR(g.fecha_guardia) = YEAR(CURDATE())) AS asistencias,
                (SELECT COUNT(*) FROM guardias g LEFT JOIN asistencia a ON g.id_guardia = a.id_guardia
                  WHERE a.estado = 'falta' AND YEAR(g.fecha_guardia) = YEAR(CURDATE())) AS faltas,
                (SELECT COUNT(*) FROM guardias g LEFT JOIN asistencia a ON g.id_guardia = a.id_guardia
                  WHERE (a.estado IS NULL OR a.estado = '' OR a.estado NOT IN ('asistio','falta','justificado'))
                    AND YEAR(g.fecha_guardia) = YEAR(CURDATE())) AS pendientes
        """)
        resumen_operativo = cursor.fetchone()
        resumen_operativo = {
            "feriados": int(resumen_operativo["feriados"] or 0),
            "asistencias": int(resumen_operativo["asistencias"] or 0),
            "faltas": int(resumen_operativo["faltas"] or 0),
            "pendientes": int(resumen_operativo["pendientes"] or 0),
        }

        # Resumen operativo: guardias agrupadas por año y mes
        cursor.execute("""
            SELECT YEAR(fecha_guardia) AS anio, MONTH(fecha_guardia) AS mes, COUNT(*) AS total
            FROM guardias
            GROUP BY YEAR(fecha_guardia), MONTH(fecha_guardia)
            ORDER BY anio ASC, mes ASC
        """)
        guardias_por_mes = cursor.fetchall()

        # Resumen operativo: estado de los fiscalizadores (activos / en vacaciones / inactivos)
        cursor.execute("""
            SELECT
                COALESCE(SUM(CASE WHEN u.estado = 'activo' AND v.id_vacacion IS NULL THEN 1 ELSE 0 END), 0) AS activos,
                COALESCE(SUM(CASE WHEN u.estado = 'activo' AND v.id_vacacion IS NOT NULL THEN 1 ELSE 0 END), 0) AS en_vacaciones,
                COALESCE(SUM(CASE WHEN u.estado = 'inactivo' THEN 1 ELSE 0 END), 0) AS inactivos
            FROM usuarios u
            INNER JOIN usuarios_roles ur ON u.id_usuario = ur.id_usuario
            INNER JOIN roles r ON ur.id_rol = r.id_rol
            LEFT JOIN vacaciones v
                ON v.id_usuario = u.id_usuario
                AND CURDATE() BETWEEN v.fecha_inicio AND v.fecha_fin
            WHERE r.nombre_rol = 'fiscalizador'
        """)
        estado_fiscalizadores = cursor.fetchone()
        estado_fiscalizadores = {
            "activos": int(estado_fiscalizadores["activos"] or 0),
            "en_vacaciones": int(estado_fiscalizadores["en_vacaciones"] or 0),
            "inactivos": int(estado_fiscalizadores["inactivos"] or 0),
        }

        # Próximas guardias programadas (fechas futuras)
        cursor.execute("""
            SELECT g.id_guardia, g.fecha_guardia, CONCAT(u.nombre,' ',u.apellidos) AS fiscalizador
            FROM guardias g
            INNER JOIN usuarios u ON g.id_usuario = u.id_usuario
            WHERE g.fecha_guardia >= CURDATE()
            ORDER BY g.fecha_guardia ASC
            LIMIT 5
        """)
        proximas_guardias = cursor.fetchall()
        for g in proximas_guardias:
            fecha = g["fecha_guardia"]
            if isinstance(fecha, date):
                g["dia_semana"] = DIAS_ES[fecha.weekday()]
                g["dia_numero"] = fecha.day
                g["mes_abrev"] = MESES_ABREV[fecha.month - 1]
            else:
                g["dia_semana"] = ""
                g["dia_numero"] = "—"
                g["mes_abrev"] = ""

        return render_template("administrador.html",
                               alertas=alertas,
                               total_usuarios=total_usuarios,
                               total_guardias=total_guardias,
                               total_informes=total_informes,
                               total_compensaciones=total_compensaciones,
                               resumen_operativo=resumen_operativo,
                               guardias_por_mes=guardias_por_mes,
                               estado_fiscalizadores=estado_fiscalizadores,
                               proximas_guardias=proximas_guardias)
    except Exception as e:
        print("ERROR administrador:", e)
        return error_interno()
    finally:
        if cursor is not None:
            cursor.close()


# ==========================
# PANEL DE FISCALIZADOR
# ==========================
# Dashboard del fiscalizador: guardias, notificaciones, alertas de vacaciones y contadores
@dashboard_bp.route("/inicio")
def inicio():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo", "").lower() == "admin":
        return redirect(url_for("dashboard.administrador"))

    if session.get("perfil_activo", "").lower() != "fiscalizador":
        return acceso_no_autorizado()

    cursor = None

    try:
        cursor = conexion.cursor(dictionary=True)

        # Obtener datos desde la vista resumen_guardias
        cursor.execute("""
            SELECT id_guardia, fecha_guardia, tipo_dia, asistencia
            FROM resumen_guardias
            WHERE id_usuario = (
                SELECT id_usuario FROM usuarios WHERE usuario = %s
            )
              AND YEAR(fecha_guardia) = YEAR(CURDATE())
            ORDER BY fecha_guardia DESC
        """, (session["usuario"],))

        datos = cursor.fetchall()

        for d in datos:
            fecha = d.get('fecha_guardia')
            if isinstance(fecha, date):
                d['dia_semana'] = DIAS_ES[fecha.weekday()]
            else:
                d['dia_semana'] = str(fecha) if fecha else '—'

        # NOTIFICACIONES
        cursor.execute("""
            SELECT id_notificacion, titulo, mensaje, fecha_creacion, leida
            FROM notificaciones
            WHERE id_usuario = (
                SELECT id_usuario FROM usuarios WHERE usuario = %s
            )
            ORDER BY fecha_creacion DESC
            LIMIT 20
        """, (session["usuario"],))
        notificaciones = cursor.fetchall()

        for n in notificaciones:
            fc = n.get('fecha_creacion')
            if isinstance(fc, datetime):
                n['fecha_creacion_str'] = fc.strftime('%d/%m/%Y %H:%M')
            elif isinstance(fc, date):
                n['fecha_creacion_str'] = fc.strftime('%d/%m/%Y')
            else:
                n['fecha_creacion_str'] = str(fc) if fc else '—'

        notif_no_leidas = sum(1 for n in notificaciones if not n.get('leida'))

        # ALERTAS DE VACACIONES (para el panel de notificaciones)
        cursor.execute("""
            SELECT
                u.id_usuario,
                CONCAT(u.nombre,' ',u.apellidos) AS nombre,
                DATE_ADD(u.fecha_ingreso, INTERVAL GREATEST(1, YEAR(CURDATE()) - YEAR(u.fecha_ingreso)) YEAR) AS fecha_vacaciones,
                DATEDIFF(
                    DATE_ADD(u.fecha_ingreso, INTERVAL GREATEST(1, YEAR(CURDATE()) - YEAR(u.fecha_ingreso)) YEAR),
                    CURDATE()
                ) AS dias_faltantes,
                COALESCE(
                    (SELECT SUM(DATEDIFF(v.fecha_fin, v.fecha_inicio) + 1)
                     FROM vacaciones v
                     WHERE v.id_usuario = u.id_usuario
                       AND v.fecha_inicio >= DATE_ADD(u.fecha_ingreso, INTERVAL TIMESTAMPDIFF(YEAR, u.fecha_ingreso, CURDATE()) YEAR) AND v.fecha_inicio <= CURDATE()
                    ), 0
                ) AS dias_tomados,
                TIMESTAMPDIFF(YEAR, u.fecha_ingreso, CURDATE()) * 30 AS total_acumulado,
                COALESCE(
                    (SELECT SUM(DATEDIFF(v.fecha_fin, v.fecha_inicio) + 1)
                     FROM vacaciones v
                     WHERE v.id_usuario = u.id_usuario AND v.fecha_inicio <= CURDATE()), 0
                ) AS dias_tomados_total
            FROM usuarios u
            INNER JOIN usuarios_roles ur ON u.id_usuario = ur.id_usuario
            INNER JOIN roles r ON ur.id_rol = r.id_rol
            WHERE r.nombre_rol = 'fiscalizador'
              AND u.estado = 'activo'
              AND u.usuario = %s
        """, (session["usuario"],))
        alertas = cursor.fetchall()

        for a in alertas:
            pendientes_este_anio = max(0, 30 - a["dias_tomados"])
            a["dias_pendientes_este_anio"] = pendientes_este_anio
            a["dias_pendientes_anteriores"] = max(
                0,
                (a["total_acumulado"] - a["dias_tomados_total"]) - pendientes_este_anio
            )

        return render_template(
            "index.html",
            datos=datos,
            notificaciones=notificaciones,
            notif_no_leidas=notif_no_leidas,
            alertas=alertas
        )

    except Exception as e:
        print("ERROR PANEL FISCALIZADOR:", e)
        return render_template(
            "error.html",
            codigo="Error",
            titulo="Error del sistema",
            mensaje="Error interno del servidor. Intente nuevamente.",
            volver_url="/"
        ), 500

    finally:
        if cursor is not None:
            cursor.close()
