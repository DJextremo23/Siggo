import os
from flask import Flask, render_template, request, redirect
from flask_talisman import Talisman
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv
from limiter_instance import limiter
from csrf import generate_token, validate_csrf

from datetime import datetime, timedelta, date
import mysql.connector.errors

from db import conexion

from login import login_bp
from registro import registro_bp
from mis_informes import informe_bp
from informes import informes_bp
from fiscalizadores import fiscalizadores_bp
from perfil import perfil_bp
from mis_reportes import mis_reportes_bp
from reporte import reporte_bp
from dashboard import dashboard_bp
from compensaciones import compensaciones_bp
from feriados import feriados_bp
from vacaciones import vacaciones_bp
from guardias import guardias_bp
from notificaciones import notificaciones_bp

"""
Aplicación principal de SIGGO.

Este módulo contiene únicamente:
  - Configuración de la aplicación Flask (clave secreta, seguridad HTTPS/CSRF, rate limiting).
  - Filtros y manejadores globales (formato de fecha, no-cache, teardown de BD, errores).
  - Registro de los blueprints modulares.
  - Health check y arranque vía Waitress en producción.

Las rutas de negocio viven en módulos dedicados (dashboard, compensaciones, feriados,
vacaciones, guardias, notificaciones, login, registro, perfil, informes, reportes, etc.).
"""

load_dotenv()

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

app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=4)


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


@app.after_request
def no_cache(resp):
    """Evita que el navegador almacene páginas en caché y muestre datos desactualizados."""
    if resp.mimetype == "text/html":
        resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
    return resp


# -----------------------------------------------
# Conexión a la base de datos (singleton compartido)
# -----------------------------------------------

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
# RUTA DE INICIO
# ==========================

@app.route("/")
def home():
    return render_template("login.html")


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
app.register_blueprint(dashboard_bp)
app.register_blueprint(compensaciones_bp)
app.register_blueprint(feriados_bp)
app.register_blueprint(vacaciones_bp)
app.register_blueprint(guardias_bp)
app.register_blueprint(notificaciones_bp)

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
    port = int(os.getenv("PORT", 8080))
    host = "0.0.0.0"
    threads = int(os.getenv("WAITRESS_THREADS", 8))
    print(f"\n{'='*60}")
    print("  SIGGO - Guardia OIG")
    print(f"  http://{host}:{port}")
    print(f"{'='*60}\n")
    serve(app, host=host, port=port, threads=threads)
