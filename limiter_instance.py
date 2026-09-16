"""
Módulo de inicialización del limitador de tasa (rate limiter) para la aplicación.

Este módulo configura una instancia de Flask-Limiter que impone límites de
peticiones por hora y por minuto a los clientes, usando la dirección IP remota
como identificador y memoria local como almacenamiento.
"""

from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# ── Instancia global del limitador ──────────────────────────────────────────

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["300 per hour", "30 per minute"],
    storage_uri="memory://",
)
