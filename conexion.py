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

# Configuración de la base de datos obtenida desde variables de entorno
DB_CONFIG = {
    "host": os.getenv("DB_HOST", "localhost"),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", "admin"),
    "database": os.getenv("DB_NAME", "guardiaoig"),
    "port": int(os.getenv("DB_PORT", "8090")),
    "connection_timeout": 10,
}

def conexion():
    return ConexionDB()

class ConexionDB:
    """Gestor de conexión MySQL con reconexión automática y soporte multihilo."""

    def __init__(self, **kwargs):
        self._config = {**DB_CONFIG, **kwargs}
        self._local = threading.local()  # Almacenamiento thread-local para una conexión por hilo

    def _ensure_connected(self):
        """Verifica que la conexión esté activa; si no, la restablece."""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = self._conectar_con_reintentos()
        else:
            try:
                self._local.conn.ping(reconnect=True, attempts=1, delay=1)
            except Exception:
                try:
                    self._local.conn.close()
                except Exception:
                    pass
                self._local.conn = self._conectar_con_reintentos()

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
        return self._local.conn.cursor(**kwargs)

    def commit(self):
        if hasattr(self._local, 'conn') and self._local.conn:
            self._local.conn.commit()

    def rollback(self):
        if hasattr(self._local, 'conn') and self._local.conn:
            self._local.conn.rollback()

    def close(self):
        """Cierra la conexión activa del hilo actual de forma segura."""
        if hasattr(self._local, 'conn') and self._local.conn:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None

    def ping(self):
        """Comprueba si la conexión a la base de datos está activa."""
        try:
            self._ensure_connected()
            return True
        except Exception:
            return False
