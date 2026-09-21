from flask import Blueprint, request, session
from datetime import datetime, date
from db import conexion

"""
Blueprint de notificaciones: marcar leídas y consultar nuevas.
Los endpoints devuelven JSON y son consumidos vía fetch por el frontend.
"""

notificaciones_bp = Blueprint("notificaciones", __name__)


# Marca una notificación como leída
@notificaciones_bp.route("/notificaciones/leer/<int:id_notificacion>", methods=["POST"])
def marcar_notificacion_leida(id_notificacion):
    if "usuario" not in session:
        return {"ok": False, "error": "No autorizado"}, 401

    cursor = conexion.cursor()
    try:
        cursor.execute("""
            UPDATE notificaciones
            SET leida = TRUE
            WHERE id_notificacion = %s
              AND id_usuario = (
                  SELECT id_usuario FROM usuarios WHERE usuario = %s
              )
        """, (id_notificacion, session["usuario"]))
        conexion.commit()
        return {"ok": True}
    except Exception as e:
        conexion.rollback()
        return {"ok": False, "error": str(e)}, 500
    finally:
        cursor.close()


# Marca todas las notificaciones del usuario como leídas
@notificaciones_bp.route("/notificaciones/leer_todas", methods=["POST"])
def marcar_todas_leidas():
    if "usuario" not in session:
        return {"ok": False, "error": "No autorizado"}, 401

    cursor = conexion.cursor()
    try:
        cursor.execute("""
            UPDATE notificaciones
            SET leida = TRUE
            WHERE id_usuario = (
                SELECT id_usuario FROM usuarios WHERE usuario = %s
            )
              AND leida = FALSE
        """, (session["usuario"],))
        conexion.commit()
        return {"ok": True}
    except Exception as e:
        conexion.rollback()
        return {"ok": False, "error": str(e)}, 500
    finally:
        cursor.close()


# Devuelve las notificaciones no leídas más recientes
@notificaciones_bp.route("/notificaciones/nuevas")
def notificaciones_nuevas():
    if "usuario" not in session:
        return {"ok": False, "error": "No autorizado"}, 401

    cursor = conexion.cursor(dictionary=True)
    try:
        cursor.execute("""
            SELECT id_notificacion, titulo, mensaje, fecha_creacion, leida
            FROM notificaciones
            WHERE id_usuario = (
                SELECT id_usuario FROM usuarios WHERE usuario = %s
            )
              AND leida = FALSE
            ORDER BY fecha_creacion DESC
            LIMIT 10
        """, (session["usuario"],))
        notifs = cursor.fetchall()

        for n in notifs:
            fc = n.get('fecha_creacion')
            if isinstance(fc, datetime):
                n['fecha_creacion_str'] = fc.strftime('%d/%m/%Y %H:%M')
            elif isinstance(fc, date):
                n['fecha_creacion_str'] = fc.strftime('%d/%m/%Y')
            else:
                n['fecha_creacion_str'] = str(fc) if fc else '—'

        return {"ok": True, "notificaciones": notifs}
    except Exception as e:
        return {"ok": False, "error": str(e)}, 500
    finally:
        cursor.close()
