from flask import Blueprint, request, render_template, redirect, session, url_for, flash
from datetime import datetime, date
import mysql.connector.errors
from db import conexion
from utils import (
    guardar_filtros,
    redirigir_con_filtros,
    error_interno,
    no_encontrado,
    datos_invalidos,
    acceso_no_autorizado,
)

"""
Blueprint de vacaciones: CRUD del panel admin y vacaciones propias del fiscalizador.
"""

vacaciones_bp = Blueprint("vacaciones", __name__)


# ==========================
# ADMIN — VACACIONES
# ==========================
# Listado, asignación, edición y eliminación de vacaciones con validaciones
@vacaciones_bp.route("/vacaciones")
def vacaciones():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
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
        anio_alerta = request.args.get("anio_alerta", "").strip()
        desde_alerta = request.args.get("desde_alerta", "").strip()
        hasta_alerta = request.args.get("hasta_alerta", "").strip()
        fiscalizador_alerta = request.args.get("fiscalizador_alerta", "").strip()
        estado_alerta = request.args.get("estado_alerta", "").strip()

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
            filtro_vac += " AND YEAR(v.fecha_inicio) = %s"
            params_vac.append(int(anio_vac))
        if desde_vac:
            filtro_vac += " AND v.fecha_inicio >= %s"
            params_vac.append(desde_vac)
        if hasta_vac:
            filtro_vac += " AND v.fecha_fin <= %s"
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
        # ALERTAS
        # =========================
        filtro_alerta = ""
        params_alerta = []

        if anio_alerta:
            filtro_alerta += " AND YEAR(DATE_ADD(u.fecha_ingreso, INTERVAL GREATEST(1, YEAR(CURDATE()) - YEAR(u.fecha_ingreso)) YEAR)) = %s"
            params_alerta.append(int(anio_alerta))
        if desde_alerta:
            filtro_alerta += " AND DATE_ADD(u.fecha_ingreso, INTERVAL GREATEST(1, YEAR(CURDATE()) - YEAR(u.fecha_ingreso)) YEAR) >= %s"
            params_alerta.append(desde_alerta)
        if hasta_alerta:
            filtro_alerta += " AND DATE_ADD(u.fecha_ingreso, INTERVAL GREATEST(1, YEAR(CURDATE()) - YEAR(u.fecha_ingreso)) YEAR) <= %s"
            params_alerta.append(hasta_alerta)
        if fiscalizador_alerta:
            filtro_alerta += " AND u.id_usuario = %s"
            params_alerta.append(int(fiscalizador_alerta))
        if estado_alerta == "LISTO":
            filtro_alerta += " AND DATEDIFF(DATE_ADD(u.fecha_ingreso, INTERVAL GREATEST(1, YEAR(CURDATE()) - YEAR(u.fecha_ingreso)) YEAR), CURDATE()) <= 30"
        elif estado_alerta == "PRÓXIMO":
            filtro_alerta += " AND DATEDIFF(DATE_ADD(u.fecha_ingreso, INTERVAL GREATEST(1, YEAR(CURDATE()) - YEAR(u.fecha_ingreso)) YEAR), CURDATE()) BETWEEN 31 AND 90"
        elif estado_alerta == "NORMAL":
            filtro_alerta += " AND DATEDIFF(DATE_ADD(u.fecha_ingreso, INTERVAL GREATEST(1, YEAR(CURDATE()) - YEAR(u.fecha_ingreso)) YEAR), CURDATE()) > 90"

        cursor.execute(f"""
            SELECT 
                u.id_usuario,
                CONCAT(u.nombre,' ',u.apellidos) AS nombre,
                DATE_ADD(u.fecha_ingreso, INTERVAL GREATEST(1, YEAR(CURDATE()) - YEAR(u.fecha_ingreso)) YEAR) AS fecha_vacaciones,
                DATEDIFF(
                    DATE_ADD(u.fecha_ingreso, INTERVAL GREATEST(1, YEAR(CURDATE()) - YEAR(u.fecha_ingreso)) YEAR),
                    CURDATE()
                ) AS dias_faltantes,
                TIMESTAMPDIFF(YEAR, u.fecha_ingreso, CURDATE()) * 30 AS total_acumulado,
                COALESCE(
                    (SELECT SUM(DATEDIFF(v.fecha_fin, v.fecha_inicio) + 1)
                     FROM vacaciones v
                     WHERE v.id_usuario = u.id_usuario AND v.fecha_inicio <= CURDATE()), 0
                ) AS dias_tomados_total,
                COALESCE(
                    (SELECT SUM(DATEDIFF(v.fecha_fin, v.fecha_inicio) + 1)
                     FROM vacaciones v
                     WHERE v.id_usuario = u.id_usuario
                       AND v.fecha_inicio >= DATE_ADD(u.fecha_ingreso, INTERVAL TIMESTAMPDIFF(YEAR, u.fecha_ingreso, CURDATE()) YEAR) AND v.fecha_inicio <= CURDATE()), 0
                ) AS dias_tomados_anio
            FROM usuarios u
            INNER JOIN usuarios_roles ur ON u.id_usuario = ur.id_usuario
            INNER JOIN roles r ON ur.id_rol = r.id_rol
            WHERE r.nombre_rol = 'fiscalizador' {filtro_alerta}
            ORDER BY dias_faltantes ASC
        """, params_alerta)

        alertas = cursor.fetchall()

        for a in alertas:
            pendientes_este_anio = max(0, 30 - a["dias_tomados_anio"])
            a["dias_pendientes_anteriores"] = max(
                0,
                (a["total_acumulado"] - a["dias_tomados_total"]) - pendientes_este_anio
            )

        return render_template(
            "vacaciones.html",
            vacaciones=vacaciones,
            usuarios=usuarios,
            alertas=alertas
        )
    except Exception as e:
        print("ERROR vacaciones:", e)
        return error_interno()
    finally:
        if cursor is not None:
            cursor.close()


@vacaciones_bp.route("/editar_vacacion/<int:id>")
def editar_vacacion(id):

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
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


@vacaciones_bp.route("/actualizar_vacacion/<int:id>", methods=["POST"])
def actualizar_vacacion(id):

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    inicio = request.form.get("fecha_inicio", "").strip()
    fin = request.form.get("fecha_fin", "").strip()

    if not inicio or not fin:
        flash("Todos los campos son obligatorios", "error")
        return redirigir_con_filtros("vacaciones.vacaciones", "filtro_vacaciones")

    try:
        inicio_dt = datetime.strptime(inicio, "%Y-%m-%d").date()
        fin_dt = datetime.strptime(fin, "%Y-%m-%d").date()
    except ValueError:
        flash("Formato de fecha inválido. Use YYYY-MM-DD", "error")
        return redirigir_con_filtros("vacaciones.vacaciones", "filtro_vacaciones")

    if inicio_dt > fin_dt:
        flash("Fecha inválida: inicio mayor que fin", "error")
        return redirigir_con_filtros("vacaciones.vacaciones", "filtro_vacaciones")

    cursor = conexion.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT id_usuario, fecha_inicio AS fecha_inicio_ant, fecha_fin AS fecha_fin_ant,
                   (SELECT CONCAT(nombre,' ',apellidos) FROM usuarios WHERE id_usuario = vacaciones.id_usuario) AS nombre_completo
            FROM vacaciones
            WHERE id_vacacion = %s
        """, (id,))
        old_data = cursor.fetchone()

        if not old_data:
            return no_encontrado("Vacación no encontrada")

        id_usuario = old_data["id_usuario"]

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
            return redirigir_con_filtros("vacaciones.vacaciones", "filtro_vacaciones")

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
            return redirigir_con_filtros("vacaciones.vacaciones", "filtro_vacaciones")

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
                f"Vacaciones modificadas: del {inicio} al {fin} (antes: {old_data['fecha_inicio_ant']} al {old_data['fecha_fin_ant']}) para {old_data['nombre_completo']}."
            ))

        conexion.commit()

        flash("Vacaciones actualizadas correctamente", "success")
        return redirigir_con_filtros("vacaciones.vacaciones", "filtro_vacaciones")

    except Exception as e:
        conexion.rollback()
        print("ERROR actualizar_vacacion:", e)
        return error_interno()

    finally:
        cursor.close()


# ADMIN — Guardar nueva vacación (validando antigüedad, cruces y guardias)
@vacaciones_bp.route("/guardar_vacacion", methods=["POST"])
def guardar_vacacion():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    id_usuario = request.form.get("id_usuario", "").strip()
    inicio = request.form.get("fecha_inicio", "").strip()
    fin = request.form.get("fecha_fin", "").strip()

    if not id_usuario or not inicio or not fin:
        flash("Todos los campos son obligatorios", "error")
        return redirigir_con_filtros("vacaciones.vacaciones", "filtro_vacaciones")

    try:
        inicio_dt = datetime.strptime(inicio, "%Y-%m-%d").date()
        fin_dt = datetime.strptime(fin, "%Y-%m-%d").date()
    except ValueError:
        flash("Formato de fecha inválido. Use YYYY-MM-DD", "error")
        return redirigir_con_filtros("vacaciones.vacaciones", "filtro_vacaciones")

    if inicio_dt > fin_dt:
        flash("Fecha inválida: inicio mayor que fin", "error")
        return redirigir_con_filtros("vacaciones.vacaciones", "filtro_vacaciones")

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
            return redirigir_con_filtros("vacaciones.vacaciones", "filtro_vacaciones")

        if user["estado"] != "activo":
            flash("No se pueden registrar vacaciones para un usuario inactivo", "error")
            return redirigir_con_filtros("vacaciones.vacaciones", "filtro_vacaciones")

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
            return redirigir_con_filtros("vacaciones.vacaciones", "filtro_vacaciones")

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
            return redirigir_con_filtros("vacaciones.vacaciones", "filtro_vacaciones")

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
            return redirigir_con_filtros("vacaciones.vacaciones", "filtro_vacaciones")

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
            SELECT CONCAT(nombre,' ',apellidos) AS nombre_completo
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
                f"Se han registrado vacaciones del {inicio} al {fin} para {user_info['nombre_completo']}."
            ))

        conexion.commit()

        flash("Vacaciones registradas correctamente", "success")
        return redirigir_con_filtros("vacaciones.vacaciones", "filtro_vacaciones")

    except mysql.connector.errors.OperationalError as e:
        conexion.rollback()
        if e.errno == 1205:
            flash("La base de datos está ocupada, intente nuevamente en unos segundos", "error")
        else:
            flash("Error de conexión con la base de datos, intente nuevamente", "error")
        return redirigir_con_filtros("vacaciones.vacaciones", "filtro_vacaciones")

    except Exception as e:
        conexion.rollback()
        print("ERROR guardar_vacacion:", e)
        flash("Ocurrió un error al registrar las vacaciones. Intente nuevamente.", "error")
        return redirigir_con_filtros("vacaciones.vacaciones", "filtro_vacaciones")

    finally:
        cursor.close()


# ADMIN — Eliminar vacación (con notificación al usuario)
@vacaciones_bp.route("/eliminar_vacacion/<int:id>", methods=["POST"])
def eliminar_vacacion(id):

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    cursor = conexion.cursor(dictionary=True)

    try:
        # Obtener datos antes de eliminar
        cursor.execute("""
            SELECT v.id_usuario, v.fecha_inicio, v.fecha_fin,
                   CONCAT(u.nombre,' ',u.apellidos) AS nombre_completo
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
                f"Se han eliminado las vacaciones del {vac_data['fecha_inicio']} al {vac_data['fecha_fin']} de {vac_data['nombre_completo']}."
            ))

        conexion.commit()

        flash("Vacaciones eliminadas correctamente", "success")
        return redirigir_con_filtros("vacaciones.vacaciones", "filtro_vacaciones")

    except Exception as e:
        conexion.rollback()
        print("ERROR eliminar_vacacion:", e)
        return error_interno()

    finally:
        cursor.close()


@vacaciones_bp.route("/eliminar_vacaciones", methods=["POST"])
def eliminar_vacaciones():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    ids = request.form.getlist("ids_vacacion")
    if not ids:
        flash("No seleccionó ninguna vacación", "error")
        return redirigir_con_filtros("vacaciones.vacaciones", "filtro_vacaciones")

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
                   CONCAT(u.nombre,' ',u.apellidos) AS nombre_completo
            FROM vacaciones v
            INNER JOIN usuarios u ON v.id_usuario = u.id_usuario
            WHERE v.id_vacacion IN ({placeholders})
        """, tuple(ids_int))
        vacaciones_data = cursor.fetchall()

        cursor.execute(
            f"DELETE FROM vacaciones WHERE id_vacacion IN ({placeholders})",
            tuple(ids_int),
        )

        for vac in vacaciones_data:
            cursor.execute("""
                INSERT INTO notificaciones (id_usuario, titulo, mensaje)
                VALUES (%s, %s, %s)
            """, (
                vac["id_usuario"],
                "Vacaciones eliminadas",
                f"Se han eliminado las vacaciones del {vac['fecha_inicio']} al {vac['fecha_fin']} de {vac['nombre_completo']}."
            ))

        conexion.commit()
        flash(f"Se eliminaron {len(vacaciones_data)} vacaciones correctamente", "success")
    except Exception as e:
        conexion.rollback()
        print("ERROR ELIMINAR VACACIONES:", e)
        flash("Ocurrió un error al eliminar las vacaciones", "error")
    finally:
        cursor.close()

    return redirigir_con_filtros("vacaciones.vacaciones", "filtro_vacaciones")


# ==========================
# FISCALIZADOR — MIS VACACIONES
# ==========================
# Vacaciones propias con filtros y cálculo de días pendientes
@vacaciones_bp.route("/mis_vacaciones")
def mis_vacaciones():

    # =========================
    # VALIDACIÓN DE SESIÓN
    # =========================
    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "fiscalizador":
        return acceso_no_autorizado()

    # conexion YA ES OBJETO (NO SE USA ())
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
            conditions.append("YEAR(v.fecha_inicio) = %s")
            params.append(int(anio))

        if fecha_desde:
            conditions.append("v.fecha_inicio >= %s")
            params.append(fecha_desde)

        if fecha_hasta:
            conditions.append("v.fecha_fin <= %s")
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
            SELECT TIMESTAMPDIFF(YEAR, fecha_ingreso, CURDATE()) * 30 AS total_dias
            FROM usuarios WHERE id_usuario = %s
        """, (session["id_usuario"],))

        user = cursor.fetchone()
        dias_pendientes = 0
        dias_pendientes_este_anio = 0
        dias_pendientes_anteriores = 0

        if user and user["total_dias"] is not None:
            total_dias = user["total_dias"]

            cursor.execute("""
                SELECT 
                    COALESCE(SUM(DATEDIFF(v.fecha_fin, v.fecha_inicio) + 1), 0) AS total,
                    COALESCE(SUM(CASE WHEN v.fecha_inicio >= DATE_ADD(u.fecha_ingreso, INTERVAL TIMESTAMPDIFF(YEAR, u.fecha_ingreso, CURDATE()) YEAR)
                                      THEN DATEDIFF(v.fecha_fin, v.fecha_inicio) + 1
                                      ELSE 0 END), 0) AS total_anio
                FROM vacaciones v
                JOIN usuarios u ON u.id_usuario = v.id_usuario
                WHERE v.id_usuario = %s
                  AND v.fecha_inicio <= CURDATE()
            """, (session["id_usuario"],))

            result = cursor.fetchone()
            dias_tomados = result["total"] if result else 0
            dias_tomados_anio = result["total_anio"] if result else 0

            dias_pendientes_este_anio = max(0, min(30, total_dias) - dias_tomados_anio)
            dias_pendientes_anteriores = max(0, (total_dias - dias_tomados) - dias_pendientes_este_anio)
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
