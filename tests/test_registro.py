"""Pruebas de validación de seguridad de contraseñas en el módulo de registro."""

from registro import password_segura


def test_password_segura_valida():
    """Contraseñas que cumplen todos los requisitos deben ser aceptadas."""
    assert password_segura("Abcdef1234!")
    assert password_segura("P@ssw0rd2024X")
    assert password_segura("C0mpl3j!simo")


def test_password_corta_rechazada():
    """Contraseñas con menos de 10 caracteres deben ser rechazadas."""
    assert not password_segura("Ab1!")
    assert not password_segura("aB1!")


def test_password_sin_mayuscula_rechazada():
    """Contraseñas sin mayúsculas deben ser rechazadas."""
    assert not password_segura("abcdef1234!")


def test_password_sin_minuscula_rechazada():
    """Contraseñas sin minúsculas deben ser rechazadas."""
    assert not password_segura("ABCDEF1234!")


def test_password_sin_numero_rechazada():
    """Contraseñas sin números deben ser rechazadas."""
    assert not password_segura("Abcdefgh!!")


def test_password_sin_especial_rechazada():
    """Contraseñas sin caracteres especiales deben ser rechazadas."""
    assert not password_segura("Abcdef1234")


def test_password_vacia_rechazada():
    """Una contraseña vacía debe ser rechazada."""
    assert not password_segura("")


def test_password_10_chars_valida():
    """Una contraseña de exactamente 10 caracteres que cumple todo debe ser aceptada."""
    assert password_segura("Abcdef1!90")
