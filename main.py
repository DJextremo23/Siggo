import os
import sys
import logging
from logging.handlers import RotatingFileHandler
from flask import Flask, render_template, request, redirect, session, url_for, flash
from flask_talisman import Talisman
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv
from limiter_instance import limiter
from reporte import reporte_bp
from conexion import ConexionDB
from mis_reportes import mis_reportes_bp
from csrf import generate_token, validate_csrf
from utils import error_response, acceso_no_autorizado, error_interno, datos_invalidos, no_encontrado, guardar_filtros, redirigir_con_filtros, registrar_auditoria

from datetime import datetime, timedelta, date
from login import login_bp
from registro import registro_bp
from mis_informes import informe_bp
from informes import informes_bp
from fiscalizadores import fiscalizadores_bp
from perfil import perfil_bp
import mysql.connector.errors

"""
Aplicación principal de SIGGO.

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
# Logging: archivo con rotación + consola
# -----------------------------------------------
def configurar_logging():
    log_dir = os.getenv("LOG_DIR", "logs")
    os.makedirs(log_dir, exist_ok=True)

    formato = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    archivo = RotatingFileHandler(
        os.path.join(log_dir, "app.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    archivo.setFormatter(formato)

    consola = logging.StreamHandler()
    consola.setFormatter(formato)

    nivel = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)

    root = logging.getLogger()
    root.setLevel(nivel)
    root.addHandler(archivo)
    root.addHandler(consola)


class _StreamToLogger:
    """Redirige stdout/stderr hacia el logger (captura print y trazas no controladas)."""
    def __init__(self, level):
        self._level = level

    def write(self, buf):
        if buf and buf.strip():
            logging.log(self._level, buf.rstrip())

    def flush(self):
        pass


configurar_logging()


# -----------------------------------------------
# Configuración de la app y seguridad
# -----------------------------------------------

app = Flask(__name__)

# La clave de sesión DEBE venir del entorno; si falta, se aborta el arranque
# para no firmar sesiones con una clave aleatoria que cambiaría en cada reinicio.
_secret_key = os.getenv("SECRET_KEY")
if not _secret_key:
    raise RuntimeError("SECRET_KEY no está configurada en el entorno")
app.secret_key = _secret_key

# Detectar HTTPS detrás de un proxy inverso (Railway / nginx)
# Permite que session_cookie_secure y force_https funcionen correctamente
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Configuración de sesión: timeout por inactividad + tope absoluto (mejores prácticas OWASP)
SESSION_IDLE_MINUTOS = int(os.getenv("SESSION_IDLE_MINUTOS", "60"))     # inactividad: 1 hora
SESSION_ABSOLUTO_HORAS = int(os.getenv("SESSION_ABSOLUTO_HORAS", "8"))  # tope máximo: 8 horas
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=SESSION_ABSOLUTO_HORAS)
# Límite duro de subida: evita recibir archivos enormes (las fotos se comprimen en el cliente a <1 MB)
app.config["MAX_CONTENT_LENGTH"] = 6 * 1024 * 1024  # 6 MB


def formatear_fecha(valor, con_hora=False):
    """Formatea una fecha como día.mes.año (DD.MM.YYYY)."""
    if valor is None or valor == "":
        return "—"
    if isinstance(valor, datetime):
        return valor.strftime("%d.%m.%Y %H:%M") if con_hora else valor.strftime("%d.%m.%Y")
    if isinstance(valor, date):
        return valor.strftime("%d.%m.%Y")
    s = str(valor).strip()
    if not s:
        return "—"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.strftime("%d.%m.%Y %H:%M") if con_hora else dt.strftime("%d.%m.%Y")
        except ValueError:
            continue
    return s


app.jinja_env.filters["fecha"] = formatear_fecha

# Rate limiting global (límites por IP)
limiter._default_limits = ["200 per hour", "20 per minute"]
limiter.init_app(app)

# HTTPS forzado (según variable de entorno) y cabeceras de seguridad
FORCE_HTTPS = os.getenv("FORCE_HTTPS", "false").lower() == "true"
Talisman(
    app,
    force_https=False,
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
    content_security_policy={
        "default-src": ["'self'", "blob:"],
        "script-src": ["'self'", "https://cdn.jsdelivr.net", "https://cdnjs.cloudflare.com"],
        "style-src": ["'self'", "https://cdn.jsdelivr.net", "https://cdnjs.cloudflare.com", "https://fonts.googleapis.com"],
        "style-src-attr": ["'unsafe-inline'"],
        "img-src": ["'self'", "data:", "blob:"],
        "font-src": ["'self'", "https://fonts.gstatic.com", "https://cdnjs.cloudflare.com"],
        "connect-src": ["'self'"],
        "frame-ancestors": ["'none'"],
    },
    content_security_policy_nonce_in=["script-src", "style-src"],
)


@app.before_request
def forzar_https():
    """Redirige HTTP a HTTPS, excepto en el health check (evita reinicios en Railway)."""
    if not FORCE_HTTPS or request.is_secure:
        return
    if request.path == "/health":
        return
    url = request.url.replace("http://", "https://", 1)
    return redirect(url, code=301)


# Protección CSRF: inyección de token en plantillas y validación en cada petición
@app.context_processor
def inject_csrf_token():
    return {"csrf_token": generate_token()}


@app.before_request
def csrf_check():
    validate_csrf()


@app.before_request
def verificar_expiracion_sesion():
    """Cierra la sesión por inactividad o por tiempo máximo (enforcement en servidor)."""
    if "usuario" not in session:
        return

    ahora = datetime.now().timestamp()

    login_at = session.get("_login_at")
    if login_at is None:
        session["_login_at"] = ahora
        login_at = ahora

    last_activity = session.get("_last_activity")
    if last_activity is None:
        session["_last_activity"] = ahora
        last_activity = ahora

    # Tope absoluto: fuerza re-login aunque el usuario haya estado activo
    if ahora - login_at > SESSION_ABSOLUTO_HORAS * 3600:
        session.clear()
        return redirect(url_for("login.login"))

    # Inactividad: cierra si no hubo actividad dentro del período definido
    if ahora - last_activity > SESSION_IDLE_MINUTOS * 60:
        session.clear()
        return redirect(url_for("login.login"))

    # Renovar la marca de actividad (timeout deslizante)
    session["_last_activity"] = ahora


@app.after_request
def no_cache(resp):
    """Evita que el navegador almacene páginas en caché y muestre datos desactualizados."""
    if resp.mimetype == "text/html":
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


# ── Medicion de rendimiento por request (diagnóstico) ────────────────────────
import time as _time_prof


@app.before_request
def _perf_inicio():
    request._perf_t0 = _time_prof.time()


@app.after_request
def _perf_log(resp):
    try:
        dt = _time_prof.time() - getattr(request, "_perf_t0", _time_prof.time())
        if dt >= 0.5:
            print(f"[PERF] {request.method} {request.path} -> {dt:.2f}s ({resp.status_code})")
    except Exception:
        pass
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

# Meses abreviados en español para los bloques de fecha
MESES_ABREV = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']

def _balance_vacaciones_fifo(anio_ingreso, dias_tomados_total):
    """Calcula el balance de vacaciones con atribución FIFO (igual que reportes).

    Los días tomados se descuentan primero de los períodos anteriores al año
    actual; el excedente se atribuye al año en curso. Devuelve
    (dias_tomados_este_anio, dias_pendientes_este_anio, dias_pendientes_anteriores).
    """
    anio_actual = date.today().year
    ingreso = int(anio_ingreso or anio_actual)
    tomados = int(dias_tomados_total or 0)
    if anio_actual <= ingreso:
        return 0, 0, 0
    ent_antes = (anio_actual - ingreso - 1) * 30
    dias_tomados_este_anio = min(30, max(0, tomados - ent_antes))
    dias_pendientes_este_anio = max(0, 30 - dias_tomados_este_anio)
    dias_pendientes_anteriores = max(0, ent_antes - tomados)
    return dias_tomados_este_anio, dias_pendientes_este_anio, dias_pendientes_anteriores

# ==========================
# RUTAS DE INICIO / LOGIN
# ==========================

@app.route("/")
def home():
    if "usuario" in session:
        return redirect(url_for("inicio"))
    return render_template("login.html")

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
            WHERE r.nombre_rol = 'fiscalizador'
              AND u.estado = 'activo'
        """)

        alertas = cursor.fetchall()

        for a in alertas:
            a["dias_tomados"], a["dias_pendientes_este_anio"], a["dias_pendientes_anteriores"] = \
                _balance_vacaciones_fifo(a["anio_ingreso"], a["dias_tomados_total"])

        # Totales del dashboard
        cursor.execute("SELECT COUNT(*) AS total FROM usuarios u INNER JOIN usuarios_roles ur ON u.id_usuario = ur.id_usuario INNER JOIN roles r ON ur.id_rol = r.id_rol WHERE r.nombre_rol = 'fiscalizador' AND u.estado = 'activo'")
        total_usuarios = cursor.fetchone()["total"]

        cursor.execute("SELECT COUNT(*) AS total FROM guardias WHERE YEAR(fecha_guardia) = YEAR(CURDATE())")
        total_guardias = cursor.fetchone()["total"]

        cursor.execute("SELECT COUNT(*) AS total FROM informes i INNER JOIN guardias g ON i.id_guardia = g.id_guardia WHERE i.estado = 'activo' AND YEAR(g.fecha_guardia) = YEAR(CURDATE())")
        total_informes = cursor.fetchone()["total"]

        cursor.execute("SELECT COUNT(*) AS total FROM compensaciones c INNER JOIN guardias g ON c.id_guardia = g.id_guardia WHERE YEAR(g.fecha_guardia) = YEAR(CURDATE())")
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
                    AND YEAR(g.fecha_guardia) = YEAR(CURDATE())
                    AND g.fecha_guardia <= CURDATE()) AS pendientes
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
            SELECT g.id_guardia, g.fecha_guardia, CONCAT(u.nombre,' ',u.apellidos) AS fiscalizador,
                   f.descripcion AS feriado
            FROM guardias g
            INNER JOIN usuarios u ON g.id_usuario = u.id_usuario
            LEFT JOIN feriados f ON g.fecha_guardia = f.fecha
            WHERE g.fecha_guardia >= CURDATE()
              AND g.tipo = 'guardia'
            ORDER BY g.fecha_guardia ASC
            LIMIT 5
        """)
        proximas_guardias = cursor.fetchall()
        for g in proximas_guardias:
            g["es_feriado"] = g.get("feriado") is not None
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
# ADMIN — COMPENSACIONES
# ==========================
# Listado, edición y eliminación de compensaciones desde el panel admin

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

        flash("Compensación registrada correctamente", "success")
        return redirigir_con_filtros("compensaciones_admin", "filtro_compensaciones")

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
        return redirigir_con_filtros("compensaciones_admin", "filtro_compensaciones")

    except Exception as e:

        conexion.rollback()

        print("ERROR ELIMINAR COMPENSACIÓN:", e)

        return error_interno()

    finally:

        cursor.close()

@app.route("/eliminar_compensaciones", methods=["POST"])
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

    guardar_filtros("filtro_feriados")

    anio = request.args.get("anio", "").strip()
    fecha_desde = request.args.get("fecha_desde", "").strip()
    fecha_hasta = request.args.get("fecha_hasta", "").strip()
    descripcion = request.args.get("descripcion", "").strip()

    cursor = conexion.cursor(dictionary=True)

    try:

        sql = "SELECT id_feriado, fecha, descripcion FROM feriados WHERE 1=1 "
        params = []

        if anio:
            try:
                anio_int = int(anio)
            except (TypeError, ValueError):
                anio_int = None
            if anio_int:
                sql += " AND YEAR(fecha) = %s "
                params.append(anio_int)

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
        return redirigir_con_filtros("feriados", "filtro_feriados")

    try:
        datetime.strptime(fecha, "%Y-%m-%d")
    except ValueError:
        flash("Formato de fecha inválido. Use YYYY-MM-DD", "error")
        return redirigir_con_filtros("feriados", "filtro_feriados")

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
        return redirigir_con_filtros("feriados", "filtro_feriados")

    except mysql.connector.errors.IntegrityError as e:
        conexion.rollback()
        if e.errno == 1062:
            flash("Ya existe un feriado registrado en esa fecha", "error")
        else:
            flash("No se pudo registrar el feriado: datos duplicados o inválidos", "error")
        return redirigir_con_filtros("feriados", "filtro_feriados")

    except mysql.connector.errors.OperationalError as e:
        conexion.rollback()
        if e.errno == 1205:
            flash("La base de datos está ocupada, intente nuevamente en unos segundos", "error")
        else:
            flash("Error de conexión con la base de datos, intente nuevamente", "error")
        return redirigir_con_filtros("feriados", "filtro_feriados")

    except Exception as e:

        conexion.rollback()

        print("ERROR GUARDAR FERIADO:", e)

        flash("Ocurrió un error al registrar el feriado. Intente nuevamente.", "error")
        return redirigir_con_filtros("feriados", "filtro_feriados")

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
            SELECT id_feriado, fecha, descripcion
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

    fecha = request.form.get("fecha", "").strip()
    descripcion = request.form.get("descripcion", "").strip()

    if not fecha or not descripcion:
        flash("Fecha y descripción son obligatorios", "error")
        return redirigir_con_filtros("feriados", "filtro_feriados")

    try:
        datetime.strptime(fecha, "%Y-%m-%d")
    except ValueError:
        flash("Formato de fecha inválido. Use YYYY-MM-DD", "error")
        return redirigir_con_filtros("feriados", "filtro_feriados")

    cursor = conexion.cursor()

    try:
        cursor.execute("""
            UPDATE feriados
            SET fecha = %s, descripcion = %s
            WHERE id_feriado = %s
        """, (fecha, descripcion, id))

        conexion.commit()

        flash("Feriado actualizado correctamente", "success")
        return redirigir_con_filtros("feriados", "filtro_feriados")

    except mysql.connector.errors.IntegrityError as e:
        conexion.rollback()
        if e.errno == 1062:
            flash("Ya existe un feriado registrado en esa fecha", "error")
        else:
            flash("No se pudo actualizar el feriado: datos duplicados o inválidos", "error")
        return redirigir_con_filtros("feriados", "filtro_feriados")

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
        return redirigir_con_filtros("feriados", "filtro_feriados")

    except Exception as e:

        conexion.rollback()

        print("ERROR ELIMINAR FERIADO:", e)

        return error_interno()

    finally:

        cursor.close()

@app.route("/eliminar_feriados", methods=["POST"])
def eliminar_feriados():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    ids = request.form.getlist("ids_feriado")
    if not ids:
        flash("No seleccionó ningún feriado", "error")
        return redirigir_con_filtros("feriados", "filtro_feriados")

    ids_int = []
    for valor in ids:
        try:
            ids_int.append(int(valor))
        except (TypeError, ValueError):
            return datos_invalidos("Identificador de feriado inválido")

    cursor = conexion.cursor()

    try:
        placeholders = ",".join(["%s"] * len(ids_int))
        cursor.execute(
            f"DELETE FROM feriados WHERE id_feriado IN ({placeholders})",
            tuple(ids_int),
        )
        conexion.commit()
        registrar_auditoria("feriados_eliminados", f"Eliminados {cursor.rowcount} feriados")
        flash(f"Se eliminaron {cursor.rowcount} feriados correctamente", "success")
    except Exception as e:
        conexion.rollback()
        print("ERROR ELIMINAR FERIADOS:", e)
        flash("Ocurrió un error al eliminar los feriados", "error")
    finally:
        cursor.close()

    return redirigir_con_filtros("feriados", "filtro_feriados")



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
                _balance_vacaciones_fifo(a["anio_ingreso"], a["dias_tomados_total"])

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
                   (SELECT CONCAT(nombre,' ',apellidos) FROM usuarios WHERE id_usuario = vacaciones.id_usuario) AS nombre_completo
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
                f"Vacaciones modificadas: del {inicio} al {fin} (antes: {old_data['fecha_inicio_ant']} al {old_data['fecha_fin_ant']}) para {old_data['nombre_completo']}."
            ))

        conexion.commit()

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
        return redirigir_con_filtros("vacaciones", "filtro_vacaciones")

    except Exception as e:
        conexion.rollback()
        print("ERROR eliminar_vacacion:", e)
        return error_interno()

    finally:
        cursor.close()

@app.route("/eliminar_vacaciones", methods=["POST"])
def eliminar_vacaciones():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
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

@app.route("/inicio")
def inicio():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo", "").lower() == "admin":
        return redirect(url_for("administrador"))

    if session.get("perfil_activo", "").lower() != "fiscalizador":
        return acceso_no_autorizado()

    cursor = None

    try:
        cursor = conexion.cursor(dictionary=True)

        # 🔥 Obtener datos desde la vista correcta
        cursor.execute("""
            SELECT id_guardia, fecha_guardia, tipo_dia, feriado, asistencia, tipo
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
            WHERE r.nombre_rol = 'fiscalizador'
              AND u.estado = 'activo'
              AND u.usuario = %s
        """, (session["usuario"],))
        alertas = cursor.fetchall()

        for a in alertas:
            a["dias_tomados"], a["dias_pendientes_este_anio"], a["dias_pendientes_anteriores"] = \
                _balance_vacaciones_fifo(a["anio_ingreso"], a["dias_tomados_total"])

        return render_template(
            "fiscalizador.html",
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
        print("ERROR notificaciones:", e)
        return {"ok": False, "error": "Error interno del servidor."}, 500
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
        print("ERROR notificaciones:", e)
        return {"ok": False, "error": "Error interno del servidor."}, 500
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
        print("ERROR notificaciones nuevas:", e)
        return {"ok": False, "error": "Error interno del servidor."}, 500
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
def agregar_guardia():

    if "usuario" not in session:
        return redirect(url_for("home"))

    # 🔥 CORRECCIÓN: perfil_activo
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
def eliminar_guardia(id):

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    cursor = conexion.cursor()

    try:
        _eliminar_guardias_con_archivos(cursor, [id])

        conexion.commit()

        flash("Guardia eliminada correctamente", "success")
        return redirigir_con_filtros("ver_guardias", "filtro_guardias")

    except Exception as e:

        conexion.rollback()

        print("ERROR ELIMINAR GUARDIA:", e)

        return error_interno()

    finally:

        cursor.close()

@app.route("/eliminar_guardias", methods=["POST"])
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
        flash(mensajes.get(estado, "Asistencia actualizada"), "success")
        return redirigir_con_filtros("asistencia_admin", "filtro_asistencias")

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

        # 🔥 1. YA REGISTRADO
        if d["asistencia"] != "sin registro":
            d["estado_accion"] = "registrado"

        # 🔥 2. FUTURO
        elif fecha > hoy:
            d["estado_accion"] = "futuro"

        # 🔥 3. DENTRO DE 48 HORAS (HOY O AYER) — ACTIVO
        elif fecha >= limite:
            d["estado_accion"] = "hoy"

        # 🔥 4. PASADO SIN REGISTRO (fuera de las 48 horas)
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

        hoy = date.today()
        limite = hoy - timedelta(days=1)  # ventana de 48 horas
        if not (limite <= guardia["fecha_guardia"] <= hoy):
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

        flash("Compensación actualizada correctamente", "success")
        return redirigir_con_filtros("mis_compensaciones", "filtro_mis_compensaciones")

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
        return redirigir_con_filtros("mis_compensaciones", "filtro_mis_compensaciones")

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
            SELECT DISTINCT f.id_feriado, f.fecha, f.descripcion
            FROM feriados f
            INNER JOIN guardias g ON f.fecha = g.fecha_guardia
            WHERE g.id_usuario = %s
        """
        params = [session["id_usuario"]]

        if anio:
            try:
                anio_int = int(anio)
            except (TypeError, ValueError):
                anio_int = None
            if anio_int:
                sql += " AND YEAR(f.fecha) = %s"
                params.append(anio_int)

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
                _balance_vacaciones_fifo(user["anio_ingreso"], dias_tomados_total)
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
app.register_blueprint(mis_reportes_bp, url_prefix="/mis_reportes")
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
    # En producción, redirigir stdout/stderr al log para capturar
    # los print de errores y las trazas no controladas.
    sys.stdout = _StreamToLogger(logging.INFO)
    sys.stderr = _StreamToLogger(logging.ERROR)

    from waitress import serve
    port = int(os.getenv("PORT", 8080))
    host = "0.0.0.0"
    threads = int(os.getenv("WAITRESS_THREADS", 8))
    print(f"\n{'='*60}")
    print("  SIGGO - Guardia OIG")
    print(f"  http://{host}:{port}")
    print(f"{'='*60}\n")
    serve(app, host=host, port=port, threads=threads)
