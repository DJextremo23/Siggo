"""Rutas de gestión de compensaciones (admin y fiscalizador)."""

import os
from flask import render_template, request, session, redirect, url_for, flash
from conexion import conexion as _obtener_conexion
conexion = _obtener_conexion()
from limiter_instance import limiter
from utils import (
    acceso_no_autorizado, datos_invalidos, no_encontrado, error_interno,
    error_response, guardar_filtros, redirigir_con_filtros, registrar_auditoria,
)
from correo import enviar_correo
from helpers import DIAS_ES
from datetime import datetime, date

# Logos incrustados en los correos de notificación
LOGO_SIGGO = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "img", "logo.png")
LOGO_SMALL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "ppt", "logo_small.jpg")

# Configuración visual de los correos de compensación según la acción
_ACCIONES_CORREO = {
    "registrada": {
        "titulo": "Compensación registrada",
        "header_bg": "#1e3a8a",
        "subtitle_color": "#bfdbfe",
        "mensaje": "Se le informa que se ha registrado una compensación asociada a una de sus guardias. A continuación se detallan los datos correspondientes:",
        "footer_label": "Registrado por",
    },
    "editada": {
        "titulo": "Compensación actualizada",
        "header_bg": "#92400e",
        "subtitle_color": "#fde68a",
        "mensaje": "Se le informa que se ha actualizado la compensación asociada a una de sus guardias. A continuación se detallan los datos correspondientes:",
        "footer_label": "Actualizado por",
    },
    "eliminada": {
        "titulo": "Compensación eliminada",
        "header_bg": "#991b1b",
        "subtitle_color": "#fecaca",
        "mensaje": "Se le informa que se ha eliminado la compensación asociada a una de sus guardias. A continuación se detallan los datos correspondientes:",
        "footer_label": "Eliminado por",
    },
}


def _enviar_correo_compensacion(destinatario, nombre_completo, fecha_guardia,
                                fecha_compensacion, tipo, feriado, accion,
                                admin_nombre="", admin_correo=""):
    """Notifica al fiscalizador una compensación registrada/editada/eliminada (mejor esfuerzo)."""
    if not destinatario:
        return

    cfg = _ACCIONES_CORREO.get(accion, _ACCIONES_CORREO["registrada"])
    asunto = cfg["titulo"]

    fecha_guardia_str = fecha_guardia.strftime("%d/%m/%Y") if isinstance(fecha_guardia, date) else (str(fecha_guardia) if fecha_guardia else "—")
    fecha_compensacion_str = fecha_compensacion.strftime("%d/%m/%Y") if isinstance(fecha_compensacion, date) else (str(fecha_compensacion) if fecha_compensacion else "—")

    tipo_guardia_str = "Soporte" if tipo == "soporte" else "Guardia"

    if isinstance(fecha_guardia, date):
        dia = DIAS_ES[fecha_guardia.weekday()]
    else:
        dia = str(fecha_guardia) if fecha_guardia else "—"
    dia_guardia_str = f"{dia} (Feriado: {feriado})" if feriado else dia

    cuerpo = f"""
    <div style="margin:0; padding:0; background-color:#f1f5f9;">
      <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f1f5f9; padding:24px 0;">
        <tr>
          <td align="center">
            <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px; width:100%; background-color:#ffffff; border-radius:10px; overflow:hidden; border:1px solid #e2e8f0;">
              <tr>
                <td style="background-color:{cfg['header_bg']}; padding:20px 24px;">
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
                  <div style="font-family:'Segoe UI',Arial,sans-serif; color:{cfg['subtitle_color']}; font-size:12px; margin-top:10px;">{cfg['titulo']}</div>
                </td>
              </tr>
              <tr>
                <td style="padding:24px; font-family:'Segoe UI',Arial,sans-serif; color:#0f172a; line-height:1.6; font-size:14px;">
                  <p style="margin:0 0 12px;">Estimado(a) <strong>{nombre_completo}</strong>:</p>
                  <p style="margin:0 0 16px;">{cfg['mensaje']}</p>
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background-color:#f8fafc; border:1px solid #e2e8f0; border-radius:8px;">
                    <tr>
                      <td style="padding:16px;">
                        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                          <tr>
                            <td style="font-size:11px; color:#64748b; padding-bottom:4px; text-transform:uppercase; letter-spacing:0.5px;">Fecha de la guardia</td>
                          </tr>
                          <tr>
                            <td style="font-size:15px; font-weight:700; color:#0f172a; padding-bottom:14px;">{fecha_guardia_str}</td>
                          </tr>
                          <tr>
                            <td style="font-size:11px; color:#64748b; padding-bottom:4px; text-transform:uppercase; letter-spacing:0.5px;">Tipo de guardia</td>
                          </tr>
                          <tr>
                            <td style="font-size:15px; font-weight:700; color:#0f172a; padding-bottom:14px;">{tipo_guardia_str}</td>
                          </tr>
                          <tr>
                            <td style="font-size:11px; color:#64748b; padding-bottom:4px; text-transform:uppercase; letter-spacing:0.5px;">Día de la guardia</td>
                          </tr>
                          <tr>
                            <td style="font-size:15px; font-weight:700; color:#0f172a; padding-bottom:14px;">{dia_guardia_str}</td>
                          </tr>
                          <tr>
                            <td style="font-size:11px; color:#64748b; padding-bottom:4px; text-transform:uppercase; letter-spacing:0.5px;">Fecha de compensación</td>
                          </tr>
                          <tr>
                            <td style="font-size:15px; font-weight:700; color:#0f172a;">{fecha_compensacion_str}</td>
                          </tr>
                        </table>
                      </td>
                    </tr>
                  </table>
                  <p style="margin:16px 0 0;">Para mayor detalle, puede consultar la información en el sistema SIGGO.</p>
                </td>
              </tr>
              <tr>
                <td style="background-color:#f8fafc; border-top:1px solid #e2e8f0; padding:16px 24px; font-family:'Segoe UI',Arial,sans-serif; font-size:12px; color:#64748b;">
                  <p style="margin:0 0 4px;">{cfg['footer_label']}: <strong style="color:#0f172a;">{admin_nombre}</strong></p>
                  <p style="margin:0; color:#2563eb;">{admin_correo}</p>
                  <p style="margin:10px 0 0; font-size:11px; color:#94a3b8;">Este es un mensaje automático. No responda a este correo.</p>
                </td>
              </tr>
            </table>
          </td>
        </tr>
      </table>
    </div>
    """

    enviar_correo(
        destinatario,
        asunto,
        cuerpo,
        imagenes_inline=[(LOGO_SIGGO, "logo_siggo"), (LOGO_SMALL, "logo_small")]
    )


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
                SELECT g.fecha_guardia, g.tipo, f.descripcion
                FROM guardias g
                LEFT JOIN feriados f ON g.fecha_guardia = f.fecha
                WHERE g.id_guardia = %s
            """, (id_guardia,))

            fila_guardia = cursor.fetchone()

            if not fila_guardia:
                return no_encontrado("Guardia no encontrada")

            fecha_guardia = fila_guardia[0]
            tipo_guardia = fila_guardia[1]
            feriado_guardia = fila_guardia[2]

            try:
                fecha_compensacion = datetime.strptime(fecha, "%Y-%m-%d").date()
            except ValueError:
                flash("Formato de fecha inválido. Use YYYY-MM-DD", "error")
                return redirigir_con_filtros("compensaciones_admin", "filtro_compensaciones")

            if fecha_compensacion < fecha_guardia:
                flash("La fecha de compensación no puede ser anterior a la fecha de la guardia", "error")
                return redirigir_con_filtros("compensaciones_admin", "filtro_compensaciones")

            # Determinar si es un registro nuevo o una actualización (para el correo)
            cursor.execute("""
                SELECT id_compensacion
                FROM compensaciones
                WHERE id_guardia = %s
            """, (id_guardia,))
            accion = "editada" if cursor.fetchone() else "registrada"

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

            # Notificar al fiscalizador por correo la fecha de compensación (mejor esfuerzo)
            cursor.execute("""
                SELECT u.correo, CONCAT(u.nombre,' ',u.apellidos)
                FROM guardias g
                INNER JOIN usuarios u ON g.id_usuario = u.id_usuario
                WHERE g.id_guardia = %s
            """, (id_guardia,))
            info_usuario = cursor.fetchone()
            if info_usuario and info_usuario[0]:
                nombre_completo = info_usuario[1] or "Fiscalizador"

                # Datos del administrador que registró/actualizó la compensación
                cursor.execute("""
                    SELECT correo, CONCAT(nombre,' ',apellidos)
                    FROM usuarios
                    WHERE id_usuario = %s
                """, (session.get("id_usuario"),))
                admin_info = cursor.fetchone()
                admin_nombre = admin_info[1] if admin_info and admin_info[1] else "Administración"
                admin_correo = admin_info[0] if admin_info and admin_info[0] else ""

                _enviar_correo_compensacion(
                    info_usuario[0],
                    nombre_completo,
                    fecha_guardia,
                    fecha_compensacion,
                    tipo_guardia,
                    feriado_guardia,
                    accion,
                    admin_nombre=admin_nombre,
                    admin_correo=admin_correo,
                )

            if accion == "editada":
                registrar_auditoria("compensacion_editada", f"Compensación actualizada: guardia {id_guardia}")
                flash("Compensación actualizada correctamente", "success")
            else:
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

            # Obtener datos antes de eliminar para notificar al fiscalizador
            cursor.execute("""
                SELECT g.fecha_guardia, u.correo, CONCAT(u.nombre,' ',u.apellidos),
                       c.fecha_compensacion, g.tipo, f.descripcion
                FROM guardias g
                INNER JOIN usuarios u ON g.id_usuario = u.id_usuario
                LEFT JOIN compensaciones c ON g.id_guardia = c.id_guardia
                LEFT JOIN feriados f ON g.fecha_guardia = f.fecha
                WHERE g.id_guardia = %s
            """, (id_guardia,))
            info = cursor.fetchone()

            cursor.execute("""
                DELETE FROM compensaciones
                WHERE id_guardia = %s
            """, (id_guardia,))

            conexion.commit()

            if info and info[1]:
                nombre_completo = info[2] or "Fiscalizador"

                cursor.execute("""
                    SELECT correo, CONCAT(nombre,' ',apellidos)
                    FROM usuarios
                    WHERE id_usuario = %s
                """, (session.get("id_usuario"),))
                admin_info = cursor.fetchone()
                admin_nombre = admin_info[1] if admin_info and admin_info[1] else "Administración"
                admin_correo = admin_info[0] if admin_info and admin_info[0] else ""

                _enviar_correo_compensacion(
                    info[1],
                    nombre_completo,
                    info[0],
                    info[3],
                    info[4],
                    info[5],
                    "eliminada",
                    admin_nombre=admin_nombre,
                    admin_correo=admin_correo,
                )

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

            # Obtener datos antes de eliminar para notificar a cada fiscalizador
            cursor.execute(
                f"""
                SELECT g.fecha_guardia, u.correo, CONCAT(u.nombre,' ',u.apellidos),
                       c.fecha_compensacion, g.tipo, f.descripcion
                FROM guardias g
                INNER JOIN usuarios u ON g.id_usuario = u.id_usuario
                LEFT JOIN compensaciones c ON g.id_guardia = c.id_guardia
                LEFT JOIN feriados f ON g.fecha_guardia = f.fecha
                WHERE g.id_guardia IN ({placeholders})
                """,
                tuple(ids_int),
            )
            info_guardias = cursor.fetchall()

            cursor.execute(
                f"DELETE FROM compensaciones WHERE id_guardia IN ({placeholders})",
                tuple(ids_int),
            )
            eliminadas = cursor.rowcount
            conexion.commit()

            # Datos del administrador que elimina
            cursor.execute("""
                SELECT correo, CONCAT(nombre,' ',apellidos)
                FROM usuarios
                WHERE id_usuario = %s
            """, (session.get("id_usuario"),))
            admin_info = cursor.fetchone()
            admin_nombre = admin_info[1] if admin_info and admin_info[1] else "Administración"
            admin_correo = admin_info[0] if admin_info and admin_info[0] else ""

            for g in info_guardias:
                if not g[1]:
                    continue
                nombre_completo = g[2] or "Fiscalizador"
                _enviar_correo_compensacion(
                    g[1],
                    nombre_completo,
                    g[0],
                    g[3],
                    g[4],
                    g[5],
                    "eliminada",
                    admin_nombre=admin_nombre,
                    admin_correo=admin_correo,
                )

            registrar_auditoria("compensaciones_eliminadas", f"Eliminadas {eliminadas} compensaciones")
            flash(f"Se eliminaron {eliminadas} compensaciones correctamente", "success")
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


