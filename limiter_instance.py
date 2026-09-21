"""
Módulo de inicialización del limitador de tasa (rate limiter) para la aplicación.

Este módulo configura una instancia de Flask-Limiter que impone límites de
peticiones por hora y por minuto a los clientes, usando la dirección IP remota
como identificador y memoria local como almacenamiento.
"""

import os
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# ── Almacenamiento del limitador ──────────────────────────────────────────
# memory://  (por defecto): contadores en RAM del proceso. Suficiente para un
#             solo proceso (Waitress con threads).
# redis://   : contadores compartidos entre varios procesos/replicas.
#             Usar cuando el sistema corra con múltiples workers o réplicas:
#             RATE_LIMIT_STORAGE=redis://localhost:6379/0
_storage_uri = os.getenv("RATE_LIMIT_STORAGE", "memory://")

# ── Instancia global del limitador ──────────────────────────────────────────

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["300 per hour", "30 per minute"],
    storage_uri=_storage_uri,
)
