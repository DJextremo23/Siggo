"""Singleton de conexión a base de datos compartido por toda la aplicación.

ConexionDB es thread-local: cada hilo obtiene su propia conexión, reutilizada
entre peticiones del mismo hilo. El teardown_request en main.py hace rollback
para evitar lecturas obsoletas por el aislamiento REPEATABLE READ de MySQL.
"""

from conexion import ConexionDB

conexion = ConexionDB()
