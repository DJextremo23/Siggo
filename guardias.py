"""Rutas de gestión de guardias (admin y fiscalizador)."""

import os
from flask import render_template, request, session, redirect, url_for, flash
from conexion import conexion as _obtener_conexion
conexion = _obtener_conexion()
from limiter_instance import limiter
from utils import (
    acceso_no_autorizado, datos_invalidos, error_interno,
    guardar_filtros, redirigir_con_filtros, registrar_auditoria,
)
from helpers import DIAS_ES
import mysql.connector.errors
from datetime import datetime, date


def registrar_rutas(app):
    @app.route("/guardias")
    def ver_guardias():

        if "usuario" not in session:
            return redirect(url_for("home"))

        if session.get("perfil_activo") != "admin":
            return acceso_no_autorizado()

        guardar_filtros("filtro_guardias")

        cursor = conexion.cursor(dictionary=True)

        id_usuario = request.args.get("id_usuario", "").strip()
        fecha_desde = request.args.get("fecha_desde", "").strip()
        fecha_hasta = request.args.get("fecha_hasta", "").strip()
        asistencia = request.args.get("asistencia", "").strip()
        tipo = request.args.get("tipo", "").strip()

        # =========================
        # 1. GUARDIAS
        # =========================
        sql = "SELECT rg.*, u.foto FROM resumen_guardias rg LEFT JOIN usuarios u ON rg.id_usuario = u.id_usuario WHERE 1=1 "
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

        cursor.close()

        return render_template(
            "guardias.html",
            guardias=guardias,
            fiscalizadores=fiscalizadores
        )

    # ADMIN — Agregar nueva guardia (con verificación de duplicados)
    @app.route("/agregar_guardia", methods=["POST"])
    @limiter.limit("10 per minute")
    def agregar_guardia():

        if "usuario" not in session:
            return redirect(url_for("home"))

        # Validar que el perfil activo sea admin
        if session.get("perfil_activo") != "admin":
            return acceso_no_autorizado()

        id_usuario = request.form.get("id_usuario", "").strip()
        fecha_guardia = request.form.get("fecha_guardia", "").strip()
        tipo = request.form.get("tipo", "guardia").strip()
        if tipo not in ("guardia", "soporte"):
            tipo = "guardia"

        if not id_usuario or not fecha_guardia:
            flash("Todos los campos son obligatorios", "error")
            return redirigir_con_filtros("ver_guardias", "filtro_guardias")

        try:
            datetime.strptime(fecha_guardia, "%Y-%m-%d")
        except ValueError:
            flash("Formato de fecha inválido. Use YYYY-MM-DD", "error")
            return redirigir_con_filtros("ver_guardias", "filtro_guardias")

        cursor = conexion.cursor()

        try:
            cursor.execute("SELECT estado FROM usuarios WHERE id_usuario = %s", (id_usuario,))
            usuario = cursor.fetchone()
            if not usuario:
                flash("Usuario no encontrado", "error")
                return redirigir_con_filtros("ver_guardias", "filtro_guardias")
            if usuario[0] != "activo":
                flash("No se puede registrar una guardia para un usuario inactivo", "error")
                return redirigir_con_filtros("ver_guardias", "filtro_guardias")

            # Verificar que el usuario seleccionado sea fiscalizador
            cursor.execute("""
                SELECT 1
                FROM usuarios_roles ur
                INNER JOIN roles r ON ur.id_rol = r.id_rol
                WHERE ur.id_usuario = %s AND r.nombre_rol = 'fiscalizador'
            """, (id_usuario,))
            if not cursor.fetchone():
                flash("El usuario seleccionado no es fiscalizador", "error")
                return redirigir_con_filtros("ver_guardias", "filtro_guardias")

            cursor.execute("""
                SELECT 1 FROM guardias
                WHERE id_usuario = %s AND fecha_guardia = %s
                LIMIT 1
            """, (id_usuario, fecha_guardia))

            if cursor.fetchone():
                flash("Este fiscalizador ya tiene una guardia asignada en esa fecha", "error")
                return redirigir_con_filtros("ver_guardias", "filtro_guardias")

            cursor.execute("""
                INSERT INTO guardias (id_usuario, fecha_guardia, tipo)
                VALUES (%s, %s, %s)
            """, (id_usuario, fecha_guardia, tipo))

            conexion.commit()
            registrar_auditoria("guardia_creada", f"Guardia creada: usuario {id_usuario}, fecha {fecha_guardia}")
            flash("Guardia registrada correctamente", "success")
            return redirigir_con_filtros("ver_guardias", "filtro_guardias")

        except mysql.connector.errors.IntegrityError as e:
            conexion.rollback()
            if e.errno == 1062:
                flash("Este fiscalizador ya tiene una guardia asignada en esa fecha", "error")
            else:
                flash("No se pudo registrar la guardia: datos duplicados o inválidos", "error")
            return redirigir_con_filtros("ver_guardias", "filtro_guardias")

        except mysql.connector.errors.OperationalError as e:
            conexion.rollback()
            if e.errno == 1205:
                flash("La base de datos está ocupada, intente nuevamente en unos segundos", "error")
            else:
                flash("Error de conexión con la base de datos, intente nuevamente", "error")
            return redirigir_con_filtros("ver_guardias", "filtro_guardias")

        except Exception as e:
            conexion.rollback()
            print("ERROR agregar_guardia:", e)
            flash("Ocurrió un error al registrar la guardia. Intente nuevamente.", "error")
            return redirigir_con_filtros("ver_guardias", "filtro_guardias")

        finally:
            cursor.close()


    # ADMIN — Editar guardia (GET muestra formulario, POST guarda cambios)
    @app.route("/editar_guardia/<int:id>", methods=["GET", "POST"])
    @limiter.limit("10 per minute")
    def editar_guardia(id):

        if "usuario" not in session:
            return redirect(url_for("home"))

        if session.get("perfil_activo") != "admin":
            return acceso_no_autorizado()

        cursor = conexion.cursor(dictionary=True)

        try:

            # ======================
            # GUARDAR CAMBIOS
            # ======================
            if request.method == "POST":

                id_usuario = request.form.get("id_usuario", "").strip()
                fecha_guardia = request.form.get("fecha_guardia", "").strip()
                tipo = request.form.get("tipo", "guardia").strip()
                if tipo not in ("guardia", "soporte"):
                    tipo = "guardia"

                if not id_usuario or not fecha_guardia:
                    flash("Todos los campos son obligatorios", "error")
                    return redirigir_con_filtros("ver_guardias", "filtro_guardias")

                try:
                    datetime.strptime(fecha_guardia, "%Y-%m-%d")
                except ValueError:
                    flash("Formato de fecha inválido. Use YYYY-MM-DD", "error")
                    return redirigir_con_filtros("ver_guardias", "filtro_guardias")

                # Verificar que el fiscalizador destino esté activo
                cursor.execute("SELECT estado FROM usuarios WHERE id_usuario = %s", (id_usuario,))
                usuario = cursor.fetchone()
                if not usuario:
                    flash("Usuario no encontrado", "error")
                    return redirigir_con_filtros("ver_guardias", "filtro_guardias")
                if usuario["estado"] != "activo":
                    flash("No se puede asignar la guardia a un usuario inactivo", "error")
                    return redirigir_con_filtros("ver_guardias", "filtro_guardias")

                # Verificar que no exista otra guardia del mismo fiscalizador en esa fecha
                cursor.execute("""
                    SELECT 1 FROM guardias
                    WHERE id_usuario = %s AND fecha_guardia = %s AND id_guardia != %s
                    LIMIT 1
                """, (id_usuario, fecha_guardia, id))

                if cursor.fetchone():
                    flash("Este fiscalizador ya tiene una guardia asignada en esa fecha", "error")
                    return redirigir_con_filtros("ver_guardias", "filtro_guardias")

                try:
                    cursor.execute("""
                        UPDATE guardias
                        SET id_usuario=%s, fecha_guardia=%s, tipo=%s
                        WHERE id_guardia=%s
                    """, (id_usuario, fecha_guardia, tipo, id))

                    conexion.commit()
                    registrar_auditoria("guardia_actualizada", f"Guardia actualizada: ID {id}")
                    flash("Guardia actualizada correctamente", "success")
                    return redirigir_con_filtros("ver_guardias", "filtro_guardias")

                except mysql.connector.errors.IntegrityError as e:
                    conexion.rollback()
                    if e.errno == 1062:
                        flash("Este fiscalizador ya tiene una guardia asignada en esa fecha", "error")
                    else:
                        flash("No se pudo actualizar la guardia: datos duplicados o inválidos", "error")
                    return redirigir_con_filtros("ver_guardias", "filtro_guardias")

                except Exception as e:
                    conexion.rollback()
                    print("ERROR editar_guardia:", e)
                    return error_interno()

            # ======================
            # CARGAR DATOS
            # ======================
            cursor.execute("""
                SELECT *
                FROM guardias
                WHERE id_guardia=%s
            """, (id,))

            guardia = cursor.fetchone()

            cursor.execute("""
                SELECT DISTINCT
                    u.id_usuario,
                    u.nombre,
                    u.apellidos
                FROM usuarios u
                INNER JOIN usuarios_roles ur ON u.id_usuario = ur.id_usuario
                INNER JOIN roles r ON ur.id_rol = r.id_rol
                WHERE r.nombre_rol = 'fiscalizador'
                  AND (u.estado = 'activo' OR u.id_usuario = %s)
                ORDER BY u.nombre
            """, (guardia["id_usuario"],))

            fiscalizadores = cursor.fetchall()

            return render_template(
                "editar_guardia.html",
                guardia=guardia,
                fiscalizadores=fiscalizadores
            )

        finally:
            cursor.close()


    # ── Limpieza de archivos físicos de informes al eliminar guardias ──
    UPLOAD_FOLDER = os.getenv("UPLOAD_FOLDER", "uploads")


    def _eliminar_archivos_informes(rutas):
        """Elimina del disco los archivos de informes (protección contra path traversal)."""
        uploads_real = os.path.realpath(UPLOAD_FOLDER)
        for ruta in rutas:
            if not ruta:
                continue
            ruta_real = os.path.realpath(ruta)
            if ruta_real != uploads_real and not ruta_real.startswith(uploads_real + os.sep):
                continue
            try:
                if os.path.isfile(ruta_real):
                    os.remove(ruta_real)
            except OSError:
                pass


    def _eliminar_guardias_con_archivos(cursor, ids_guardia):
        """Elimina guardias y los archivos físicos de sus informes asociados."""
        placeholders = ",".join(["%s"] * len(ids_guardia))
        cursor.execute(
            f"SELECT ruta_archivo FROM informes WHERE id_guardia IN ({placeholders})",
            tuple(ids_guardia),
        )
        rutas = [fila[0] for fila in cursor.fetchall() if fila[0]]
        cursor.execute(
            f"DELETE FROM guardias WHERE id_guardia IN ({placeholders})",
            tuple(ids_guardia),
        )
        _eliminar_archivos_informes(rutas)


    @app.route("/eliminar_guardia/<int:id>", methods=["POST"])
    @limiter.limit("10 per minute")
    def eliminar_guardia(id):

        if "usuario" not in session:
            return redirect(url_for("home"))

        if session.get("perfil_activo") != "admin":
            return acceso_no_autorizado()

        cursor = conexion.cursor()

        try:
            _eliminar_guardias_con_archivos(cursor, [id])

            conexion.commit()

            registrar_auditoria("guardia_eliminada", f"Guardia eliminada: ID {id}")
            flash("Guardia eliminada correctamente", "success")
            return redirigir_con_filtros("ver_guardias", "filtro_guardias")

        except Exception as e:

            conexion.rollback()

            print("ERROR ELIMINAR GUARDIA:", e)

            return error_interno()

        finally:

            cursor.close()

    @app.route("/eliminar_guardias", methods=["POST"])
    @limiter.limit("10 per minute")
    def eliminar_guardias():

        if "usuario" not in session:
            return redirect(url_for("home"))

        if session.get("perfil_activo") != "admin":
            return acceso_no_autorizado()

        ids = request.form.getlist("ids_guardia")
        if not ids:
            flash("No seleccionó ninguna guardia", "error")
            return redirigir_con_filtros("ver_guardias", "filtro_guardias")

        ids_int = []
        for valor in ids:
            try:
                ids_int.append(int(valor))
            except (TypeError, ValueError):
                return datos_invalidos("Identificador de guardia inválido")

        cursor = conexion.cursor()

        try:
            _eliminar_guardias_con_archivos(cursor, ids_int)
            conexion.commit()
            registrar_auditoria("guardias_eliminadas", f"Eliminadas {cursor.rowcount} guardias")
            flash(f"Se eliminaron {cursor.rowcount} guardias correctamente", "success")
        except Exception as e:
            conexion.rollback()
            print("ERROR ELIMINAR GUARDIAS:", e)
            flash("Ocurrió un error al eliminar las guardias", "error")
        finally:
            cursor.close()

        return redirigir_con_filtros("ver_guardias", "filtro_guardias")


    # ==========================
    # ADMIN — ASISTENCIA
    # ==========================
    # Registrar asistencia (asistió, falta, justificado) desde el panel admin



    @app.route("/mis_guardias")
    def mis_guardias():

        # ================= VALIDAR SESIÓN =================
        if "usuario" not in session:
            return redirect(url_for("home"))

        if session.get("perfil_activo", "").lower() != "fiscalizador":
            return acceso_no_autorizado()

        anio = request.args.get("anio", "").strip()
        fecha_desde = request.args.get("fecha_desde")
        fecha_hasta = request.args.get("fecha_hasta")
        asistencia = request.args.get("asistencia")
        estado_guardia = request.args.get("estado_guardia")
        tipo = request.args.get("tipo", "").strip()

        cursor = conexion.cursor(dictionary=True)

        try:

            sql = """
                SELECT
                    g.id_guardia,
                    g.fecha_guardia,
                    g.tipo,

                    CASE
                        WHEN LOWER(COALESCE(a.estado, '')) IN ('asistio')
                            THEN 'realizada'
                        WHEN LOWER(COALESCE(a.estado, '')) IN ('falta')
                            THEN 'cancelada'
                        WHEN LOWER(COALESCE(a.estado, '')) IN ('justificado')
                            THEN 'justificada'
                        ELSE 'programada'
                    END AS estado_guardia,

                    ELT(DAYOFWEEK(g.fecha_guardia),
                        'Domingo','Lunes','Martes','Miércoles',
                        'Jueves','Viernes','Sábado') AS dia_semana,

                    CASE
                        WHEN f.id_feriado IS NOT NULL THEN 'FERIADO'
                        ELSE ELT(DAYOFWEEK(g.fecha_guardia),
                            'Domingo','Lunes','Martes','Miércoles',
                            'Jueves','Viernes','Sábado')
                    END AS tipo_dia,

                    f.descripcion AS feriado,

                    CASE
                        WHEN LOWER(COALESCE(a.estado, '')) IN ('asistio')
                            THEN 'Asistió'
                        WHEN LOWER(COALESCE(a.estado, '')) IN ('falta')
                            THEN 'Falta'
                        WHEN LOWER(COALESCE(a.estado, '')) IN ('justificado')
                            THEN 'Justificado'
                        ELSE 'Pendiente'
                    END AS asistencia,

                    c.fecha_compensacion

                FROM guardias g
                LEFT JOIN asistencia a ON g.id_guardia = a.id_guardia
                LEFT JOIN compensaciones c ON g.id_guardia = c.id_guardia
                LEFT JOIN feriados f ON g.fecha_guardia = f.fecha

                WHERE g.id_usuario = %s
            """

            params = [session["id_usuario"]]

            # ================= FILTRO AÑO =================
            if anio:
                try:
                    anio_int = int(anio)
                except (TypeError, ValueError):
                    anio_int = None
                if anio_int:
                    sql += " AND YEAR(g.fecha_guardia) = %s "
                    params.append(anio_int)

            # ================= FILTRO DESDE =================
            if fecha_desde:
                sql += " AND g.fecha_guardia >= %s "
                params.append(fecha_desde)

            # ================= FILTRO HASTA =================
            if fecha_hasta:
                sql += " AND g.fecha_guardia <= %s "
                params.append(fecha_hasta)

            # ================= FILTRO ASISTENCIA =================
            if asistencia:
                if asistencia == "Asistió":
                    sql += " AND LOWER(COALESCE(a.estado, '')) = 'asistio' "
                elif asistencia == "Falta":
                    sql += " AND LOWER(COALESCE(a.estado, '')) = 'falta' "
                elif asistencia == "Justificado":
                    sql += " AND LOWER(COALESCE(a.estado, '')) = 'justificado' "
                elif asistencia == "Pendiente":
                    sql += " AND (a.estado IS NULL OR LOWER(COALESCE(a.estado, '')) NOT IN ('asistio','falta','justificado')) "

            # ================= FILTRO ESTADO GUARDIA =================
            if estado_guardia == "realizada":
                sql += " AND LOWER(COALESCE(a.estado, '')) IN ('asistio') "
            elif estado_guardia == "cancelada":
                sql += " AND LOWER(COALESCE(a.estado, '')) IN ('falta') "
            elif estado_guardia == "justificada":
                sql += " AND LOWER(COALESCE(a.estado, '')) IN ('justificado') "
            elif estado_guardia == "programada":
                sql += """ AND (
                    a.estado IS NULL
                    OR LOWER(COALESCE(a.estado, '')) NOT IN ('asistio','falta','justificado')
                ) """

            # ================= FILTRO TIPO =================
            if tipo:
                sql += " AND g.tipo = %s "
                params.append(tipo)

            sql += " ORDER BY g.fecha_guardia DESC"

            cursor.execute(sql, tuple(params))
            guardias = cursor.fetchall()

            return render_template(
                "mi_guardias.html",
                guardias=guardias
            )

        finally:
            cursor.close()



    # -----------------------------------------------

