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
from correo import enviar_correo, construir_correo
import mysql.connector.errors
from datetime import datetime, date

_ACCIONES_CORREO_GUARDIA = {
    "registrada": {
        "titulo": "Guardia asignada",
        "clase_header": "mail-header-blue",
        "clase_subtitulo": "mail-subtitle-blue",
        "mensaje": "Se le informa que se le ha asignado una nueva guardia. A continuación se detallan los datos correspondientes:",
        "footer_label": "Registrado por",
    },
    "actualizada": {
        "titulo": "Guardia actualizada",
        "clase_header": "mail-header-orange",
        "clase_subtitulo": "mail-subtitle-orange",
        "mensaje": "Se le informa que se ha actualizado una de sus guardias asignadas. A continuación se detallan los datos correspondientes:",
        "footer_label": "Actualizado por",
    },
    "eliminada": {
        "titulo": "Guardia eliminada",
        "clase_header": "mail-header-red",
        "clase_subtitulo": "mail-subtitle-red",
        "mensaje": "Se le informa que se ha eliminado una de sus guardias asignadas. A continuación se detallan los datos correspondientes:",
        "footer_label": "Eliminado por",
    },
}


def _formatear_fecha(fecha):
    """Devuelve (fecha dd/mm/aaaa, nombre del día) a partir de date, datetime o string."""
    if isinstance(fecha, date) and not isinstance(fecha, datetime):
        return fecha.strftime("%d/%m/%Y"), DIAS_ES[fecha.weekday()]
    if isinstance(fecha, datetime):
        return fecha.strftime("%d/%m/%Y"), DIAS_ES[fecha.weekday()]
    if fecha:
        try:
            d = datetime.strptime(str(fecha), "%Y-%m-%d")
            return d.strftime("%d/%m/%Y"), DIAS_ES[d.weekday()]
        except ValueError:
            return str(fecha), str(fecha)
    return "—", "—"


def _enviar_correo_guardia(destinatario, nombre_completo, fecha_guardia, tipo, accion,
                           admin_nombre="", admin_correo=""):
    """Notifica al fiscalizador una guardia registrada/actualizada/eliminada (mejor esfuerzo)."""
    if not destinatario:
        return

    cfg = _ACCIONES_CORREO_GUARDIA.get(accion, _ACCIONES_CORREO_GUARDIA["registrada"])
    asunto = cfg["titulo"]

    fecha_guardia_str, dia_guardia_str = _formatear_fecha(fecha_guardia)
    tipo_guardia_str = "Soporte" if tipo == "soporte" else "Guardia"

    cuerpo = f"""
                  <p class="mail-greeting">Estimado(a) <strong>{nombre_completo}</strong>:</p>
                  <p class="mail-message">{cfg['mensaje']}</p>
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="mail-detail">
                    <tr>
                      <td class="mail-detail-pad">
                        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                          <tr><td class="mail-label">Fecha de la guardia</td></tr>
                          <tr><td class="mail-value">{fecha_guardia_str}</td></tr>
                          <tr><td class="mail-label">Tipo de guardia</td></tr>
                          <tr><td class="mail-value">{tipo_guardia_str}</td></tr>
                          <tr><td class="mail-label">Día de la guardia</td></tr>
                          <tr><td class="mail-value-last">{dia_guardia_str}</td></tr>
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


def _notificar_fiscalizador_guardia(cursor, id_usuario, fecha_guardia, tipo, accion):
    """Consulta al fiscalizador y al admin y envía el correo de guardia (mejor esfuerzo)."""
    try:
        cursor.execute("""
            SELECT correo, CONCAT(nombre,' ',apellidos) AS nombre_completo
            FROM usuarios
            WHERE id_usuario = %s
        """, (id_usuario,))
        info_usuario = cursor.fetchone()
        if not info_usuario:
            return
        if isinstance(info_usuario, dict):
            destinatario = info_usuario.get("correo")
            nombre_completo = info_usuario.get("nombre_completo") or "Fiscalizador"
        else:
            destinatario = info_usuario[0]
            nombre_completo = info_usuario[1] or "Fiscalizador"
        if not destinatario:
            return

        cursor.execute("""
            SELECT correo, CONCAT(nombre,' ',apellidos) AS nombre_completo
            FROM usuarios
            WHERE id_usuario = %s
        """, (session.get("id_usuario"),))
        admin_info = cursor.fetchone()
        if isinstance(admin_info, dict):
            admin_nombre = admin_info.get("nombre_completo") or "Administración"
            admin_correo = admin_info.get("correo") or ""
        else:
            admin_nombre = admin_info[1] if admin_info and admin_info[1] else "Administración"
            admin_correo = admin_info[0] if admin_info and admin_info[0] else ""

        _enviar_correo_guardia(
            destinatario,
            nombre_completo,
            fecha_guardia,
            tipo,
            accion,
            admin_nombre=admin_nombre,
            admin_correo=admin_correo,
        )
    except Exception as e:
        print("ERROR AL NOTIFICAR AL FISCALIZADOR DE GUARDIA:", e)


_MAX_LISTAR_ELIMINADAS = 20


def _formatear_fecha_correo(fecha):
    if isinstance(fecha, datetime):
        fecha = fecha.date()
    return fecha.strftime("%d/%m/%Y") if isinstance(fecha, date) else (str(fecha) if fecha else "—")


def _enviar_correo_guardias_eliminadas(destinatario, nombre_completo, guardias,
                                       admin_nombre="", admin_correo=""):
    """Notifica al fiscalizador la eliminación masiva de sus guardias (un solo correo)."""
    if not destinatario:
        return

    ordenadas = sorted(guardias, key=lambda g: g["fecha"])
    total = len(ordenadas)
    n_soporte = sum(1 for g in ordenadas if g["tipo"] == "soporte")
    n_guardia = total - n_soporte

    desde_str = _formatear_fecha_correo(ordenadas[0]["fecha"])
    hasta_str = _formatear_fecha_correo(ordenadas[-1]["fecha"])

    listado = ""
    if total <= _MAX_LISTAR_ELIMINADAS:
        for g in ordenadas:
            fecha_str = _formatear_fecha_correo(g["fecha"])
            dia = DIAS_ES[g["fecha"].weekday()] if isinstance(g["fecha"], date) else "—"
            tipo_str = "Soporte" if g["tipo"] == "soporte" else "Guardia"
            listado += f"""
                        <tr>
                          <td class="mail-list-cell">{fecha_str}</td>
                          <td class="mail-list-cell-muted">{dia}</td>
                          <td class="mail-list-cell-right">{tipo_str}</td>
                        </tr>
        """

    seccion_listado = ""
    if listado:
        seccion_listado = f"""
                  <p class="mail-section-title">Detalle de las guardias eliminadas</p>
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="mail-list">
                    <tr>
                      <td class="mail-list-pad">
                        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                          <tr>
                            <td class="mail-list-head">Fecha</td>
                            <td class="mail-list-head">Día</td>
                            <td class="mail-list-head-right">Tipo</td>
                          </tr>
        {listado}
                        </table>
                      </td>
                    </tr>
                  </table>
        """

    cuerpo = f"""
                  <p class="mail-greeting">Estimado(a) <strong>{nombre_completo}</strong>:</p>
                  <p class="mail-message">Se le informa que se han eliminado <strong>{total}</strong> de sus guardias asignadas. A continuación el resumen correspondiente:</p>
                  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" class="mail-detail">
                    <tr>
                      <td class="mail-detail-pad">
                        <table role="presentation" width="100%" cellpadding="0" cellspacing="0">
                          <tr><td class="mail-label">Total de guardias eliminadas</td></tr>
                          <tr><td class="mail-value">{total}</td></tr>
                          <tr><td class="mail-label">Rango de fechas</td></tr>
                          <tr><td class="mail-value">{desde_str} — {hasta_str}</td></tr>
                          <tr><td class="mail-label">Guardias</td></tr>
                          <tr><td class="mail-value">{n_guardia}</td></tr>
                          <tr><td class="mail-label">Soportes</td></tr>
                          <tr><td class="mail-value-last">{n_soporte}</td></tr>
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

    cuerpo_html = construir_correo("Guardias eliminadas", "mail-header-red", "mail-subtitle-red", cuerpo, pie)
    enviar_correo(destinatario, "Guardias eliminadas", cuerpo_html)


def registrar_rutas(app):
    @app.route("/guardias")
    def ver_guardias():

        if "usuario" not in session:
            return redirect(url_for("home"))

        if session.get("perfil_activo", "").lower() != "admin":
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
        if session.get("perfil_activo", "").lower() != "admin":
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
            _notificar_fiscalizador_guardia(cursor, id_usuario, fecha_guardia, tipo, "registrada")
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

        if session.get("perfil_activo", "").lower() != "admin":
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
                    _notificar_fiscalizador_guardia(cursor, id_usuario, fecha_guardia, tipo, "actualizada")
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

        if session.get("perfil_activo", "").lower() != "admin":
            return acceso_no_autorizado()

        cursor = conexion.cursor()

        try:
            cursor.execute("""
                SELECT id_usuario, fecha_guardia, tipo
                FROM guardias
                WHERE id_guardia = %s
            """, (id,))
            info = cursor.fetchone()

            _eliminar_guardias_con_archivos(cursor, [id])

            conexion.commit()

            registrar_auditoria("guardia_eliminada", f"Guardia eliminada: ID {id}")
            if info:
                _notificar_fiscalizador_guardia(cursor, info[0], info[1], info[2], "eliminada")
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

        if session.get("perfil_activo", "").lower() != "admin":
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
            placeholders = ",".join(["%s"] * len(ids_int))
            cursor.execute(
                f"""
                SELECT g.fecha_guardia, g.tipo, u.correo, CONCAT(u.nombre,' ',u.apellidos)
                FROM guardias g
                INNER JOIN usuarios u ON g.id_usuario = u.id_usuario
                WHERE g.id_guardia IN ({placeholders})
                """,
                tuple(ids_int),
            )
            info_guardias = cursor.fetchall()

            _eliminar_guardias_con_archivos(cursor, ids_int)
            conexion.commit()
            total_eliminadas = cursor.rowcount
            registrar_auditoria("guardias_eliminadas", f"Eliminadas {total_eliminadas} guardias")

            por_correo = {}
            for fila in info_guardias:
                fecha, tipo, correo, nombre = fila
                if not correo:
                    continue
                grupo = por_correo.setdefault(correo, {"nombre": nombre, "guardias": []})
                grupo["guardias"].append({"fecha": fecha, "tipo": tipo})

            cursor.execute(
                "SELECT correo, CONCAT(nombre,' ',apellidos) FROM usuarios WHERE id_usuario = %s",
                (session.get("id_usuario"),),
            )
            admin_info = cursor.fetchone()
            admin_nombre = admin_info[1] if admin_info and admin_info[1] else "Administración"
            admin_correo = admin_info[0] if admin_info and admin_info[0] else ""

            for correo, grupo in por_correo.items():
                try:
                    _enviar_correo_guardias_eliminadas(
                        correo,
                        grupo["nombre"] or "Fiscalizador",
                        grupo["guardias"],
                        admin_nombre=admin_nombre,
                        admin_correo=admin_correo,
                    )
                except Exception as e:
                    print("ERROR AL NOTIFICAR ELIMINACIÓN MASIVA:", e)

            flash(f"Se eliminaron {total_eliminadas} guardias correctamente", "success")
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

