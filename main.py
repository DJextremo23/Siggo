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
from conexion import conexion as obtener_conexion
from mis_reportes import mis_reportes_bp
from csrf import generate_token, validate_csrf
from utils import error_response, acceso_no_autorizado, error_interno, datos_invalidos, no_encontrado, guardar_filtros, redirigir_con_filtros, registrar_auditoria
from helpers import DIAS_ES, MESES_ABREV, balance_vacaciones_fifo

from datetime import datetime, timedelta, date
from login import login_bp
from registro import registro_bp
from mis_informes import informe_bp
from informes import informes_bp
from fiscalizadores import fiscalizadores_bp
from perfil import perfil_bp
from feriados import registrar_rutas as registrar_rutas_feriados
from compensaciones import registrar_rutas as registrar_rutas_compensaciones
from vacaciones import registrar_rutas as registrar_rutas_vacaciones
from guardias import registrar_rutas as registrar_rutas_guardias
from asistencia import registrar_rutas as registrar_rutas_asistencia
import mysql.connector.errors

"""
Aplicación principal de SIGGO.

Este módulo contiene la configuración central de Flask y las rutas que no
pertenecen a un dominio específico:

  - Configuración de seguridad (clave de sesión, HTTPS/CSRF, rate limiting,
    cabeceras de seguridad, timeouts de sesión).
  - Dashboard de administrador y panel de inicio del fiscalizador.
  - Endpoints de notificaciones (marcar leídas, obtener nuevas).
  - Health check, manejadores de errores globales y arranque vía Waitress.

Los CRUD de negocio viven en módulos separados (feriados, compensaciones,
vacaciones, guardias, asistencia) y se registran mediante registrar_rutas(app).
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

# El modo depuración debe permanecer desactivado SIEMPRE en producción
# (evita exponer trazas/stack traces y el depurador interactivo de Werkzeug).
app.config["DEBUG"] = False
app.config["TESTING"] = False
# No propagar excepciones ni mostrar información interna en respuestas de error
app.config["PROPAGATE_EXCEPTIONS"] = False

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

# Cookie de sesión con nombre propio (reduce huella del framework y evita colisiones con otras apps)
app.config["SESSION_COOKIE_NAME"] = "siggo_session"
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"
# Path estricto y sin dominio explícito para limitar el alcance de la cookie
app.config["SESSION_COOKIE_PATH"] = "/"


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
    strict_transport_security_preload=True,
    frame_options="DENY",
    referrer_policy="strict-origin-when-cross-origin",
    x_content_type_options=True,
    permissions_policy={
        "camera": "()",
        "microphone": "()",
        "geolocation": "()",
        "interest-cohort": "()",
        "payment": "()",
        "usb": "()",
        "accelerometer": "()",
        "gyroscope": "()",
        "browsing-topics": "()",
    },
    content_security_policy={
        "default-src": ["'self'", "blob:"],
        "script-src": ["'self'", "https://cdn.jsdelivr.net", "https://cdnjs.cloudflare.com"],
        "style-src": ["'self'", "https://cdn.jsdelivr.net", "https://cdnjs.cloudflare.com", "https://fonts.googleapis.com"],
        "style-src-attr": ["'unsafe-inline'"],
        "img-src": ["'self'", "data:", "blob:"],
        "font-src": ["'self'", "https://fonts.gstatic.com", "https://cdnjs.cloudflare.com"],
        "connect-src": ["'self'"],
        "frame-ancestors": ["'none'"],
        "base-uri": ["'self'"],
        "form-action": ["'self'"],
        "object-src": ["'none'"],
        "frame-src": ["'none'"],
        "upgrade-insecure-requests": [],
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


# Validación del Host: evita ataques de "Host header poisoning" (envenenamiento
# de enlaces de restablecimiento, cache poisoning). Solo se activa si se define
# ALLOWED_HOSTS (lista separada por comas); por defecto queda desactivado para
# no romper despliegues detrás de proxies (Railway/nginx) sin dominio fijo.
_ALLOWED_HOSTS = os.getenv("ALLOWED_HOSTS", "").strip()


@app.before_request
def validar_host():
    if not _ALLOWED_HOSTS:
        return
    host = request.host.split(":")[0]
    permitidos = [h.strip().lower() for h in _ALLOWED_HOSTS.split(",") if h.strip()]
    if host.lower() not in permitidos:
        return error_response(
            "Host no permitido.",
            codigo=400,
            titulo="Solicitud inválida"
        )


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


@app.before_request
def verificar_sesion_revocada():
    """Invalida la sesión si fue revocada (cambio de contraseña o cierre global).

    Compara el session_version guardado en la cookie con el valor actual en la
    BD. Si difieren, la sesión ya no es válida y se cierra la sesión.
    """
    if "usuario" not in session:
        return
    if request.endpoint == "static":
        return
    id_usuario = session.get("id_usuario")
    if not id_usuario:
        return

    try:
        cursor = conexion.cursor(dictionary=True)
        try:
            cursor.execute(
                "SELECT session_version FROM usuarios WHERE id_usuario = %s",
                (id_usuario,)
            )
            row = cursor.fetchone()
            if not row or row["session_version"] != session.get("session_version"):
                session.clear()
                return redirect(url_for("login.login"))
        finally:
            cursor.close()
    except Exception:
        pass


@app.after_request
def no_cache(resp):
    """Evita que el navegador almacene en caché páginas y respuestas de API con datos sensibles."""
    if resp.mimetype in ("text/html", "application/json"):
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


@app.after_request
def cabeceras_seguridad(resp):
    """Cabeceras defensivas adicionales (defensa en profundidad).

    - Permissions-Policy: deshabilita APIs sensibles del navegador que la app no usa.
    - X-Permitted-Cross-Domain-Policies: bloquea archivos de política de dominio.
    - Cross-Origin-Resource-Policy: impide que otros orígenes incrusten respuestas.
    - Cross-Origin-Opener-Policy: aisla el contexto de navegación (mitiga side-channels).
    - X-XSS-Protection: desactivado; la CSP ya mitiga XSS y este filtro es obsoleto/abusable.
    """
    resp.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
    resp.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    resp.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    resp.headers.setdefault("X-XSS-Protection", "0")
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
# Conexión a la base de datos (singleton compartido entre módulos)
# -----------------------------------------------

conexion = obtener_conexion()


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
                balance_vacaciones_fifo(a["anio_ingreso"], a["dias_tomados_total"])

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

        # Resumen operativo: guardias (solo tipo 'guardia') agrupadas por año y mes
        cursor.execute("""
            SELECT YEAR(fecha_guardia) AS anio, MONTH(fecha_guardia) AS mes, COUNT(*) AS total
            FROM guardias
            WHERE tipo = 'guardia'
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

        # Obtener datos desde la vista correcta
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
                balance_vacaciones_fifo(a["anio_ingreso"], a["dias_tomados_total"])

        # ESTADO DE GUARDIAS: distribución guardia / soporte del año en curso
        estado_guardias = {
            "guardia": sum(1 for d in datos if d.get("tipo") == "guardia"),
            "soporte": sum(1 for d in datos if d.get("tipo") == "soporte"),
        }

        # PRÓXIMAS GUARDIAS: turnos programados a futuro del fiscalizador
        cursor.execute("""
            SELECT g.id_guardia, g.fecha_guardia, g.tipo, f.descripcion AS feriado
            FROM guardias g
            LEFT JOIN feriados f ON g.fecha_guardia = f.fecha
            WHERE g.id_usuario = (
                SELECT id_usuario FROM usuarios WHERE usuario = %s
            )
              AND g.fecha_guardia >= CURDATE()
            ORDER BY g.fecha_guardia ASC
            LIMIT 5
        """, (session["usuario"],))
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

        return render_template(
            "fiscalizador.html",
            datos=datos,
            notificaciones=notificaciones,
            notif_no_leidas=notif_no_leidas,
            alertas=alertas,
            estado_guardias=estado_guardias,
            proximas_guardias=proximas_guardias
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
@limiter.limit("10 per minute")
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
@limiter.limit("10 per minute")
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
        registrar_auditoria("notificaciones_leidas", "Marcadas todas las notificaciones como leídas")
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

# Registro de rutas extraídas a módulos (mismos endpoints, url_for intactos)
registrar_rutas_feriados(app)
registrar_rutas_compensaciones(app)
registrar_rutas_vacaciones(app)
registrar_rutas_guardias(app)
registrar_rutas_asistencia(app)

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

@app.errorhandler(404)
def handle_404(error):
    return render_template(
        "error.html",
        codigo="Error 404",
        titulo="No encontrado",
        mensaje="La página o recurso solicitado no existe.",
        volver_url="/",
        volver_texto="Volver al inicio"
    ), 404

@app.errorhandler(405)
def handle_405(error):
    return render_template(
        "error.html",
        codigo="Error 405",
        titulo="Método no permitido",
        mensaje="El método de la solicitud no está permitido para este recurso.",
        volver_url="/",
        volver_texto="Volver al inicio"
    ), 405

@app.errorhandler(400)
def handle_400(error):
    return render_template(
        "error.html",
        codigo="Error 400",
        titulo="Solicitud inválida",
        mensaje="La solicitud no pudo ser procesada.",
        volver_url="/",
        volver_texto="Volver al inicio"
    ), 400

@app.errorhandler(429)
def handle_429(error):
    return render_template(
        "error.html",
        codigo="Error 429",
        titulo="Demasiadas solicitudes",
        mensaje="Ha realizado demasiadas solicitudes. Espere un momento e intente nuevamente.",
        volver_url="/",
        volver_texto="Volver al inicio"
    ), 429

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
