"""Protección CSRF para formularios Flask.

Genera y valida tokens CSRF en peticiones que modifican estado,
protegiendo contra ataques de falsificación de solicitudes entre sitios.
"""

# --- Importaciones del módulo CSRF ---
import secrets
from flask import session, request, abort

# Rutas exentas de validación CSRF (webhooks, APIs externas, etc.)
CSRF_SKIP_PATHS = [
    "/notificaciones/",
    "/api",
    "/analizar_informe/",
]


def generate_token():
    """Genera y guarda un token CSRF en la sesion si no existe."""
    if "csrf_token" not in session:
        session["csrf_token"] = secrets.token_hex(32)
    return session["csrf_token"]


def validate_csrf():
    """Valida CSRF en peticiones POST/PUT/DELETE/PATCH."""
    # Solo validar peticiones que modifican estado
    if request.method not in ("POST", "PUT", "DELETE", "PATCH"):
        return

    # Permitir rutas exentas sin validación de token
    for path in CSRF_SKIP_PATHS:
        if request.path.startswith(path):
            return

    # Extraer token CSRF del campo de formulario
    token = request.form.get("csrf_token")

    # Si no se encuentra, intentar desde el cuerpo JSON
    if not token and request.is_json:
        data = request.get_json(silent=True) or {}
        token = data.get("csrf_token")

    # Último recurso: buscar el token en encabezados HTTP
    if not token:
        token = request.headers.get("X-CSRFToken")

    # Comparar de forma segura y abortar si no coinciden
    if not token or not secrets.compare_digest(token, session.get("csrf_token", "")):
        abort(403)
