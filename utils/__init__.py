"""Utilidades compartidas para respuestas de error HTTP estandarizadas (403, 404, 400, 500)."""

from flask import render_template


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
