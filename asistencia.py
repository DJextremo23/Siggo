"""Rutas de gestión de asistencias (admin y fiscalizador)."""

from flask import render_template, request, session, redirect, url_for, flash
from conexion import conexion as _obtener_conexion
conexion = _obtener_conexion()
from limiter_instance import limiter
from utils import (
    acceso_no_autorizado, datos_invalidos, no_encontrado, error_interno,
    guardar_filtros, redirigir_con_filtros, registrar_auditoria,
)
from helpers import DIAS_ES
from datetime import date, datetime, timedelta


def registrar_rutas(app):
    @app.route("/asistencia/<int:id_guardia>/<estado>", methods=["POST"])
    @limiter.limit("10 per minute")
    def registrar_asistencia(id_guardia, estado):

        if "usuario" not in session:
            return redirect(url_for("home"))

        # Validar que el perfil activo sea admin
        if session.get("perfil_activo") != "admin":
            return acceso_no_autorizado()

        if estado not in ["asistio", "falta", "justificado"]:
            return datos_invalidos("Estado inválido")

        cursor = conexion.cursor()

        try:
            # Verificar que la guardia exista antes de insertar/actualizar asistencia
            cursor.execute("""
                SELECT id_guardia
                FROM guardias
                WHERE id_guardia = %s
            """, (id_guardia,))
            if not cursor.fetchone():
                return no_encontrado("Guardia no encontrada")

            cursor.execute("""
                SELECT id_asistencia
                FROM asistencia
                WHERE id_guardia = %s
            """, (id_guardia,))

            existe = cursor.fetchone()

            if existe:
                # Si la guardia ya tiene una compensación registrada, bloquear el cambio
                cursor.execute("""
                    SELECT id_compensacion
                    FROM compensaciones
                    WHERE id_guardia = %s
                """, (id_guardia,))

                if cursor.fetchone():
                    flash("No se puede cambiar la asistencia: elimina primero la compensación registrada", "error")
                    return redirigir_con_filtros("asistencia_admin", "filtro_asistencias")

                cursor.execute("""
                    UPDATE asistencia
                    SET estado = %s
                    WHERE id_guardia = %s
                """, (estado, id_guardia))
            else:
                cursor.execute("""
                    INSERT INTO asistencia (id_guardia, estado)
                    VALUES (%s, %s)
                """, (id_guardia, estado))

            conexion.commit()

            mensajes = {
                "asistio": "Asistencia marcada como 'Asistió'",
                "falta": "Asistencia marcada como 'Falta'",
                "justificado": "Asistencia marcada como 'Justificado'"
            }
            registrar_auditoria("asistencia_registrada", f"Asistencia guardia {id_guardia} -> {estado}")
            flash(mensajes.get(estado, "Asistencia actualizada"), "success")
            return redirigir_con_filtros("asistencia_admin", "filtro_asistencias")

        finally:
            cursor.close()


    # ADMIN — Listado de asistencias con filtros por usuario, fecha y estado

    @app.route("/asistencias")
    def asistencia_admin():

        if "usuario" not in session:
            return redirect(url_for("home"))

        # Validar que el perfil activo sea admin
        if session.get("perfil_activo") != "admin":
            return acceso_no_autorizado()

        guardar_filtros("filtro_asistencias")

        cursor = conexion.cursor(dictionary=True)

        id_usuario = request.args.get("id_usuario", "").strip()
        fecha_desde = request.args.get("fecha_desde", "").strip()
        fecha_hasta = request.args.get("fecha_hasta", "").strip()
        asistencia = request.args.get("asistencia", "").strip()
        tipo = request.args.get("tipo", "").strip()

        try:
            # =========================
            # 1. GUARDIAS CON FILTROS
            # =========================
            sql = "SELECT rg.*, u.foto, CONCAT(u.nombre, ' ', u.apellidos) AS fiscalizador FROM resumen_guardias rg LEFT JOIN usuarios u ON rg.id_usuario = u.id_usuario WHERE 1=1 "
            params = []

            if id_usuario:
                sql += " AND rg.id_usuario = %s "
                params.append(id_usuario)

            if fecha_desde:
                sql += " AND rg.fecha_guardia >= %s "
                params.append(fecha_desde)

            if fecha_hasta:
                sql += " AND rg.fecha_guardia <= %s "
                params.append(fecha_hasta)

            if tipo:
                sql += " AND rg.tipo = %s "
                params.append(tipo)

            if asistencia:
                if asistencia == "pendiente":
                    sql += " AND (rg.asistencia IS NULL OR rg.asistencia = '' OR rg.asistencia = 'pendiente' OR rg.asistencia NOT IN ('asistio','falta','justificado')) "
                else:
                    sql += " AND rg.asistencia = %s "
                    params.append(asistencia)

            sql += " ORDER BY rg.fecha_guardia DESC"

            cursor.execute(sql, tuple(params))
            guardias = cursor.fetchall()

            for g in guardias:
                fecha = g.get('fecha_guardia')
                if isinstance(fecha, date):
                    g['dia_semana'] = DIAS_ES[fecha.weekday()]
                else:
                    g['dia_semana'] = str(fecha) if fecha else '—'

            # =========================
            # 2. FISCALIZADORES
            # =========================
            cursor.execute("""
                SELECT DISTINCT
                    u.id_usuario,
                    u.nombre,
                    u.apellidos
                FROM usuarios u
                INNER JOIN usuarios_roles ur
                    ON u.id_usuario = ur.id_usuario
                INNER JOIN roles r
                    ON ur.id_rol = r.id_rol
                WHERE r.nombre_rol = 'fiscalizador'
                  AND u.estado = 'activo'
                ORDER BY u.nombre
            """)

            fiscalizadores = cursor.fetchall()

            return render_template(
                "asistencia_admin.html",
                guardias=guardias,
                fiscalizadores=fiscalizadores
            )

        finally:
            cursor.close()

    # ==========================
    # FISCALIZADOR — ASISTENCIA PROPIA
    # ==========================
    # Vista de asistencias del fiscalizador logueado

    @app.route("/mis_asistencias")
    def asistencia():

        if "usuario" not in session:
            return redirect(url_for("home"))

        if session.get("perfil_activo", "").lower() != "fiscalizador":
            return acceso_no_autorizado()

        fecha_desde = request.args.get("fecha_desde", "").strip()
        fecha_hasta = request.args.get("fecha_hasta", "").strip()
        asistencia = request.args.get("asistencia", "").strip()
        tipo = request.args.get("tipo", "").strip()

        cursor = conexion.cursor(dictionary=True)

        sql = "SELECT * FROM resumen_guardias WHERE id_usuario = %s "
        params = [session["id_usuario"]]

        if fecha_desde:
            sql += " AND fecha_guardia >= %s "
            params.append(fecha_desde)

        if fecha_hasta:
            sql += " AND fecha_guardia <= %s "
            params.append(fecha_hasta)

        if not fecha_desde and not fecha_hasta:
            sql += " AND YEAR(fecha_guardia) = YEAR(CURDATE()) "

        if asistencia:
            if asistencia == "pendiente":
                sql += " AND (asistencia IS NULL OR asistencia = '' OR asistencia = 'pendiente' OR asistencia NOT IN ('asistio','falta','justificado')) "
            else:
                sql += " AND asistencia = %s "
                params.append(asistencia)

        if tipo:
            sql += " AND tipo = %s "
            params.append(tipo)

        sql += " ORDER BY fecha_guardia DESC"

        cursor.execute(sql, params)
        datos = cursor.fetchall()

        for d in datos:
            fecha = d.get('fecha_guardia')
            if isinstance(fecha, date):
                d['dia_semana'] = DIAS_ES[fecha.weekday()]
            else:
                d['dia_semana'] = str(fecha) if fecha else '—'

        # Totales KPI del año actual (independientes de los filtros de la tabla)
        cursor.execute("""
            SELECT
                COALESCE(SUM(CASE WHEN asistencia = 'asistio' THEN 1 ELSE 0 END), 0) AS asistencias,
                COALESCE(SUM(CASE WHEN asistencia = 'falta' THEN 1 ELSE 0 END), 0) AS faltas,
                COALESCE(SUM(CASE WHEN asistencia = 'justificado' THEN 1 ELSE 0 END), 0) AS justificadas,
                COALESCE(SUM(CASE WHEN asistencia = 'sin registro' THEN 1 ELSE 0 END), 0) AS pendientes
            FROM resumen_guardias
            WHERE id_usuario = %s
              AND YEAR(fecha_guardia) = YEAR(CURDATE())
        """, (session["id_usuario"],))
        kpi = cursor.fetchone()

        return render_template(
            "asistencia.html",
            datos=datos,
            kpi=kpi
        )
    # ---------- FIN ASISTENCIA ----------

    # ==========================
    # FISCALIZADOR — MARCAR ASISTENCIA PROPIA
    # ==========================
    # Permite al fiscalizador registrar su asistencia del día

    @app.route("/registrar_asistencia")
    def mi_asistencia():

        if "usuario" not in session:
            return redirect(url_for("home"))

        if session.get("perfil_activo", "").lower() != "fiscalizador":
            return acceso_no_autorizado()

        cursor = conexion.cursor(dictionary=True)

        cursor.execute("""
            SELECT 
                id_guardia,
                fecha_guardia,
                tipo_dia,
                feriado,
                tipo,
                asistencia
            FROM resumen_guardias
            WHERE id_usuario = %s
            ORDER BY fecha_guardia DESC
        """, (session["id_usuario"],))

        datos = cursor.fetchall()

        hoy = date.today()
        limite = hoy - timedelta(days=1)  # ventana de 48 horas: permite hoy o ayer

        for d in datos:

            fecha = d["fecha_guardia"]

            if isinstance(fecha, date):
                d['dia_semana'] = DIAS_ES[fecha.weekday()]
            else:
                d['dia_semana'] = str(fecha) if fecha else '—'

            # 1. Ya registrado
            if d["asistencia"] != "sin registro":
                d["estado_accion"] = "registrado"

            # 2. Futuro
            elif fecha > hoy:
                d["estado_accion"] = "futuro"

            # 3. Dentro de 48 horas (hoy o ayer) — activo
            elif fecha >= limite:
                d["estado_accion"] = "hoy"

            # 4. Pasado sin registro (fuera de las 48 horas)
            else:
                d["estado_accion"] = "cerrado"

        cursor.close()

        return render_template("asistencia_fiscalizadores.html", datos=datos)

    # FISCALIZADOR — Registrar asistencia vía POST (autoservicio)
    @app.route("/marcar_asistencia", methods=["POST"])
    @limiter.limit("10 per minute")
    def marcar_asistencia():

        if "usuario" not in session:
            return redirect(url_for("home"))

        if session.get("perfil_activo", "").lower() != "fiscalizador":
            return acceso_no_autorizado()

        id_guardia = request.form.get("id_guardia")
        id_usuario = session.get("id_usuario")

        if not id_guardia or not id_usuario:
            return datos_invalidos("Datos inválidos")

        # Usar la conexión global (no get_connection)
        conn = conexion
        cursor = conn.cursor(dictionary=True)

        try:

            # Verificar que la guardia pertenece al fiscalizador y es de hoy
            cursor.execute("""
                SELECT id_guardia, fecha_guardia
                FROM guardias
                WHERE id_guardia = %s AND id_usuario = %s
            """, (id_guardia, id_usuario))

            guardia = cursor.fetchone()

            if not guardia:
                return acceso_no_autorizado()

            hoy = date.today()
            limite = hoy - timedelta(days=1)  # ventana de 48 horas
            if not (limite <= guardia["fecha_guardia"] <= hoy):
                return redirect(url_for("mi_asistencia"))

            # Verificar si ya registró
            cursor.execute("""
                SELECT id_asistencia
                FROM asistencia
                WHERE id_guardia = %s
            """, (id_guardia,))

            existe = cursor.fetchone()

            if existe:
                flash("Ya registraste tu asistencia", "warning")
                return redirect(url_for("mi_asistencia"))

            # Insertar asistencia
            cursor.execute("""
                INSERT INTO asistencia (id_guardia, estado)
                VALUES (%s, 'asistio')
            """, (id_guardia,))

            conn.commit()

            registrar_auditoria("asistencia_autoservicio", f"Asistencia registrada: guardia {id_guardia}")
            flash("Asistencia registrada correctamente", "success")

            return redirect(url_for("mi_asistencia"))

        except Exception as e:
            conn.rollback()
            print("ERROR marcar_asistencia:", e)
            return error_interno()

        finally:
            cursor.close()


    # ==========================
    # FISCALIZADOR — MIS COMPENSACIONES
    # ==========================
    # Listado de compensaciones propias con filtros


