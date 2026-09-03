import os
from flask import Flask, render_template, request, redirect, session, url_for, flash
from flask_talisman import Talisman
from dotenv import load_dotenv
from limiter_instance import limiter
from reporte import reporte_bp
from conexion import ConexionDB
from mis_reportes import mis_reportes_bp
from csrf import generate_token, validate_csrf
from utils import error_response, acceso_no_autorizado, error_interno, datos_invalidos, no_encontrado

from datetime import datetime, timedelta, date
from login import login_bp
from registro import registro_bp
from mis_informes import informe_bp
from informes import informes_bp
from fiscalizadores import fiscalizadores_bp
from perfil import perfil_bp
import mysql.connector.errors

"""
Aplicación principal de SIGGO — Sistema Integrado de Gestión de Guardias y Operaciones.

Este módulo contiene:
  - Configuración de la aplicación Flask (clave secreta, seguridad HTTPS/CSRF, rate limiting).
  - Registro de blueprints modulares (login, registro, perfil, reportes, informes, fiscalizadores).
  - Conexión a la base de datos MySQL mediante el singleton ConexionDB.
  - Todas las rutas del panel de administrador: dashboard, compensaciones, feriados, vacaciones,
    guardias y asistencia (CRUD completo).
  - Todas las rutas del panel de fiscalizador: asistencias propias, compensaciones, guardias,
    feriados y vacaciones con filtros y consultas personalizadas.
  - Endpoints de notificaciones (marcar leídas, obtener nuevas).
  - Manejadores de errores globales para errores de base de datos y errores 500.
  - Health check y arranque vía Waitress en producción.
"""

load_dotenv()

# -----------------------------------------------
# Configuración de la app y seguridad
# -----------------------------------------------

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", os.urandom(24).hex())

app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=4)

# Rate limiting global (límites por IP)
limiter._default_limits = ["200 per hour", "20 per minute"]
limiter.init_app(app)

# HTTPS forzado (según variable de entorno) y cabeceras de seguridad
FORCE_HTTPS = os.getenv("FORCE_HTTPS", "false").lower() == "true"
Talisman(
    app,
    force_https=FORCE_HTTPS,
    force_https_permanent=True,
    session_cookie_secure=FORCE_HTTPS,
    session_cookie_http_only=True,
    session_cookie_samesite="Lax",
    strict_transport_security=FORCE_HTTPS,
    strict_transport_security_max_age=31536000,
    strict_transport_security_include_subdomains=True,
    frame_options="DENY",
    referrer_policy="strict-origin-when-cross-origin",
    x_content_type_options=True,
    x_xss_protection=True,
    content_security_policy={
        "default-src": ["'self'", "blob:"],
        "script-src": ["'self'", "https://cdn.jsdelivr.net", "https://cdnjs.cloudflare.com"],
        "style-src": ["'self'", "'unsafe-inline'", "https://cdn.jsdelivr.net", "https://cdnjs.cloudflare.com", "https://fonts.googleapis.com"],
        "img-src": ["'self'", "data:", "blob:"],
        "font-src": ["'self'", "https://fonts.gstatic.com", "https://cdnjs.cloudflare.com"],
        "connect-src": ["'self'"],
        "frame-ancestors": ["'none'"],
    },
    content_security_policy_nonce_in=["script-src"],
)

# Protección CSRF: inyección de token en plantillas y validación en cada petición
@app.context_processor
def inject_csrf_token():
    return {"csrf_token": generate_token()}


@app.before_request
def csrf_check():
    validate_csrf()


@app.after_request
def no_cache(resp):
    """Evita que el navegador almacene páginas en caché y muestre datos desactualizados."""
    if resp.mimetype == "text/html":
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp

# -----------------------------------------------
# Conexión a la base de datos (singleton)
# -----------------------------------------------

conexion = ConexionDB()


@app.teardown_request
def cerrar_transaccion_db(exception=None):
    """Finaliza la transacción pendiente al terminar cada petición.

    La conexión thread-local se reutiliza entre peticiones; si una petición de
    solo lectura deja una transacción abierta, la siguiente petición en el mismo
    hilo vería datos desactualizados por el aislamiento REPEATABLE READ de MySQL
    (el cambio sí se guarda, pero no se visualiza en la tabla).
    """
    try:
        conexion.rollback()
    except Exception:
        pass


# Días de la semana en español para mostrar en las vistas
DIAS_ES = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']

# ==========================
# RUTAS DE INICIO / LOGIN
# ==========================

@app.route("/")
def home():
    return render_template("login.html")

@app.route("/inicio")
def inicio():

    if "usuario" not in session:
        return redirect(url_for("login.login"))

    perfil = session.get("perfil_activo")

    if perfil == "admin":
        return redirect(url_for("administrador"))

    elif perfil == "fiscalizador":
        return redirect(url_for("panel_fiscalizador"))

    return redirect(url_for("login.login"))

# ==========================
# PANEL DE ADMINISTRADOR
# ==========================
# Dashboard principal con estadísticas y alertas de vacaciones

@app.route("/administrador")
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
                       AND YEAR(v.fecha_inicio) = YEAR(CURDATE())
                    ), 0
                ) AS dias_tomados,
                TIMESTAMPDIFF(YEAR, u.fecha_ingreso, CURDATE()) * 30 AS total_acumulado,
                COALESCE(
                    (SELECT SUM(DATEDIFF(v.fecha_fin, v.fecha_inicio) + 1)
                     FROM vacaciones v
                     WHERE v.id_usuario = u.id_usuario), 0
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
        total_reportes = cursor.fetchone()["total"]

        return render_template("administrador.html",
                               alertas=alertas,
                               total_usuarios=total_usuarios,
                               total_guardias=total_guardias,
                               total_informes=total_informes,
                               total_reportes=total_reportes)
    except Exception as e:
        print("ERROR administrador:", e)
        return error_interno()
    finally:
        if cursor is not None:
            cursor.close()








# ==========================
# ADMIN — COMPENSACIONES
# ==========================
# Listado, edición y eliminación de compensaciones desde el panel admin

@app.route("/compensaciones")
def compensaciones_admin():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    buscar = request.args.get("buscar", "").strip()
    anio = request.args.get("anio", "").strip()
    desde = request.args.get("desde", "").strip()
    hasta = request.args.get("hasta", "").strip()
    estado_filtro = request.args.get("estado", "").strip()

    cursor = None
    try:
        cursor = conexion.cursor(dictionary=True)

        sql = """
            SELECT
                g.id_guardia,
                CONCAT(u.nombre,' ',u.apellidos) AS fiscalizador,
                u.foto,
                g.fecha_guardia,
                COALESCE(a.estado,'sin registro') AS asistencia,
                c.fecha_compensacion,
                c.observacion,
                CASE
                    WHEN f.id_feriado IS NOT NULL
                    THEN 'FERIADO'
                    ELSE ELT(DAYOFWEEK(g.fecha_guardia),
                        'Domingo','Lunes','Martes','Miércoles',
                        'Jueves','Viernes','Sábado')
                END AS tipo_dia
            FROM guardias g
            LEFT JOIN usuarios u
                ON g.id_usuario = u.id_usuario
            LEFT JOIN asistencia a
                ON g.id_guardia = a.id_guardia
            LEFT JOIN compensaciones c
                ON g.id_guardia = c.id_guardia
            LEFT JOIN feriados f
                ON g.id_feriado = f.id_feriado
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

        if buscar:
            filtro = f"%{buscar}%"
            sql += """ AND (
                    CONCAT(u.nombre,' ',u.apellidos) LIKE %s
                    OR a.estado LIKE %s
                    OR CAST(g.fecha_guardia AS CHAR) LIKE %s
                    OR CAST(c.fecha_compensacion AS CHAR) LIKE %s
            ) """
            params.extend([filtro, filtro, filtro, filtro])

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

        return render_template(
            "compensaciones.html",
            guardias=data
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
                ON g.id_feriado = f.id_feriado

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
def guardar_edicion_compensacion(id_guardia):

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    fecha = request.form["fecha_compensacion"]
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

        flash("Compensación registrada correctamente", "success")
        return redirect(url_for("compensaciones_admin"))

    except Exception as e:

        conexion.rollback()

        print("ERROR EDITAR COMPENSACIÓN:", e)

        return error_interno()

    finally:

        cursor.close()

@app.route("/eliminar_compensacion/<int:id_guardia>", methods=["POST"])
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

        flash("Compensación eliminada correctamente", "success")
        return redirect(url_for("compensaciones_admin"))

    except Exception as e:

        conexion.rollback()

        print("ERROR ELIMINAR COMPENSACIÓN:", e)

        return error_interno()

    finally:

        cursor.close()

# ==========================
# ADMIN — FERIADOS
# ==========================
# CRUD de feriados desde el panel admin

@app.route("/feriados")
def feriados():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    anio = request.args.get("anio", "").strip()
    fecha_desde = request.args.get("fecha_desde", "").strip()
    fecha_hasta = request.args.get("fecha_hasta", "").strip()
    descripcion = request.args.get("descripcion", "").strip()

    cursor = conexion.cursor(dictionary=True)

    try:

        sql = "SELECT * FROM feriados WHERE 1=1 "
        params = []

        if anio:
            sql += " AND YEAR(fecha) = %s "
            params.append(int(anio))

        if fecha_desde:
            sql += " AND fecha >= %s "
            params.append(fecha_desde)

        if fecha_hasta:
            sql += " AND fecha <= %s "
            params.append(fecha_hasta)

        if descripcion:
            sql += " AND descripcion LIKE %s "
            params.append(f"%{descripcion}%")

        sql += " ORDER BY fecha DESC"

        cursor.execute(sql, params)

        data = cursor.fetchall()

        for f in data:
            fecha = f.get('fecha')
            if isinstance(fecha, date):
                f['dia_semana'] = DIAS_ES[fecha.weekday()]
            else:
                f['dia_semana'] = str(fecha) if fecha else '—'

        return render_template(
            "feriados.html",
            feriados=data
        )

    finally:

        cursor.close()

@app.route("/guardar_feriado", methods=["POST"])
def guardar_feriado():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    fecha = request.form.get("fecha", "").strip()
    descripcion = request.form.get("descripcion", "").strip()

    if not fecha or not descripcion:
        flash("Fecha y descripción son obligatorios", "error")
        return redirect(url_for("feriados"))

    try:
        datetime.strptime(fecha, "%Y-%m-%d")
    except ValueError:
        flash("Formato de fecha inválido. Use YYYY-MM-DD", "error")
        return redirect(url_for("feriados"))

    cursor = conexion.cursor()

    try:

        cursor.execute("""
            INSERT INTO feriados (
                fecha,
                descripcion
            )
            VALUES (%s, %s)
        """, (
            fecha,
            descripcion
        ))

        conexion.commit()

        flash("Feriado registrado correctamente", "success")
        return redirect(url_for("feriados"))

    except mysql.connector.errors.IntegrityError as e:
        conexion.rollback()
        if e.errno == 1062:
            flash("Ya existe un feriado registrado en esa fecha", "error")
        else:
            flash("No se pudo registrar el feriado: datos duplicados o inválidos", "error")
        return redirect(url_for("feriados"))

    except mysql.connector.errors.OperationalError as e:
        conexion.rollback()
        if e.errno == 1205:
            flash("La base de datos está ocupada, intente nuevamente en unos segundos", "error")
        else:
            flash("Error de conexión con la base de datos, intente nuevamente", "error")
        return redirect(url_for("feriados"))

    except Exception as e:

        conexion.rollback()

        print("ERROR GUARDAR FERIADO:", e)

        flash("Ocurrió un error al registrar el feriado. Intente nuevamente.", "error")
        return redirect(url_for("feriados"))

    finally:

        cursor.close()


@app.route("/editar_feriado/<int:id>")
def editar_feriado(id):

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    cursor = conexion.cursor(dictionary=True)

    try:

        cursor.execute("""
            SELECT *
            FROM feriados
            WHERE id_feriado = %s
        """, (id,))

        data = cursor.fetchone()

        if not data:
            return no_encontrado("Feriado no encontrado")

        return render_template(
            "editar_feriado.html",
            data=data
        )

    finally:

        cursor.close()

@app.route("/actualizar_feriado/<int:id>", methods=["POST"])
def actualizar_feriado(id):

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    fecha = request.form["fecha"]
    descripcion = request.form["descripcion"].strip()

    cursor = conexion.cursor()

    try:
        cursor.execute("""
            UPDATE feriados
            SET fecha = %s, descripcion = %s
            WHERE id_feriado = %s
        """, (fecha, descripcion, id))

        conexion.commit()

        flash("Feriado actualizado correctamente", "success")
        return redirect(url_for("feriados"))

    except Exception as e:
        conexion.rollback()
        print("ERROR ACTUALIZAR FERIADO:", e)
        return error_interno()

    finally:
        cursor.close()

@app.route("/eliminar_feriado/<int:id>", methods=["POST"])
def eliminar_feriado(id):

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    cursor = conexion.cursor()

    try:

        cursor.execute("""
            DELETE FROM feriados
            WHERE id_feriado = %s
        """, (id,))

        conexion.commit()

        flash("Feriado eliminado correctamente", "success")
        return redirect(url_for("feriados"))

    except Exception as e:

        conexion.rollback()

        print("ERROR ELIMINAR FERIADO:", e)

        return error_interno()

    finally:

        cursor.close()



# ==========================
# ADMIN — VACACIONES
# ==========================
# Listado, asignación, edición y eliminación de vacaciones con validaciones

@app.route("/vacaciones")
def vacaciones():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
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
            filtro_vac += " AND CONCAT(u.nombre,' ',u.apellidos) = %s"
            params_vac.append(fiscalizador_vac)
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
            filtro_alerta += " AND CONCAT(u.nombre,' ',u.apellidos) = %s"
            params_alerta.append(fiscalizador_alerta)
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
                     WHERE v.id_usuario = u.id_usuario), 0
                ) AS dias_tomados_total,
                COALESCE(
                    (SELECT SUM(DATEDIFF(v.fecha_fin, v.fecha_inicio) + 1)
                     FROM vacaciones v
                     WHERE v.id_usuario = u.id_usuario
                       AND YEAR(v.fecha_inicio) = YEAR(CURDATE())), 0
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

@app.route("/editar_vacacion/<int:id>")
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

@app.route("/actualizar_vacacion/<int:id>", methods=["POST"])
def actualizar_vacacion(id):

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    inicio = request.form["fecha_inicio"]
    fin = request.form["fecha_fin"]

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
        return redirect(url_for("vacaciones"))

    except Exception as e:
        conexion.rollback()
        print("ERROR actualizar_vacacion:", e)
        return error_interno()

    finally:
        cursor.close()
# ADMIN — Guardar nueva vacación (validando antigüedad, cruces y guardias)
@app.route("/guardar_vacacion", methods=["POST"])
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
        return redirect(url_for("vacaciones"))

    try:
        inicio_dt = datetime.strptime(inicio, "%Y-%m-%d").date()
        fin_dt = datetime.strptime(fin, "%Y-%m-%d").date()
    except ValueError:
        flash("Formato de fecha inválido. Use YYYY-MM-DD", "error")
        return redirect(url_for("vacaciones"))

    if inicio_dt > fin_dt:
        flash("Fecha inválida: inicio mayor que fin", "error")
        return redirect(url_for("vacaciones"))

    cursor = conexion.cursor(dictionary=True)

    try:

        # =========================
        # OBTENER USUARIO
        # =========================
        cursor.execute("""
            SELECT fecha_ingreso
            FROM usuarios
            WHERE id_usuario = %s
        """, (id_usuario,))

        user = cursor.fetchone()

        if not user:
            flash("Usuario no encontrado", "error")
            return redirect(url_for("vacaciones"))

        fecha_ingreso = user["fecha_ingreso"]

        # =========================
        # VALIDAR 1 AÑO DE ANTIGÜEDAD
        # =========================
        fecha_habil = fecha_ingreso.replace(year=fecha_ingreso.year + 1)

        if datetime.now().date() < fecha_habil:
            flash("El usuario aún no cumple 1 año de antigüedad para solicitar vacaciones", "error")
            return redirect(url_for("vacaciones"))

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
            return redirect(url_for("vacaciones"))

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
            return redirect(url_for("vacaciones"))

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
        return redirect(url_for("vacaciones"))

    except mysql.connector.errors.OperationalError as e:
        conexion.rollback()
        if e.errno == 1205:
            flash("La base de datos está ocupada, intente nuevamente en unos segundos", "error")
        else:
            flash("Error de conexión con la base de datos, intente nuevamente", "error")
        return redirect(url_for("vacaciones"))

    except Exception as e:
        conexion.rollback()
        print("ERROR guardar_vacacion:", e)
        flash("Ocurrió un error al registrar las vacaciones. Intente nuevamente.", "error")
        return redirect(url_for("vacaciones"))

    finally:
        cursor.close()

# ADMIN — Eliminar vacación (con notificación al usuario)
@app.route("/eliminar_vacacion/<int:id>", methods=["POST"])
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
        return redirect(url_for("vacaciones"))

    except Exception as e:
        conexion.rollback()
        print("ERROR eliminar_vacacion:", e)
        return error_interno()

    finally:
        cursor.close()



# ==========================
# PANEL DE FISCALIZADOR
# ==========================
# Dashboard del fiscalizador: guardias, notificaciones, alertas de vacaciones y contadores

@app.route("/index")
def panel_fiscalizador():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo", "").lower() != "fiscalizador":
        return acceso_no_autorizado()

    cursor = None

    try:
        cursor = conexion.cursor(dictionary=True)

        # 🔥 Obtener datos desde la vista correcta
        cursor.execute("""
            SELECT id_guardia, fecha_guardia, tipo_dia, asistencia
            FROM resumen_guardias
            WHERE id_usuario = (
                SELECT id_usuario FROM usuarios WHERE usuario = %s
            )
            ORDER BY fecha_guardia DESC
        """, (session["usuario"],))

        datos = cursor.fetchall()

        for d in datos:
            fecha = d.get('fecha_guardia')
            if isinstance(fecha, date):
                d['dia_semana'] = DIAS_ES[fecha.weekday()]
            else:
                d['dia_semana'] = str(fecha) if fecha else '—'

        # 🔥 NOTIFICACIONES
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

        # 🔥 ALERTAS DE VACACIONES (para el panel de notificaciones)
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
                       AND YEAR(v.fecha_inicio) = YEAR(CURDATE())
                    ), 0
                ) AS dias_tomados,
                TIMESTAMPDIFF(YEAR, u.fecha_ingreso, CURDATE()) * 30 AS total_acumulado,
                COALESCE(
                    (SELECT SUM(DATEDIFF(v.fecha_fin, v.fecha_inicio) + 1)
                     FROM vacaciones v
                     WHERE v.id_usuario = u.id_usuario), 0
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

        # 🔥 CONTADORES
        total = len(datos)
        asistencias = sum(1 for d in datos if d["asistencia"] == "asistio")
        faltas = sum(1 for d in datos if d["asistencia"] == "falta")
        pendientes = sum(1 for d in datos if d["asistencia"] == "sin registro")

        return render_template(
            "index.html",
            datos=datos,
            total=total,
            asistencias=asistencias,
            faltas=faltas,
            pendientes=pendientes,
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

# -------------------------------------------------
# NOTIFICACIONES (fiscalizador)
# -------------------------------------------------
# Marcar una notificación como leída

@app.route("/notificaciones/leer/<int:id_notificacion>", methods=["POST"])
def marcar_notificacion_leida(id_notificacion):
    if "usuario" not in session:
        return {"ok": False, "error": "No autorizado"}, 401

    cursor = conexion.cursor()
    try:
        cursor.execute("""
            UPDATE notificaciones
            SET leida = TRUE
            WHERE id_notificacion = %s
              AND id_usuario = (
                  SELECT id_usuario FROM usuarios WHERE usuario = %s
              )
        """, (id_notificacion, session["usuario"]))
        conexion.commit()
        return {"ok": True}
    except Exception as e:
        conexion.rollback()
        return {"ok": False, "error": str(e)}, 500
    finally:
        cursor.close()

@app.route("/notificaciones/leer_todas", methods=["POST"])
def marcar_todas_leidas():
    if "usuario" not in session:
        return {"ok": False, "error": "No autorizado"}, 401

    cursor = conexion.cursor()
    try:
        cursor.execute("""
            UPDATE notificaciones
            SET leida = TRUE
            WHERE id_usuario = (
                SELECT id_usuario FROM usuarios WHERE usuario = %s
            )
              AND leida = FALSE
        """, (session["usuario"],))
        conexion.commit()
        return {"ok": True}
    except Exception as e:
        conexion.rollback()
        return {"ok": False, "error": str(e)}, 500
    finally:
        cursor.close()

@app.route("/notificaciones/nuevas")
def notificaciones_nuevas():
    if "usuario" not in session:
        return {"ok": False, "error": "No autorizado"}, 401

    cursor = conexion.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT id_notificacion, titulo, mensaje, fecha_creacion, leida
            FROM notificaciones
            WHERE id_usuario = (
                SELECT id_usuario FROM usuarios WHERE usuario = %s
            )
              AND leida = FALSE
            ORDER BY fecha_creacion DESC
            LIMIT 10
        """, (session["usuario"],))
        notifs = cursor.fetchall()

        for n in notifs:
            fc = n.get('fecha_creacion')
            if isinstance(fc, datetime):
                n['fecha_creacion_str'] = fc.strftime('%d/%m/%Y %H:%M')
            elif isinstance(fc, date):
                n['fecha_creacion_str'] = fc.strftime('%d/%m/%Y')
            else:
                n['fecha_creacion_str'] = str(fc) if fc else '—'

        return {"ok": True, "notificaciones": notifs}
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500
    finally:
        cursor.close()

# ==========================
# ADMIN — GUARDIAS
# ==========================
# Listado, creación, edición y eliminación de guardias

@app.route("/guardias")
def ver_guardias():

    if "usuario" not in session:
        return redirect(url_for("home"))

    cursor = conexion.cursor(dictionary=True)

    id_usuario = request.args.get("id_usuario", "").strip()
    fecha_desde = request.args.get("fecha_desde", "").strip()
    fecha_hasta = request.args.get("fecha_hasta", "").strip()
    asistencia = request.args.get("asistencia", "").strip()

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
def agregar_guardia():

    if "usuario" not in session:
        return redirect(url_for("home"))

    # 🔥 CORRECCIÓN: perfil_activo
    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    id_usuario = request.form.get("id_usuario", "").strip()
    fecha_guardia = request.form.get("fecha_guardia", "").strip()

    if not id_usuario or not fecha_guardia:
        flash("Todos los campos son obligatorios", "error")
        return redirect(url_for("ver_guardias"))

    try:
        datetime.strptime(fecha_guardia, "%Y-%m-%d")
    except ValueError:
        flash("Formato de fecha inválido. Use YYYY-MM-DD", "error")
        return redirect(url_for("ver_guardias"))

    cursor = conexion.cursor()

    try:
        cursor.execute("""
            SELECT 1 FROM guardias
            WHERE id_usuario = %s AND fecha_guardia = %s
            LIMIT 1
        """, (id_usuario, fecha_guardia))

        if cursor.fetchone():
            flash("Este fiscalizador ya tiene una guardia asignada en esa fecha", "error")
            return redirect(url_for("ver_guardias"))

        cursor.execute("""
            INSERT INTO guardias (id_usuario, fecha_guardia)
            VALUES (%s, %s)
        """, (id_usuario, fecha_guardia))

        conexion.commit()
        flash("Guardia registrada correctamente", "success")
        return redirect(url_for("ver_guardias"))

    except mysql.connector.errors.IntegrityError as e:
        conexion.rollback()
        if e.errno == 1062:
            flash("Este fiscalizador ya tiene una guardia asignada en esa fecha", "error")
        else:
            flash("No se pudo registrar la guardia: datos duplicados o inválidos", "error")
        return redirect(url_for("ver_guardias"))

    except mysql.connector.errors.OperationalError as e:
        conexion.rollback()
        if e.errno == 1205:
            flash("La base de datos está ocupada, intente nuevamente en unos segundos", "error")
        else:
            flash("Error de conexión con la base de datos, intente nuevamente", "error")
        return redirect(url_for("ver_guardias"))

    except Exception as e:
        conexion.rollback()
        print("ERROR agregar_guardia:", e)
        flash("Ocurrió un error al registrar la guardia. Intente nuevamente.", "error")
        return redirect(url_for("ver_guardias"))

    finally:
        cursor.close()


# ADMIN — Editar guardia (GET muestra formulario, POST guarda cambios)
@app.route("/editar_guardia/<int:id>", methods=["GET", "POST"])
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

            id_usuario = request.form["id_usuario"]
            fecha_guardia = request.form["fecha_guardia"]

            cursor.execute("""
                UPDATE guardias
                SET id_usuario=%s, fecha_guardia=%s
                WHERE id_guardia=%s
            """, (id_usuario, fecha_guardia, id))

            conexion.commit()
            flash("Guardia actualizada correctamente", "success")
            return redirect(url_for("ver_guardias"))

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
        """)

        fiscalizadores = cursor.fetchall()

        return render_template(
            "editar_guardia.html",
            guardia=guardia,
            fiscalizadores=fiscalizadores
        )

    finally:
        cursor.close()


@app.route("/eliminar_guardia/<int:id>", methods=["POST"])
def eliminar_guardia(id):

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    cursor = conexion.cursor()

    try:
        cursor.execute("""
            DELETE FROM guardias
            WHERE id_guardia = %s
        """, (id,))

        conexion.commit()

        flash("Guardia eliminada correctamente", "success")
        return redirect(url_for("ver_guardias"))

    except Exception as e:

        conexion.rollback()

        print("ERROR ELIMINAR GUARDIA:", e)

        return error_interno()

    finally:

        cursor.close()


# ==========================
# ADMIN — ASISTENCIA
# ==========================
# Registrar asistencia (asistió, falta, justificado) desde el panel admin

@app.route("/asistencia/<int:id_guardia>/<estado>", methods=["POST"])
def registrar_asistencia(id_guardia, estado):

    if "usuario" not in session:
        return redirect(url_for("home"))

    # 🔥 CORRECCIÓN: usar perfil_activo en vez de rol
    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    if estado not in ["asistio", "falta", "justificado"]:
        return datos_invalidos("Estado inválido")

    cursor = conexion.cursor()

    try:
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
                return redirect(url_for("asistencia_admin"))

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
        flash(mensajes.get(estado, "Asistencia actualizada"), "success")
        return redirect(url_for("asistencia_admin"))

    finally:
        cursor.close()


# ADMIN — Listado de asistencias con filtros por usuario, fecha y estado

@app.route("/asistencias")
def asistencia_admin():

    if "usuario" not in session:
        return redirect(url_for("home"))

    # 🔥 CORRECCIÓN AQUÍ TAMBIÉN
    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    cursor = conexion.cursor(dictionary=True)

    id_usuario = request.args.get("id_usuario", "").strip()
    fecha_desde = request.args.get("fecha_desde", "").strip()
    fecha_hasta = request.args.get("fecha_hasta", "").strip()
    asistencia = request.args.get("asistencia", "").strip()

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

@app.route("/asistencia")
def asistencia():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo", "").lower() != "fiscalizador":
        return acceso_no_autorizado()

    cursor = conexion.cursor(dictionary=True)

    cursor.execute("""
        SELECT *
        FROM resumen_guardias
        WHERE id_usuario = %s
        ORDER BY fecha_guardia DESC
    """, (session["id_usuario"],))

    datos = cursor.fetchall()

    for d in datos:
        fecha = d.get('fecha_guardia')
        if isinstance(fecha, date):
            d['dia_semana'] = DIAS_ES[fecha.weekday()]
        else:
            d['dia_semana'] = str(fecha) if fecha else '—'

    return render_template(
        "asistencia.html",
        datos=datos
    )
# ---------- FIN ASISTENCIA ----------

# ==========================
# FISCALIZADOR — MARCAR ASISTENCIA PROPIA
# ==========================
# Permite al fiscalizador registrar su asistencia del día

@app.route("/mi_asistencia")
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
            asistencia
        FROM resumen_guardias
        WHERE id_usuario = %s
        ORDER BY fecha_guardia DESC
    """, (session["id_usuario"],))

    datos = cursor.fetchall()

    hoy = date.today()

    for d in datos:

        fecha = d["fecha_guardia"]

        if isinstance(fecha, date):
            d['dia_semana'] = DIAS_ES[fecha.weekday()]
        else:
            d['dia_semana'] = str(fecha) if fecha else '—'

        # 🔥 1. YA REGISTRADO
        if d["asistencia"] != "sin registro":
            d["estado_accion"] = "registrado"

        # 🔥 2. SOLO HOY (ACTIVO)
        elif fecha == hoy:
            d["estado_accion"] = "hoy"

        # 🔥 3. FUTURO
        elif fecha > hoy:
            d["estado_accion"] = "futuro"

        # 🔥 4. PASADO SIN REGISTRO
        else:
            d["estado_accion"] = "cerrado"

    cursor.close()

    return render_template("asistencia_fiscalizadores.html", datos=datos)

# FISCALIZADOR — Registrar asistencia vía POST (autoservicio)
@app.route("/marcar_asistencia", methods=["POST"])
def marcar_asistencia():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo", "").lower() != "fiscalizador":
        return acceso_no_autorizado()

    id_guardia = request.form.get("id_guardia")
    id_usuario = session.get("id_usuario")

    if not id_guardia or not id_usuario:
        return datos_invalidos("Datos inválidos")

    # ✔ USAR TU CONEXIÓN GLOBAL (NO get_connection)
    conn = conexion
    cursor = conn.cursor(dictionary=True)

    try:

        # 🔥 VERIFICAR QUE LA GUARDIA PERTENECE AL FISCALIZADOR Y ES DE HOY
        cursor.execute("""
            SELECT id_guardia, fecha_guardia
            FROM guardias
            WHERE id_guardia = %s AND id_usuario = %s
        """, (id_guardia, id_usuario))

        guardia = cursor.fetchone()

        if not guardia:
            return acceso_no_autorizado()

        if guardia["fecha_guardia"] != date.today():
            flash("Solo puedes registrar tu asistencia del día de hoy", "warning")
            return redirect(url_for("mi_asistencia"))

        # 🔥 VERIFICAR SI YA REGISTRÓ
        cursor.execute("""
            SELECT id_asistencia
            FROM asistencia
            WHERE id_guardia = %s
        """, (id_guardia,))

        existe = cursor.fetchone()

        if existe:
            flash("Ya registraste tu asistencia", "warning")
            return redirect(url_for("mi_asistencia"))

        # ✔ INSERTAR ASISTENCIA
        cursor.execute("""
            INSERT INTO asistencia (id_guardia, estado)
            VALUES (%s, 'asistio')
        """, (id_guardia,))

        conn.commit()

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

@app.route("/mis_compensaciones")
def mis_compensaciones():

    if "usuario" not in session:
        return redirect(url_for("login.login"))

    if (session.get("perfil_activo") or "").lower() != "fiscalizador":
        return acceso_no_autorizado()

    fecha_guardia = request.args.get("fecha_guardia", "")
    anio = request.args.get("anio", "").strip()
    desde = request.args.get("desde", "").strip()
    hasta = request.args.get("hasta", "").strip()
    estado = request.args.get("estado", "")

    cursor = conexion.cursor(dictionary=True)

    query = """
        SELECT
            c.id_compensacion,
            g.fecha_guardia,
            c.fecha_compensacion,
            c.observacion,
            g.id_usuario,
            u.nombre,
            u.apellidos
        FROM compensaciones c
        INNER JOIN guardias g
            ON c.id_guardia = g.id_guardia
        INNER JOIN usuarios u
            ON g.id_usuario = u.id_usuario
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

    # FILTRO POR FECHA
    if fecha_guardia:
        query += " AND g.fecha_guardia = %s"
        parametros.append(fecha_guardia)

    query += " ORDER BY g.fecha_guardia DESC"

    cursor.execute(query, tuple(parametros))

    datos = cursor.fetchall()

    cursor.close()

    from datetime import date
    hoy = date.today()
    for c in datos:
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
                c.estado,
                g.id_usuario
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
            SELECT g.id_usuario FROM compensaciones c
            INNER JOIN guardias g ON c.id_guardia = g.id_guardia
            WHERE c.id_compensacion = %s
        """, (id_compensacion,))
        row = cursor.fetchone()
        if not row:
            return no_encontrado()
        if row[0] != session.get("id_usuario"):
            return acceso_no_autorizado()

        from datetime import date
        estado = 'usado' if date.today() >= date.fromisoformat(fecha) else 'pendiente'

        cursor.execute("""
            UPDATE compensaciones
            SET fecha_compensacion = %s,
                observacion = %s,
                estado = %s
            WHERE id_compensacion = %s
        """, (fecha, obs, estado, id_compensacion))

        conexion.commit()

        flash("Compensación actualizada correctamente", "success")
        compensacion = {
            "id_compensacion": id_compensacion,
            "fecha_compensacion": fecha,
            "observacion": obs,
            "estado": estado,
        }
        return render_template("editar_mi_compensacion.html",
                               compensacion=compensacion)

    finally:
        cursor.close()


# FISCALIZADOR — Eliminar compensación propia (POST)
@app.route("/eliminar_mi_compensacion/<int:id_compensacion>", methods=["POST"])
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

        flash("Compensación eliminada correctamente", "success")
        return redirect(url_for("mis_compensaciones"))

    finally:
        cursor.close()


# ==========================
# FISCALIZADOR — MIS GUARDIAS
# ==========================
# Listado de guardias propias con filtros por año, rango, asistencia y estado

@app.route("/mis_guardias")
def mis_guardias():

    # ================= VALIDAR SESIÓN =================
    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo", "").lower() != "fiscalizador":
        return acceso_no_autorizado()

    anio = request.args.get("anio")
    fecha_desde = request.args.get("fecha_desde")
    fecha_hasta = request.args.get("fecha_hasta")
    asistencia = request.args.get("asistencia")
    estado_guardia = request.args.get("estado_guardia")

    cursor = conexion.cursor(dictionary=True)

    try:

        sql = """
            SELECT
                g.id_guardia,
                g.fecha_guardia,

                CASE
                    WHEN LOWER(COALESCE(a.estado, '')) IN ('asistió')
                        THEN 'realizada'
                    WHEN LOWER(COALESCE(a.estado, '')) IN ('falta')
                        THEN 'cancelada'
                    ELSE 'programada'
                END AS estado_guardia,

                ELT(DAYOFWEEK(g.fecha_guardia),
                    'Domingo','Lunes','Martes','Miércoles',
                    'Jueves','Viernes','Sábado') AS dia_semana,

                CASE
                    WHEN LOWER(COALESCE(a.estado, '')) IN ('asistió')
                        THEN 'Asistió'
                    WHEN LOWER(COALESCE(a.estado, '')) IN ('falta')
                        THEN 'Falta'
                    ELSE 'Pendiente'
                END AS asistencia,

                c.fecha_compensacion,
                c.estado AS estado_compensacion,
                c.observacion AS observacion_compensacion

            FROM guardias g
            LEFT JOIN asistencia a ON g.id_guardia = a.id_guardia
            LEFT JOIN compensaciones c ON g.id_guardia = c.id_guardia

            WHERE g.id_usuario = %s
        """

        params = [session["id_usuario"]]

        # ================= FILTRO AÑO =================
        if anio:
            sql += " AND YEAR(g.fecha_guardia) = %s "
            params.append(anio)

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
                sql += """
                    AND (
                        CASE
                            WHEN LOWER(COALESCE(a.estado, '')) IN ('asistió')
                                THEN 'Asistió'
                            WHEN LOWER(COALESCE(a.estado, '')) IN ('falta')
                                THEN 'Falta'
                            ELSE 'Pendiente'
                        END
                    ) = 'Asistió'
                """
            elif asistencia == "Falta":
                sql += """
                    AND (
                        CASE
                            WHEN LOWER(COALESCE(a.estado, '')) IN ('asistió')
                                THEN 'Asistió'
                            WHEN LOWER(COALESCE(a.estado, '')) IN ('falta')
                                THEN 'Falta'
                            ELSE 'Pendiente'
                        END
                    ) = 'Falta'
                """
            elif asistencia == "Pendiente":
                sql += """
                    AND (
                        CASE
                            WHEN LOWER(COALESCE(a.estado, '')) IN ('asistió')
                                THEN 'Asistió'
                            WHEN LOWER(COALESCE(a.estado, '')) IN ('falta')
                                THEN 'Falta'
                            ELSE 'Pendiente'
                        END
                    ) = 'Pendiente'
                """

        # ================= FILTRO ESTADO GUARDIA =================
        if estado_guardia == "realizada":
            sql += " AND LOWER(COALESCE(a.estado, '')) IN ('asistió') "
        elif estado_guardia == "cancelada":
            sql += " AND LOWER(COALESCE(a.estado, '')) IN ('falta') "
        elif estado_guardia == "programada":
            sql += """ AND (
                a.estado IS NULL
                OR LOWER(COALESCE(a.estado, '')) NOT IN ('asistió','falta')
            ) """

        sql += " ORDER BY g.fecha_guardia DESC"

        cursor.execute(sql, tuple(params))
        guardias = cursor.fetchall()

        return render_template(
            "mi_guardias.html",
            guardias=guardias
        )

    finally:
        cursor.close()



# ==========================
# FISCALIZADOR — MIS FERIADOS
# ==========================
# Feriados en los que el fiscalizador tiene guardia asignada

@app.route("/mis_feriados")
def mis_feriados():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo", "").lower() != "fiscalizador":
        return acceso_no_autorizado()

    anio = request.args.get("anio", "").strip()
    fecha_desde = request.args.get("fecha_desde", "").strip()
    fecha_hasta = request.args.get("fecha_hasta", "").strip()
    descripcion = request.args.get("descripcion", "").strip()

    cursor = conexion.cursor(dictionary=True)

    try:

        sql = """
            SELECT DISTINCT f.*
            FROM feriados f
            INNER JOIN guardias g ON f.id_feriado = g.id_feriado
            WHERE g.id_usuario = %s
        """
        params = [session["id_usuario"]]

        if anio:
            sql += " AND YEAR(f.fecha) = %s"
            params.append(anio)

        if fecha_desde:
            sql += " AND f.fecha >= %s"
            params.append(fecha_desde)

        if fecha_hasta:
            sql += " AND f.fecha <= %s"
            params.append(fecha_hasta)

        if descripcion:
            sql += " AND f.descripcion LIKE %s"
            params.append(f"%{descripcion}%")

        sql += " ORDER BY f.fecha DESC"

        cursor.execute(sql, params)
        feriados = cursor.fetchall()

        DIAS_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]

        for f in feriados:
            if f.get("fecha"):
                f["dia"] = DIAS_ES[f["fecha"].weekday()]

        return render_template(
            "mis_feriados.html",
            feriados=feriados
        )

    finally:

        cursor.close()


# ==========================
# FISCALIZADOR — MIS VACACIONES
# ==========================
# Vacaciones propias con filtros y cálculo de días pendientes

@app.route("/mis_vacaciones")
def mis_vacaciones():

    # =========================
    # VALIDACIÓN DE SESIÓN
    # =========================
    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "fiscalizador":
        return acceso_no_autorizado()

    # ⚠️ IMPORTANTE: conexion YA ES OBJETO (NO SE USA ())
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
            SELECT fecha_ingreso FROM usuarios WHERE id_usuario = %s
        """, (session["id_usuario"],))

        user = cursor.fetchone()
        dias_pendientes = 0
        dias_pendientes_este_anio = 0
        dias_pendientes_anteriores = 0

        if user and user["fecha_ingreso"]:
            fecha_ingreso = user["fecha_ingreso"]
            today = date.today()

            anniv = fecha_ingreso.replace(year=fecha_ingreso.year + 1)
            years = 0
            while anniv <= today:
                years += 1
                anniv = anniv.replace(year=anniv.year + 1)

            total_dias = years * 30

            cursor.execute("""
                SELECT 
                    COALESCE(SUM(DATEDIFF(v.fecha_fin, v.fecha_inicio) + 1), 0) AS total,
                    COALESCE(SUM(CASE WHEN YEAR(v.fecha_inicio) = YEAR(CURDATE())
                                      THEN DATEDIFF(v.fecha_fin, v.fecha_inicio) + 1
                                      ELSE 0 END), 0) AS total_anio
                FROM vacaciones v
                WHERE v.id_usuario = %s
            """, (session["id_usuario"],))

            result = cursor.fetchone()
            dias_tomados = result["total"] if result else 0
            dias_tomados_anio = result["total_anio"] if result else 0

            dias_pendientes_este_anio = max(0, 30 - dias_tomados_anio)
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

# -----------------------------------------------
# Registro de blueprints modulares
# -----------------------------------------------
app.register_blueprint(login_bp)
app.register_blueprint(informe_bp)
app.register_blueprint(registro_bp)
app.register_blueprint(reporte_bp)
app.register_blueprint(mis_reportes_bp, url_prefix="/mis")
app.register_blueprint(informes_bp)
app.register_blueprint(fiscalizadores_bp)
app.register_blueprint(perfil_bp)

# -----------------------------------------------
# Health check para monitoreo
# -----------------------------------------------
@app.route("/health")
def health():
    return {"status": "ok", "app": "SIGGO"}, 200

# -----------------------------------------------
# Manejadores de errores globales
# -----------------------------------------------
@app.errorhandler(mysql.connector.errors.Error)
def handle_db_error(error):
    return render_template(
        "error.html",
        codigo="Error de base de datos",
        titulo="Servicio no disponible",
        mensaje="La base de datos no está accesible en este momento. Intente nuevamente en unos minutos.",
        volver_url="/",
        volver_texto="Reintentar"
    ), 503

@app.errorhandler(500)
def handle_500(error):
    return render_template(
        "error.html",
        codigo="Error 500",
        titulo="Error interno",
        mensaje="Error interno del servidor. Intente nuevamente.",
        volver_url="/",
        volver_texto="Volver al inicio"
    ), 500

# -----------------------------------------------
# Arranque de la aplicación con Waitress (producción)
# -----------------------------------------------
if __name__ == "__main__":
    from waitress import serve
    port = int(os.getenv("PORT", 5000))
    host = os.getenv("HOST", "127.0.0.1")
    threads = int(os.getenv("WAITRESS_THREADS", 8))
    print(f"\n{'='*60}")
    print(f"  SIGGO - Guardia OIG")
    print(f"  http://{host}:{port}")
    print(f"{'='*60}\n")
    serve(app, host=host, port=port, threads=threads)