"""
Módulo de inicialización del limitador de tasa (rate limiter) para la aplicación.

Este módulo configura una instancia de Flask-Limiter que impone límites de
peticiones por hora y por minuto a los clientes, usando la dirección IP remota
como identificador. Intenta conectarse a Redis como almacenamiento persistente;
si no está disponible, recurre a memoria local (no apta para producción
multi-worker).
"""

import os
import logging
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

logger = logging.getLogger(__name__)

# ── Conexión al almacenamiento (Redis o memoria local) ──────────────────────

REDIS_URL = os.getenv("REDIS_URL", "").strip()

if REDIS_URL:
    try:
        import redis
        r = redis.from_url(REDIS_URL, socket_connect_timeout=3)
        r.ping()
        storage_uri = REDIS_URL
        logger.info("Rate limiting configurado con Redis: %s", REDIS_URL)
    except Exception:
        # Redis inalcanzable — se usa memoria local como respaldo
        logger.warning(
            "Redis no disponible en %s. Usando memoria local como fallback. "
            "El rate limiting se reiniciará con cada deploy/worker.",
            REDIS_URL
        )
        storage_uri = "memory://"
else:
    storage_uri = "memory://"
    logger.warning(
        "REDIS_URL no configurada. Usando memoria local. "
        "Configura Redis en .env para rate limiting persistente en producción."
    )

# ── Instancia global del limitador ──────────────────────────────────────────

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["300 per hour", "30 per minute"],
    storage_uri=storage_uri,
)
