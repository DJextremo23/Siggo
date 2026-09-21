"""Configuración de fixtures para pruebas - mock de base de datos, app Flask de prueba, cliente HTTP."""

import os
import sys
from unittest.mock import MagicMock

# Agregar el directorio raíz del proyecto al path para poder importar main
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Variables de entorno simuladas para pruebas
os.environ["SECRET_KEY"] = "test-secret-key-12345"
os.environ["DB_HOST"] = "localhost"
os.environ["DB_USER"] = "test"
os.environ["DB_PASSWORD"] = "test"
os.environ["DB_NAME"] = "test_db"
os.environ["FORCE_HTTPS"] = "false"

# Mock del cursor de base de datos
mock_cursor = MagicMock()
mock_cursor.fetchall.return_value = []
mock_cursor.fetchone.return_value = None
mock_cursor.lastrowid = 1

# Mock de la conexión de base de datos
mock_db = MagicMock()
mock_db.cursor.return_value = mock_cursor
mock_db.commit.return_value = None
mock_db.rollback.return_value = None

import main

# Reemplazar la conexión real por el mock
main.conexion = mock_db


import pytest
from main import app as flask_app


@pytest.fixture(autouse=True)
def disable_csrf(monkeypatch):
    """Deshabilita la validación CSRF para todas las pruebas."""
    monkeypatch.setattr("main.validate_csrf", lambda: None)


@pytest.fixture
def app():
    """Fixture que proporciona la app Flask en modo testing."""
    flask_app.config.update({
        "TESTING": True,
    })
    return flask_app


@pytest.fixture
def client(app):
    """Fixture que proporciona un cliente HTTP de prueba."""
    return app.test_client()


@pytest.fixture
def runner(app):
    """Fixture que proporciona un runner para comandos CLI de Flask."""
    return app.test_cli_runner()
