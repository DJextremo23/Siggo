from flask import Blueprint, request, render_template, redirect, session, url_for, flash
from datetime import datetime, date
import mysql.connector.errors
from db import conexion
from constantes import DIAS_ES
from utils import (
    guardar_filtros,
    redirigir_con_filtros,
    error_interno,
    no_encontrado,
    datos_invalidos,
    acceso_no_autorizado,
)

"""
Blueprint de feriados: CRUD del panel admin y feriados propios del fiscalizador.
"""

feriados_bp = Blueprint("feriados", __name__)


# ==========================
# ADMIN — FERIADOS
# ==========================
# CRUD de feriados desde el panel admin
@feriados_bp.route("/feriados")
def feriados():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    guardar_filtros("filtro_feriados")

    anio = request.args.get("anio", "").strip()
    fecha_desde = request.args.get("fecha_desde", "").strip()
    fecha_hasta = request.args.get("fecha_hasta", "").strip()
    descripcion = request.args.get("descripcion", "").strip()

    cursor = conexion.cursor(dictionary=True)

    try:

        sql = "SELECT id_feriado, fecha, descripcion FROM feriados WHERE 1=1 "
        params = []

        if anio:
            sql += " AND YEAR(fecha) = %s "
            params.append(int(anio))

        if fecha_desde:
            sql += " AND fecha >= %s "
            params.append(fecha_desde)

        if fecha_hasta:
            sql += " AND fecha <= %s "
            params.append(fecha_hasta)

        if descripcion:
            sql += " AND descripcion LIKE %s "
            params.append(f"%{descripcion}%")

        sql += " ORDER BY fecha DESC"

        cursor.execute(sql, params)

        data = cursor.fetchall()

        for f in data:
            fecha = f.get('fecha')
            if isinstance(fecha, date):
                f['dia_semana'] = DIAS_ES[fecha.weekday()]
            else:
                f['dia_semana'] = str(fecha) if fecha else '—'

        return render_template(
            "feriados.html",
            feriados=data
        )

    finally:

        cursor.close()


@feriados_bp.route("/guardar_feriado", methods=["POST"])
def guardar_feriado():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    fecha = request.form.get("fecha", "").strip()
    descripcion = request.form.get("descripcion", "").strip()

    if not fecha or not descripcion:
        flash("Fecha y descripción son obligatorios", "error")
        return redirigir_con_filtros("feriados.feriados", "filtro_feriados")

    try:
        datetime.strptime(fecha, "%Y-%m-%d")
    except ValueError:
        flash("Formato de fecha inválido. Use YYYY-MM-DD", "error")
        return redirigir_con_filtros("feriados.feriados", "filtro_feriados")

    cursor = conexion.cursor()

    try:

        cursor.execute("""
            INSERT INTO feriados (
                fecha,
                descripcion
            )
            VALUES (%s, %s)
        """, (
            fecha,
            descripcion
        ))

        id_feriado = cursor.lastrowid

        cursor.execute("""
            UPDATE guardias
            SET id_feriado = %s
            WHERE fecha_guardia = %s
        """, (id_feriado, fecha))

        conexion.commit()

        flash("Feriado registrado correctamente", "success")
        return redirigir_con_filtros("feriados.feriados", "filtro_feriados")

    except mysql.connector.errors.IntegrityError as e:
        conexion.rollback()
        if e.errno == 1062:
            flash("Ya existe un feriado registrado en esa fecha", "error")
        else:
            flash("No se pudo registrar el feriado: datos duplicados o inválidos", "error")
        return redirigir_con_filtros("feriados.feriados", "filtro_feriados")

    except mysql.connector.errors.OperationalError as e:
        conexion.rollback()
        if e.errno == 1205:
            flash("La base de datos está ocupada, intente nuevamente en unos segundos", "error")
        else:
            flash("Error de conexión con la base de datos, intente nuevamente", "error")
        return redirigir_con_filtros("feriados.feriados", "filtro_feriados")

    except Exception as e:

        conexion.rollback()

        print("ERROR GUARDAR FERIADO:", e)

        flash("Ocurrió un error al registrar el feriado. Intente nuevamente.", "error")
        return redirigir_con_filtros("feriados.feriados", "filtro_feriados")

    finally:

        cursor.close()


@feriados_bp.route("/editar_feriado/<int:id>")
def editar_feriado(id):

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    cursor = conexion.cursor(dictionary=True)

    try:

        cursor.execute("""
            SELECT id_feriado, fecha, descripcion
            FROM feriados
            WHERE id_feriado = %s
        """, (id,))

        data = cursor.fetchone()

        if not data:
            return no_encontrado("Feriado no encontrado")

        return render_template(
            "editar_feriado.html",
            data=data
        )

    finally:

        cursor.close()


@feriados_bp.route("/actualizar_feriado/<int:id>", methods=["POST"])
def actualizar_feriado(id):

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    fecha = request.form.get("fecha", "").strip()
    descripcion = request.form.get("descripcion", "").strip()

    if not fecha or not descripcion:
        flash("Fecha y descripción son obligatorios", "error")
        return redirigir_con_filtros("feriados.feriados", "filtro_feriados")

    try:
        datetime.strptime(fecha, "%Y-%m-%d")
    except ValueError:
        flash("Formato de fecha inválido. Use YYYY-MM-DD", "error")
        return redirigir_con_filtros("feriados.feriados", "filtro_feriados")

    cursor = conexion.cursor()

    try:
        cursor.execute("""
            UPDATE feriados
            SET fecha = %s, descripcion = %s
            WHERE id_feriado = %s
        """, (fecha, descripcion, id))

        cursor.execute("""
            UPDATE guardias
            SET id_feriado = NULL
            WHERE id_feriado = %s
        """, (id,))

        cursor.execute("""
            UPDATE guardias
            SET id_feriado = %s
            WHERE fecha_guardia = %s
        """, (id, fecha))

        conexion.commit()

        flash("Feriado actualizado correctamente", "success")
        return redirigir_con_filtros("feriados.feriados", "filtro_feriados")

    except mysql.connector.errors.IntegrityError as e:
        conexion.rollback()
        if e.errno == 1062:
            flash("Ya existe un feriado registrado en esa fecha", "error")
        else:
            flash("No se pudo actualizar el feriado: datos duplicados o inválidos", "error")
        return redirigir_con_filtros("feriados.feriados", "filtro_feriados")

    except Exception as e:
        conexion.rollback()
        print("ERROR ACTUALIZAR FERIADO:", e)
        return error_interno()

    finally:
        cursor.close()


@feriados_bp.route("/eliminar_feriado/<int:id>", methods=["POST"])
def eliminar_feriado(id):

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    cursor = conexion.cursor()

    try:

        cursor.execute("""
            DELETE FROM feriados
            WHERE id_feriado = %s
        """, (id,))

        conexion.commit()

        flash("Feriado eliminado correctamente", "success")
        return redirigir_con_filtros("feriados.feriados", "filtro_feriados")

    except Exception as e:

        conexion.rollback()

        print("ERROR ELIMINAR FERIADO:", e)

        return error_interno()

    finally:

        cursor.close()


@feriados_bp.route("/eliminar_feriados", methods=["POST"])
def eliminar_feriados():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    ids = request.form.getlist("ids_feriado")
    if not ids:
        flash("No seleccionó ningún feriado", "error")
        return redirigir_con_filtros("feriados.feriados", "filtro_feriados")

    ids_int = []
    for valor in ids:
        try:
            ids_int.append(int(valor))
        except (TypeError, ValueError):
            return datos_invalidos("Identificador de feriado inválido")

    cursor = conexion.cursor()

    try:
        placeholders = ",".join(["%s"] * len(ids_int))
        cursor.execute(
            f"DELETE FROM feriados WHERE id_feriado IN ({placeholders})",
            tuple(ids_int),
        )
        conexion.commit()
        flash(f"Se eliminaron {cursor.rowcount} feriados correctamente", "success")
    except Exception as e:
        conexion.rollback()
        print("ERROR ELIMINAR FERIADOS:", e)
        flash("Ocurrió un error al eliminar los feriados", "error")
    finally:
        cursor.close()

    return redirigir_con_filtros("feriados.feriados", "filtro_feriados")


# ==========================
# FISCALIZADOR — MIS FERIADOS
# ==========================
# Feriados en los que el fiscalizador tiene guardia asignada
@feriados_bp.route("/mis_feriados")
def mis_feriados():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo", "").lower() != "fiscalizador":
        return acceso_no_autorizado()

    anio = request.args.get("anio", "").strip()
    fecha_desde = request.args.get("fecha_desde", "").strip()
    fecha_hasta = request.args.get("fecha_hasta", "").strip()
    descripcion = request.args.get("descripcion", "").strip()

    cursor = conexion.cursor(dictionary=True)

    try:

        sql = """
            SELECT DISTINCT f.id_feriado, f.fecha, f.descripcion
            FROM feriados f
            INNER JOIN guardias g ON f.id_feriado = g.id_feriado
            WHERE g.id_usuario = %s
        """
        params = [session["id_usuario"]]

        if anio:
            try:
                anio_int = int(anio)
            except (TypeError, ValueError):
                anio_int = None
            if anio_int:
                sql += " AND YEAR(f.fecha) = %s"
                params.append(anio_int)

        if fecha_desde:
            sql += " AND f.fecha >= %s"
            params.append(fecha_desde)

        if fecha_hasta:
            sql += " AND f.fecha <= %s"
            params.append(fecha_hasta)

        if descripcion:
            sql += " AND f.descripcion LIKE %s"
            params.append(f"%{descripcion}%")

        sql += " ORDER BY f.fecha DESC"

        cursor.execute(sql, params)
        feriados = cursor.fetchall()

        for f in feriados:
            if f.get("fecha"):
                f["dia"] = DIAS_ES[f["fecha"].weekday()]

        return render_template(
            "mis_feriados.html",
            feriados=feriados
        )

    finally:

        cursor.close()
