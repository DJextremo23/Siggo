"""
Módulo de conexión a base de datos MySQL con reconexión automática y soporte multihilo.

Proporciona la clase ConexionDB que gestiona conexiones persistentes a MySQL,
reintentando automáticamente ante fallos y usando almacenamiento thread-local para
garantizar una conexión independiente por cada hilo de ejecución.
"""

import os
import time
import threading
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

# Configuración de la base de datos obtenida desde variables de entorno.
# Soporta las variables propias (DB_*) y las que inyecta Railway (MYSQL*).
DB_CONFIG = {
    "host": os.getenv("DB_HOST") or os.getenv("MYSQLHOST") or "localhost",
    "user": os.getenv("DB_USER") or os.getenv("MYSQLUSER") or "",
    "password": os.getenv("DB_PASSWORD") or os.getenv("MYSQLPASSWORD") or "",
    "database": os.getenv("DB_NAME") or os.getenv("MYSQLDATABASE") or "guardiaoig",
    "port": int(os.getenv("DB_PORT") or os.getenv("MYSQLPORT") or "3306"),
    "connection_timeout": 10,
    "time_zone": os.getenv("DB_TIMEZONE", "-05:00"),
}

_singleton = None
_singleton_lock = threading.Lock()


def conexion():
    """Devuelve la conexión global reutilizada entre peticiones (por hilo).

    Evita abrir una conexión MySQL nueva en cada request, que es costoso cuando
    la base de datos es remota (Railway). La conexión física se conserva por
    hilo y se revalida con ping en cada uso."""
    global _singleton
    if _singleton is None:
        with _singleton_lock:
            if _singleton is None:
                _singleton = ConexionDB()
    return _singleton

class ConexionDB:
    """Gestor de conexión MySQL con reconexión automática y soporte multihilo."""

    def __init__(self, **kwargs):
        self._config = {**DB_CONFIG, **kwargs}
        self._local = threading.local()  # Almacenamiento thread-local para una conexión por hilo

    def _ensure_connected(self):
        """Verifica la conexión y la restablece si es necesario.

        Si la conexión se usó recientemente (< 60 s), se asume viva y se salta
        el ping (que a un MySQL remoto cuesta ~1 s de ida y vuelta)."""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = self._conectar_con_reintentos()
            self._local.last_used = time.time()
            return
        if time.time() - getattr(self._local, 'last_used', 0) < 60:
            return
        try:
            self._local.conn.ping(reconnect=True, attempts=1, delay=1)
        except Exception:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = self._conectar_con_reintentos()
        self._local.last_used = time.time()

    def _conectar_con_reintentos(self, max_intentos=2, espera=1):
        """Intenta conectarse a MySQL reintentando hasta max_intentos veces."""
        ultimo_error = None
        for intento in range(1, max_intentos + 1):
            try:
                return mysql.connector.connect(**self._config)
            except mysql.connector.errors.Error as e:
                ultimo_error = e
                if intento < max_intentos:
                    time.sleep(espera * intento)
        raise ultimo_error

    def cursor(self, **kwargs):
        """Devuelve un cursor de base de datos asegurando la conexión activa."""
        self._ensure_connected()
        self._local.last_used = time.time()
        return self._local.conn.cursor(**kwargs)

    def commit(self):
        if hasattr(self._local, 'conn') and self._local.conn:
            self._local.conn.commit()

    def rollback(self):
        if hasattr(self._local, 'conn') and self._local.conn:
            self._local.conn.rollback()

    def close(self):
        """Finaliza la transacción actual y conserva la conexión para reutilizarla.

        No cierra la conexión física: se reutiliza por hilo entre peticiones para
        evitar reconectar a un MySQL remoto en cada request."""
        try:
            self.rollback()
        except Exception:
            pass
