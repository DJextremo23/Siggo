"""Validadores de archivos (extensión, MIME real), sanitización de nombres, límites de longitud de campos."""

import re
import os

ALLOWED_EXTENSIONS = {"xlsx", "xlsm"}
MIME_MAP = {
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "jfif": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}


# Verifica que la extensión del archivo esté en ALLOWED_EXTENSIONS
def archivo_permitido(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


# Valida el MIME real del archivo inspeccionando sus bytes mágicos
def validar_mime_real(file_bytes, extension):
    extension = extension.lower()
    expected = MIME_MAP.get(extension)
    if not expected:
        return False

    magic = file_bytes[:12]

    if extension in ("xlsx", "xlsm"):
        return magic[:4] in (b"PK\x03\x04", b"PK\0\0")
    elif extension in ("png", "jpg", "jpeg", "jfif", "gif", "webp"):
        if extension == "png":
            return magic[:8] == b"\x89PNG\r\n\x1a\n"
        elif extension in ("jpg", "jpeg", "jfif"):
            return magic[:3] == b"\xff\xd8\xff"
        elif extension == "gif":
            return magic[:4] in (b"GIF8", b"GIF9")
        elif extension == "webp":
            return magic[:4] == b"RIFF" and magic[8:12] == b"WEBP"

    return False


# Sanitiza nombres de archivo: elimina caracteres peligrosos y limita longitud
def sanitizar_nombre(filename):
    nombre, extension = os.path.splitext(filename)
    nombre = re.sub(r"[^a-zA-Z0-9_\-]", "_", nombre)
    nombre = nombre[:200]
    extension = extension.lower()[:20]
    extension = re.sub(r"[^a-z.]", "", extension)
    return nombre + extension


# Valida que la contraseña cumpla con los requisitos mínimos de seguridad
def password_segura(password):
    return all([
        len(password) >= 10,
        bool(re.search(r"[A-Z]", password)),
        bool(re.search(r"[a-z]", password)),
        bool(re.search(r"[0-9]", password)),
        bool(re.search(r"[!@#$%^&*()_+\-=\[\]{};':\"\\|,.<>\/?]", password))
    ])


# Expresión regular para validar el formato básico de un correo electrónico
EMAIL_REGEX = re.compile(r"^[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


# Verifica que la cadena tenga un formato de correo electrónico válido
def correo_valido(correo):
    return bool(correo) and bool(EMAIL_REGEX.match(correo))


INPUT_LIMITS = {
    "nombre": 100,
    "apellidos": 100,
    "usuario": 50,
    "correo": 100,
    "password": 128,
    "titulo": 255,
    "descripcion": 2000,
    "observacion": 1000,
}


# Valida que los campos no excedan las longitudes máximas definidas en INPUT_LIMITS
def validar_longitudes(data):
    for campo, valor in data.items():
        if campo in INPUT_LIMITS and valor and isinstance(valor, str):
            if len(valor) > INPUT_LIMITS[campo]:
                return False, f"El campo '{campo}' excede la longitud máxima permitida ({INPUT_LIMITS[campo]})"
    return True, None
