"""Pruebas del módulo CSRF - generación de tokens y rutas excluidas de validación."""

from csrf import generate_token, CSRF_SKIP_PATHS


def test_generate_token_returns_string(app):
    """El token CSRF generado debe ser una cadena de 64 caracteres."""
    with app.test_request_context():
        token = generate_token()
    assert isinstance(token, str)
    assert len(token) == 64


def test_generate_token_is_hex(app):
    """El token CSRF debe ser una cadena hexadecimal válida."""
    with app.test_request_context():
        token = generate_token()
    int(token, 16)


def test_csrf_skip_paths_defined():
    """Debe existir la lista de rutas que omiten validación CSRF."""
    assert isinstance(CSRF_SKIP_PATHS, list)
