"""Validadores de archivos (extensión, MIME real), sanitización de nombres, límites de longitud de campos."""

import re
import os

MAX_FILENAME_LENGTH = 255
ALLOWED_EXTENSIONS = {"pdf", "xlsx", "xlsm", "docx"}
ALLOWED_PHOTO_EXTENSIONS = {"png", "jpg", "jpeg", "gif", "webp"}
MIME_MAP = {
    "pdf": "application/pdf",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
}


# Verifica que la extensión del archivo esté en ALLOWED_EXTENSIONS
def archivo_permitido(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS
    )


# Verifica que la extensión de la imagen esté en ALLOWED_PHOTO_EXTENSIONS
def foto_permitida(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in ALLOWED_PHOTO_EXTENSIONS
    )


# Valida el MIME real del archivo inspeccionando sus bytes mágicos
def validar_mime_real(file_bytes, extension):
    extension = extension.lower()
    expected = MIME_MAP.get(extension)
    if not expected:
        return False

    magic = file_bytes[:12]

    if extension == "pdf":
        return magic[:4] == b"%PDF"
    elif extension in ("xlsx", "xlsm", "docx"):
        return magic[:4] in (b"PK\x03\x04", b"PK\0\0")
    elif extension == "xlsm":
        return magic[:4] in (b"PK\x03\x04", b"PK\0\0")
    elif extension == "docx":
        return magic[:4] in (b"PK\x03\x04", b"PK\0\0")
    elif extension in ("png", "jpg", "jpeg", "gif", "webp"):
        if extension == "png":
            return magic[:8] == b"\x89PNG\r\n\x1a\n"
        elif extension == "jpg" or extension == "jpeg":
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


INPUT_LIMITS = {
    "nombre": 100,
    "apellidos": 100,
    "usuario": 50,
    "correo": 150,
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
