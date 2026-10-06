"""Rutas de gestión de compensaciones (admin y fiscalizador)."""

from flask import render_template, request, session, redirect, url_for, flash
from conexion import conexion
from limiter_instance import limiter
from utils import (
    acceso_no_autorizado, datos_invalidos, no_encontrado, error_interno,
    error_response, guardar_filtros, redirigir_con_filtros, registrar_auditoria,
)
from helpers import DIAS_ES
from datetime import datetime, date


def registrar_rutas(app):
    @app.route("/compensaciones")
    def compensaciones_admin():

        if "usuario" not in session:
            return redirect(url_for("home"))

        if session.get("perfil_activo") != "admin":
            return acceso_no_autorizado()

        guardar_filtros("filtro_compensaciones")

        id_usuario = request.args.get("id_usuario", "").strip()
        anio = request.args.get("anio", "").strip()
        desde = request.args.get("desde", "").strip()
        hasta = request.args.get("hasta", "").strip()
        estado_filtro = request.args.get("estado", "").strip()
        tipo = request.args.get("tipo", "").strip()

        cursor = None
        try:
            cursor = conexion.cursor(dictionary=True)

            sql = """
                SELECT
                    g.id_guardia,
                    CONCAT(u.nombre,' ',u.apellidos) AS fiscalizador,
                    u.foto,
                    g.fecha_guardia,
                    g.tipo,
                    COALESCE(a.estado,'sin registro') AS asistencia,
                    c.fecha_compensacion,
                    c.observacion,
                    CASE
                        WHEN f.id_feriado IS NOT NULL
                        THEN 'FERIADO'
                        ELSE ELT(DAYOFWEEK(g.fecha_guardia),
                            'Domingo','Lunes','Martes','Miércoles',
                            'Jueves','Viernes','Sábado')
                    END AS tipo_dia,
                    f.descripcion AS feriado
                FROM guardias g
                LEFT JOIN usuarios u
                    ON g.id_usuario = u.id_usuario
                LEFT JOIN asistencia a
                    ON g.id_guardia = a.id_guardia
                LEFT JOIN compensaciones c
                    ON g.id_guardia = c.id_guardia
                LEFT JOIN feriados f
                    ON g.fecha_guardia = f.fecha
                WHERE 1=1
            """

            params = []

            if anio:
                sql += " AND YEAR(g.fecha_guardia) = %s "
                params.append(int(anio))

            if desde:
                sql += " AND g.fecha_guardia >= %s "
                params.append(desde)

            if hasta:
                sql += " AND g.fecha_guardia <= %s "
                params.append(hasta)

            if estado_filtro == "pendiente":
                sql += " AND (a.estado IS NULL OR a.estado = '' OR a.estado NOT IN ('asistio','falta','justificado')) "
            elif estado_filtro:
                sql += " AND a.estado = %s "
                params.append(estado_filtro)

            if tipo:
                sql += " AND g.tipo = %s "
                params.append(tipo)

            if id_usuario:
                sql += " AND g.id_usuario = %s "
                params.append(id_usuario)

            sql += """
                ORDER BY g.fecha_guardia DESC
            """

            cursor.execute(sql, params)

            data = cursor.fetchall()

            for g in data:
                fecha = g.get('fecha_guardia')
                if isinstance(fecha, date):
                    g['dia_semana'] = DIAS_ES[fecha.weekday()]
                else:
                    g['dia_semana'] = str(fecha) if fecha else '—'

            cursor.execute("""
                SELECT DISTINCT
                    u.id_usuario,
                    u.nombre,
                    u.apellidos
                FROM usuarios u
                INNER JOIN usuarios_roles ur ON u.id_usuario = ur.id_usuario
                INNER JOIN roles r ON ur.id_rol = r.id_rol
                WHERE r.nombre_rol = 'fiscalizador'
                  AND u.estado = 'activo'
                ORDER BY u.nombre
            """)
            fiscalizadores = cursor.fetchall()

            return render_template(
                "compensaciones.html",
                guardias=data,
                fiscalizadores=fiscalizadores
            )
        except Exception as e:
            print("ERROR compensaciones:", e)
            return error_interno()
        finally:
            if cursor is not None:
                cursor.close()

    @app.route("/editar_compensacion/<int:id_guardia>")
    def editar_compensacion(id_guardia):

        if "usuario" not in session:
            return redirect(url_for("home"))

        if session.get("perfil_activo") != "admin":
            return acceso_no_autorizado()

        cursor = conexion.cursor(dictionary=True)

        try:

            cursor.execute("""
                SELECT
                    g.id_guardia,
                    CONCAT(u.nombre,' ',u.apellidos) AS fiscalizador,
                    g.fecha_guardia,

                    CASE
                        WHEN f.id_feriado IS NOT NULL
                        THEN 'FERIADO'
                        ELSE ELT(DAYOFWEEK(g.fecha_guardia),
                            'Domingo','Lunes','Martes','Miércoles',
                            'Jueves','Viernes','Sábado')
                    END AS tipo_dia,

                    COALESCE(a.estado,'sin registro') AS asistencia,

                    c.fecha_compensacion,
                    c.observacion

                FROM guardias g

                LEFT JOIN usuarios u
                    ON g.id_usuario = u.id_usuario

                LEFT JOIN asistencia a
                    ON g.id_guardia = a.id_guardia

                LEFT JOIN compensaciones c
                    ON g.id_guardia = c.id_guardia

                LEFT JOIN feriados f
                    ON g.fecha_guardia = f.fecha

                WHERE g.id_guardia = %s
            """, (id_guardia,))

            data = cursor.fetchone()

            if not data:
                return no_encontrado("Compensación no encontrada")

            return render_template(
                "editar_compensaciones.html",
                data=data
            )

        finally:
            cursor.close()

    @app.route("/editar_compensacion/<int:id_guardia>", methods=["POST"])
    @limiter.limit("10 per minute")
    def guardar_edicion_compensacion(id_guardia):

        if "usuario" not in session:
            return redirect(url_for("home"))

        if session.get("perfil_activo") != "admin":
            return acceso_no_autorizado()

        fecha = request.form.get("fecha_compensacion", "").strip()
        observacion = request.form.get("observacion", "").strip()

        cursor = conexion.cursor()

        try:

            # Verificar asistencia
            cursor.execute("""
                SELECT estado
                FROM asistencia
                WHERE id_guardia = %s
            """, (id_guardia,))

            estado = cursor.fetchone()

            if not estado:
                return datos_invalidos("No existe registro de asistencia")

            if estado[0] != "asistio":
                return error_response("Solo se puede generar compensación para asistencias válidas")

            cursor.execute("""
                SELECT fecha_guardia
                FROM guardias
                WHERE id_guardia = %s
            """, (id_guardia,))

            fila_guardia = cursor.fetchone()

            if not fila_guardia:
                return no_encontrado("Guardia no encontrada")

            fecha_guardia = fila_guardia[0]

            try:
                fecha_compensacion = datetime.strptime(fecha, "%Y-%m-%d").date()
            except ValueError:
                flash("Formato de fecha inválido. Use YYYY-MM-DD", "error")
                return redirigir_con_filtros("compensaciones_admin", "filtro_compensaciones")

            if fecha_compensacion < fecha_guardia:
                flash("La fecha de compensación no puede ser anterior a la fecha de la guardia", "error")
                return redirigir_con_filtros("compensaciones_admin", "filtro_compensaciones")

            # Insertar o actualizar compensación
            cursor.execute("""
                INSERT INTO compensaciones (
                    id_guardia,
                    fecha_compensacion,
                    observacion
                )
                VALUES (%s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    fecha_compensacion = VALUES(fecha_compensacion),
                    observacion = VALUES(observacion)
            """, (
                id_guardia,
                fecha,
                observacion
            ))

            conexion.commit()

            registrar_auditoria("compensacion_registrada", f"Compensación guardada: guardia {id_guardia}")
            flash("Compensación registrada correctamente", "success")
            return redirigir_con_filtros("compensaciones_admin", "filtro_compensaciones")

        except Exception as e:

            conexion.rollback()

            print("ERROR EDITAR COMPENSACIÓN:", e)

            return error_interno()

        finally:

            cursor.close()

    @app.route("/eliminar_compensacion/<int:id_guardia>", methods=["POST"])
    @limiter.limit("10 per minute")
    def eliminar_compensacion(id_guardia):

        if "usuario" not in session:
            return redirect(url_for("home"))

        if session.get("perfil_activo") != "admin":
            return acceso_no_autorizado()

        cursor = conexion.cursor()

        try:

            cursor.execute("""
                DELETE FROM compensaciones
                WHERE id_guardia = %s
            """, (id_guardia,))

            conexion.commit()

            registrar_auditoria("compensacion_eliminada", f"Compensación eliminada: guardia {id_guardia}")
            flash("Compensación eliminada correctamente", "success")
            return redirigir_con_filtros("compensaciones_admin", "filtro_compensaciones")

        except Exception as e:

            conexion.rollback()

            print("ERROR ELIMINAR COMPENSACIÓN:", e)

            return error_interno()

        finally:

            cursor.close()

    @app.route("/eliminar_compensaciones", methods=["POST"])
    @limiter.limit("10 per minute")
    def eliminar_compensaciones():

        if "usuario" not in session:
            return redirect(url_for("home"))

        if session.get("perfil_activo") != "admin":
            return acceso_no_autorizado()

        ids = request.form.getlist("ids_compensacion")
        if not ids:
            flash("No seleccionó ninguna compensación", "error")
            return redirigir_con_filtros("compensaciones_admin", "filtro_compensaciones")

        ids_int = []
        for valor in ids:
            try:
                ids_int.append(int(valor))
            except (TypeError, ValueError):
                return datos_invalidos("Identificador de compensación inválido")

        cursor = conexion.cursor()

        try:
            placeholders = ",".join(["%s"] * len(ids_int))
            cursor.execute(
                f"DELETE FROM compensaciones WHERE id_guardia IN ({placeholders})",
                tuple(ids_int),
            )
            conexion.commit()
            registrar_auditoria("compensaciones_eliminadas", f"Eliminadas {cursor.rowcount} compensaciones")
            flash(f"Se eliminaron {cursor.rowcount} compensaciones correctamente", "success")
        except Exception as e:
            conexion.rollback()
            print("ERROR ELIMINAR COMPENSACIONES:", e)
            flash("Ocurrió un error al eliminar las compensaciones", "error")
        finally:
            cursor.close()

        return redirigir_con_filtros("compensaciones_admin", "filtro_compensaciones")



    @app.route("/mis_compensaciones")
    def mis_compensaciones():

        if "usuario" not in session:
            return redirect(url_for("login.login"))

        if (session.get("perfil_activo") or "").lower() != "fiscalizador":
            return acceso_no_autorizado()

        guardar_filtros("filtro_mis_compensaciones")

        anio = request.args.get("anio", "").strip()
        desde = request.args.get("desde", "").strip()
        hasta = request.args.get("hasta", "").strip()
        estado = request.args.get("estado", "")
        tipo = request.args.get("tipo", "").strip()

        cursor = conexion.cursor(dictionary=True)

        query = """
            SELECT
                c.id_compensacion,
                g.fecha_guardia,
                g.tipo,
                c.fecha_compensacion,
                c.observacion,
                g.id_usuario,
                u.nombre,
                u.apellidos,
                f.descripcion AS feriado
            FROM compensaciones c
            INNER JOIN guardias g
                ON c.id_guardia = g.id_guardia
            INNER JOIN usuarios u
                ON g.id_usuario = u.id_usuario
            LEFT JOIN feriados f
                ON g.fecha_guardia = f.fecha
            WHERE g.id_usuario = %s
        """

        parametros = [session["id_usuario"]]

        if anio:
            query += " AND YEAR(g.fecha_guardia) = %s"
            parametros.append(int(anio))

        if desde:
            query += " AND g.fecha_guardia >= %s"
            parametros.append(desde)

        if hasta:
            query += " AND g.fecha_guardia <= %s"
            parametros.append(hasta)

        if tipo:
            query += " AND g.tipo = %s"
            parametros.append(tipo)

        query += " ORDER BY g.fecha_guardia DESC"

        cursor.execute(query, tuple(parametros))

        datos = cursor.fetchall()

        cursor.close()

        from datetime import date
        hoy = date.today()
        for c in datos:
            fecha = c['fecha_guardia']
            if isinstance(fecha, date):
                c['dia_semana'] = DIAS_ES[fecha.weekday()]
            else:
                c['dia_semana'] = str(fecha) if fecha else '—'
            c['es_feriado'] = c.get('feriado') is not None
            if c['fecha_compensacion'] and c['fecha_compensacion'] <= hoy:
                c['estado'] = 'usado'
            else:
                c['estado'] = 'pendiente'

        if estado:
            datos = [c for c in datos if c['estado'] == estado]

        return render_template(
            "mi_compensaciones.html",
            compensaciones=datos
        )

    # FISCALIZADOR — Editar compensación (GET: formulario, POST: actualizar)
    @app.route("/editar_mi_compensacion/<int:id_compensacion>", methods=["GET"])
    def editar_mi_compensacion(id_compensacion):

        if "usuario" not in session:
            return redirect(url_for("login.login"))

        if (session.get("perfil_activo") or "").lower() != "fiscalizador":
            return acceso_no_autorizado()

        cursor = conexion.cursor(dictionary=True)

        try:
            cursor.execute("""
                SELECT
                    c.id_compensacion,
                    c.fecha_compensacion,
                    c.observacion,
                    g.id_usuario,
                    g.fecha_guardia
                FROM compensaciones c
                INNER JOIN guardias g ON c.id_guardia = g.id_guardia
                WHERE c.id_compensacion = %s
            """, (id_compensacion,))

            compensacion = cursor.fetchone()

            if not compensacion:
                return no_encontrado()

            if compensacion["id_usuario"] != session.get("id_usuario"):
                return acceso_no_autorizado()

            return render_template("editar_mi_compensacion.html",
                                   compensacion=compensacion)

        finally:
            cursor.close()


    # FISCALIZADOR — Actualizar compensación propia (POST)
    @app.route("/actualizar_mi_compensacion/<int:id_compensacion>", methods=["POST"])
    @limiter.limit("10 per minute")
    def actualizar_mi_compensacion(id_compensacion):

        if "usuario" not in session:
            return redirect(url_for("login.login"))

        if (session.get("perfil_activo") or "").lower() != "fiscalizador":
            return acceso_no_autorizado()

        cursor = conexion.cursor()

        try:
            fecha = request.form.get("fecha_compensacion")
            obs = request.form.get("observacion")

            if not fecha:
                return datos_invalidos("La fecha de compensación es obligatoria")

            cursor.execute("""
                SELECT g.id_usuario, g.fecha_guardia FROM compensaciones c
                INNER JOIN guardias g ON c.id_guardia = g.id_guardia
                WHERE c.id_compensacion = %s
            """, (id_compensacion,))
            row = cursor.fetchone()
            if not row:
                return no_encontrado()
            if row[0] != session.get("id_usuario"):
                return acceso_no_autorizado()
            fecha_guardia = row[1]

            from datetime import date
            try:
                fecha_compensacion = date.fromisoformat(fecha)
            except ValueError:
                flash("Formato de fecha inválido. Use YYYY-MM-DD", "error")
                compensacion = {
                    "id_compensacion": id_compensacion,
                    "fecha_compensacion": fecha,
                    "observacion": obs,
                    "fecha_guardia": fecha_guardia,
                }
                return render_template("editar_mi_compensacion.html",
                                       compensacion=compensacion)

            if fecha_compensacion < fecha_guardia:
                flash("La fecha de compensación no puede ser anterior a la fecha de la guardia", "error")
                compensacion = {
                    "id_compensacion": id_compensacion,
                    "fecha_compensacion": fecha,
                    "observacion": obs,
                    "fecha_guardia": fecha_guardia,
                }
                return render_template("editar_mi_compensacion.html",
                                       compensacion=compensacion)

            cursor.execute("""
                UPDATE compensaciones
                SET fecha_compensacion = %s,
                    observacion = %s
                WHERE id_compensacion = %s
            """, (fecha, obs, id_compensacion))

            conexion.commit()

            registrar_auditoria("compensacion_propia_actualizada", f"Compensación propia actualizada: ID {id_compensacion}")
            flash("Compensación actualizada correctamente", "success")
            return redirigir_con_filtros("mis_compensaciones", "filtro_mis_compensaciones")

        finally:
            cursor.close()


    # FISCALIZADOR — Eliminar compensación propia (POST)
    @app.route("/eliminar_mi_compensacion/<int:id_compensacion>", methods=["POST"])
    @limiter.limit("10 per minute")
    def eliminar_mi_compensacion(id_compensacion):

        if "usuario" not in session:
            return redirect(url_for("login.login"))

        if (session.get("perfil_activo") or "").lower() != "fiscalizador":
            return acceso_no_autorizado()

        cursor = conexion.cursor()

        try:
            cursor.execute("""
                SELECT g.id_usuario FROM compensaciones c
                INNER JOIN guardias g ON c.id_guardia = g.id_guardia
                WHERE c.id_compensacion = %s
            """, (id_compensacion,))
            row = cursor.fetchone()
            if not row:
                return no_encontrado()
            if row[0] != session.get("id_usuario"):
                return acceso_no_autorizado()

            cursor.execute("""
                DELETE FROM compensaciones
                WHERE id_compensacion = %s
            """, (id_compensacion,))

            conexion.commit()

            registrar_auditoria("compensacion_propia_eliminada", f"Compensación propia eliminada: ID {id_compensacion}")
            flash("Compensación eliminada correctamente", "success")
            return redirigir_con_filtros("mis_compensaciones", "filtro_mis_compensaciones")

        finally:
            cursor.close()

    # ==========================
    # FISCALIZADOR — MIS GUARDIAS
    # ==========================
    # Listado de guardias propias con filtros por año, rango, asistencia y estado


