"""Utilidades compartidas para respuestas de error HTTP estandarizadas (403, 404, 400, 500)."""

from flask import render_template, redirect, request, session, url_for
from conexion import ConexionDB

# Conexión independiente para auditoría (no interfiere con la transacción de la ruta)
_auditoria_db = ConexionDB()


def registrar_auditoria(accion, detalle=None):
    """Registra una acción sensible en la tabla 'auditoria' (mejor esfuerzo).

    No lanza excepciones: si falla, la acción principal no se ve afectada.
    """
    try:
        cursor = _auditoria_db.cursor()
        cursor.execute(
            "INSERT INTO auditoria (id_usuario, usuario, accion, detalle, ip) "
            "VALUES (%s, %s, %s, %s, %s)",
            (
                session.get("id_usuario"),
                session.get("usuario"),
                accion,
                (detalle or "")[:500],
                (request.remote_addr or "")[:50],
            ),
        )
        _auditoria_db.commit()
        cursor.close()
    except Exception:
        try:
            _auditoria_db.rollback()
        except Exception:
            pass


def guardar_filtros(key):
    """Guarda en sesión la query string actual (filtros) o la limpia si no hay filtros."""
    if request.query_string:
        session[key] = request.query_string.decode("utf-8")
    else:
        session.pop(key, None)


def redirigir_con_filtros(endpoint, key, **values):
    """Redirige a un endpoint preservando los filtros guardados en sesión bajo `key`."""
    url = url_for(endpoint, **values)
    qs = session.get(key, "")
    if qs:
        url += ("&" if "?" in url else "?") + qs
    return redirect(url)


def error_response(mensaje, codigo=400, titulo="Error", volver_url="/", volver_texto="Volver"):
    return render_template(
        "error.html",
        mensaje=mensaje,
        codigo=f"Error {codigo}",
        titulo=titulo,
        volver_url=volver_url,
        volver_texto=volver_texto
    ), codigo


def acceso_no_autorizado():
    return error_response(
        "No tiene permisos para acceder a este recurso.",
        codigo=403,
        titulo="Acceso no autorizado"
    )


def no_encontrado(mensaje="El recurso solicitado no fue encontrado."):
    return error_response(
        mensaje,
        codigo=404,
        titulo="No encontrado"
    )


def error_interno():
    return error_response(
        "Error interno del servidor. Intente nuevamente.",
        codigo=500,
        titulo="Error del servidor"
    )


def datos_invalidos(mensaje="Datos inválidos o incompletos."):
    return error_response(
        mensaje,
        codigo=400,
        titulo="Datos inválidos"
    )
