"""
Módulo de inicialización del limitador de tasa (rate limiter) para la aplicación.

Configura una instancia de Flask-Limiter que impone límites de peticiones por
hora y por minuto. La clave de limitación es la cuenta (id_usuario) cuando hay
una sesión autenticada, o la IP remota en caso contrario. Esto evita que un
proxy con IP rotativa (Railway) invalide los límites de las rutas autenticadas.
"""

import os
from flask import session
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# ── Almacenamiento del limitador ──────────────────────────────────────────
# memory://  (por defecto): contadores en RAM del proceso. Suficiente para un
#             solo proceso (Waitress con threads).
# redis://   : contadores compartidos entre varios procesos/replicas.
#             Usar cuando el sistema corra con múltiples workers o réplicas:
#             RATE_LIMIT_STORAGE=redis://localhost:6379/0
_storage_uri = os.getenv("RATE_LIMIT_STORAGE", "memory://")


def _clave_rate_limit():
    """Clave de rate-limit: cuenta (id_usuario) si hay sesión, IP en caso contrario."""
    if session.get("id_usuario"):
        return f"usuario:{session['id_usuario']}"
    return get_remote_address()


# ── Instancia global del limitador ──────────────────────────────────────────

limiter = Limiter(
    key_func=_clave_rate_limit,
    default_limits=["300 per hour", "30 per minute"],
    storage_uri=_storage_uri,
)
