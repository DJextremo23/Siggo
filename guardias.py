import os
from flask import Blueprint, request, render_template, redirect, session, url_for, flash
from datetime import datetime, date, timedelta
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
Blueprint de guardias y asistencia: CRUD de guardias y asistencia del panel admin,
además de las vistas propias del fiscalizador (mis guardias y mis asistencias).
"""

guardias_bp = Blueprint("guardias", __name__)

UPLOAD_FOLDER = "uploads"


# ── Limpieza de archivos físicos de informes al eliminar guardias ──
def _eliminar_archivos_informes(rutas):
    """Elimina del disco los archivos de informes (protección contra path traversal)."""
    uploads_real = os.path.realpath(UPLOAD_FOLDER)
    for ruta in rutas:
        if not ruta:
            continue
        ruta_real = os.path.realpath(ruta)
        if ruta_real != uploads_real and not ruta_real.startswith(uploads_real + os.sep):
            continue
        try:
            if os.path.isfile(ruta_real):
                os.remove(ruta_real)
        except OSError:
            pass


def _eliminar_guardias_con_archivos(cursor, ids_guardia):
    """Elimina guardias y los archivos físicos de sus informes asociados."""
    placeholders = ",".join(["%s"] * len(ids_guardia))
    cursor.execute(
        f"SELECT ruta_archivo FROM informes WHERE id_guardia IN ({placeholders})",
        tuple(ids_guardia),
    )
    rutas = [fila[0] for fila in cursor.fetchall() if fila[0]]
    cursor.execute(
        f"DELETE FROM guardias WHERE id_guardia IN ({placeholders})",
        tuple(ids_guardia),
    )
    _eliminar_archivos_informes(rutas)


# ==========================
# ADMIN — GUARDIAS
# ==========================
# Listado, creación, edición y eliminación de guardias
@guardias_bp.route("/guardias")
def ver_guardias():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    guardar_filtros("filtro_guardias")

    cursor = conexion.cursor(dictionary=True)

    id_usuario = request.args.get("id_usuario", "").strip()
    fecha_desde = request.args.get("fecha_desde", "").strip()
    fecha_hasta = request.args.get("fecha_hasta", "").strip()
    asistencia = request.args.get("asistencia", "").strip()

    # =========================
    # 1. GUARDIAS
    # =========================
    sql = "SELECT rg.*, u.foto FROM resumen_guardias rg LEFT JOIN usuarios u ON rg.id_usuario = u.id_usuario WHERE 1=1 "
    params = []

    if id_usuario:
        sql += " AND rg.id_usuario = %s "
        params.append(id_usuario)

    if fecha_desde:
        sql += " AND rg.fecha_guardia >= %s "
        params.append(fecha_desde)

    if fecha_hasta:
        sql += " AND rg.fecha_guardia <= %s "
        params.append(fecha_hasta)

    if asistencia:
        if asistencia == "pendiente":
            sql += " AND (rg.asistencia IS NULL OR rg.asistencia = '' OR rg.asistencia = 'pendiente' OR rg.asistencia NOT IN ('asistio','falta','justificado')) "
        else:
            sql += " AND rg.asistencia = %s "
            params.append(asistencia)

    sql += " ORDER BY rg.fecha_guardia DESC"

    cursor.execute(sql, tuple(params))
    guardias = cursor.fetchall()

    for g in guardias:
        fecha = g.get('fecha_guardia')
        if isinstance(fecha, date):
            g['dia_semana'] = DIAS_ES[fecha.weekday()]
        else:
            g['dia_semana'] = str(fecha) if fecha else '—'

    # =========================
    # 2. FISCALIZADORES
    # =========================
    cursor.execute("""
        SELECT DISTINCT
            u.id_usuario,
            u.nombre,
            u.apellidos
        FROM usuarios u
        INNER JOIN usuarios_roles ur
            ON u.id_usuario = ur.id_usuario
        INNER JOIN roles r
            ON ur.id_rol = r.id_rol
        WHERE r.nombre_rol = 'fiscalizador'
          AND u.estado = 'activo'
        ORDER BY u.nombre
    """)

    fiscalizadores = cursor.fetchall()

    cursor.close()

    return render_template(
        "guardias.html",
        guardias=guardias,
        fiscalizadores=fiscalizadores
    )


# ADMIN — Agregar nueva guardia (con verificación de duplicados)
@guardias_bp.route("/agregar_guardia", methods=["POST"])
def agregar_guardia():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    id_usuario = request.form.get("id_usuario", "").strip()
    fecha_guardia = request.form.get("fecha_guardia", "").strip()

    if not id_usuario or not fecha_guardia:
        flash("Todos los campos son obligatorios", "error")
        return redirigir_con_filtros("guardias.ver_guardias", "filtro_guardias")

    try:
        datetime.strptime(fecha_guardia, "%Y-%m-%d")
    except ValueError:
        flash("Formato de fecha inválido. Use YYYY-MM-DD", "error")
        return redirigir_con_filtros("guardias.ver_guardias", "filtro_guardias")

    cursor = conexion.cursor()

    try:
        cursor.execute("SELECT estado FROM usuarios WHERE id_usuario = %s", (id_usuario,))
        usuario = cursor.fetchone()
        if not usuario:
            flash("Usuario no encontrado", "error")
            return redirigir_con_filtros("guardias.ver_guardias", "filtro_guardias")
        if usuario[0] != "activo":
            flash("No se puede registrar una guardia para un usuario inactivo", "error")
            return redirigir_con_filtros("guardias.ver_guardias", "filtro_guardias")

        cursor.execute("""
            SELECT 1 FROM guardias
            WHERE id_usuario = %s AND fecha_guardia = %s
            LIMIT 1
        """, (id_usuario, fecha_guardia))

        if cursor.fetchone():
            flash("Este fiscalizador ya tiene una guardia asignada en esa fecha", "error")
            return redirigir_con_filtros("guardias.ver_guardias", "filtro_guardias")

        cursor.execute("""
            INSERT INTO guardias (id_usuario, fecha_guardia)
            VALUES (%s, %s)
        """, (id_usuario, fecha_guardia))

        cursor.execute("""
            UPDATE guardias
            SET id_feriado = (
                SELECT f.id_feriado
                FROM feriados f
                WHERE f.fecha = %s
                LIMIT 1
            )
            WHERE id_usuario = %s AND fecha_guardia = %s
        """, (fecha_guardia, id_usuario, fecha_guardia))

        conexion.commit()
        flash("Guardia registrada correctamente", "success")
        return redirigir_con_filtros("guardias.ver_guardias", "filtro_guardias")

    except mysql.connector.errors.IntegrityError as e:
        conexion.rollback()
        if e.errno == 1062:
            flash("Este fiscalizador ya tiene una guardia asignada en esa fecha", "error")
        else:
            flash("No se pudo registrar la guardia: datos duplicados o inválidos", "error")
        return redirigir_con_filtros("guardias.ver_guardias", "filtro_guardias")

    except mysql.connector.errors.OperationalError as e:
        conexion.rollback()
        if e.errno == 1205:
            flash("La base de datos está ocupada, intente nuevamente en unos segundos", "error")
        else:
            flash("Error de conexión con la base de datos, intente nuevamente", "error")
        return redirigir_con_filtros("guardias.ver_guardias", "filtro_guardias")

    except Exception as e:
        conexion.rollback()
        print("ERROR agregar_guardia:", e)
        flash("Ocurrió un error al registrar la guardia. Intente nuevamente.", "error")
        return redirigir_con_filtros("guardias.ver_guardias", "filtro_guardias")

    finally:
        cursor.close()


# ADMIN — Editar guardia (GET muestra formulario, POST guarda cambios)
@guardias_bp.route("/editar_guardia/<int:id>", methods=["GET", "POST"])
def editar_guardia(id):

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    cursor = conexion.cursor(dictionary=True)

    try:

        # ======================
        # GUARDAR CAMBIOS
        # ======================
        if request.method == "POST":

            id_usuario = request.form.get("id_usuario", "").strip()
            fecha_guardia = request.form.get("fecha_guardia", "").strip()

            if not id_usuario or not fecha_guardia:
                flash("Todos los campos son obligatorios", "error")
                return redirigir_con_filtros("guardias.ver_guardias", "filtro_guardias")

            try:
                datetime.strptime(fecha_guardia, "%Y-%m-%d")
            except ValueError:
                flash("Formato de fecha inválido. Use YYYY-MM-DD", "error")
                return redirigir_con_filtros("guardias.ver_guardias", "filtro_guardias")

            # Verificar que el fiscalizador destino esté activo
            cursor.execute("SELECT estado FROM usuarios WHERE id_usuario = %s", (id_usuario,))
            usuario = cursor.fetchone()
            if not usuario:
                flash("Usuario no encontrado", "error")
                return redirigir_con_filtros("guardias.ver_guardias", "filtro_guardias")
            if usuario["estado"] != "activo":
                flash("No se puede asignar la guardia a un usuario inactivo", "error")
                return redirigir_con_filtros("guardias.ver_guardias", "filtro_guardias")

            # Verificar que no exista otra guardia del mismo fiscalizador en esa fecha
            cursor.execute("""
                SELECT 1 FROM guardias
                WHERE id_usuario = %s AND fecha_guardia = %s AND id_guardia != %s
                LIMIT 1
            """, (id_usuario, fecha_guardia, id))

            if cursor.fetchone():
                flash("Este fiscalizador ya tiene una guardia asignada en esa fecha", "error")
                return redirigir_con_filtros("guardias.ver_guardias", "filtro_guardias")

            try:
                cursor.execute("""
                    UPDATE guardias
                    SET id_usuario=%s, fecha_guardia=%s
                    WHERE id_guardia=%s
                """, (id_usuario, fecha_guardia, id))

                cursor.execute("""
                    UPDATE guardias
                    SET id_feriado = (
                        SELECT f.id_feriado
                        FROM feriados f
                        WHERE f.fecha = %s
                        LIMIT 1
                    )
                    WHERE id_guardia = %s
                """, (fecha_guardia, id))

                conexion.commit()
                flash("Guardia actualizada correctamente", "success")
                return redirigir_con_filtros("guardias.ver_guardias", "filtro_guardias")

            except mysql.connector.errors.IntegrityError as e:
                conexion.rollback()
                if e.errno == 1062:
                    flash("Este fiscalizador ya tiene una guardia asignada en esa fecha", "error")
                else:
                    flash("No se pudo actualizar la guardia: datos duplicados o inválidos", "error")
                return redirigir_con_filtros("guardias.ver_guardias", "filtro_guardias")

            except Exception as e:
                conexion.rollback()
                print("ERROR editar_guardia:", e)
                return error_interno()

        # ======================
        # CARGAR DATOS
        # ======================
        cursor.execute("""
            SELECT *
            FROM guardias
            WHERE id_guardia=%s
        """, (id,))

        guardia = cursor.fetchone()

        if not guardia:
            return no_encontrado("Guardia no encontrada")

        cursor.execute("""
            SELECT DISTINCT
                u.id_usuario,
                u.nombre,
                u.apellidos
            FROM usuarios u
            INNER JOIN usuarios_roles ur ON u.id_usuario = ur.id_usuario
            INNER JOIN roles r ON ur.id_rol = r.id_rol
            WHERE r.nombre_rol = 'fiscalizador'
              AND (u.estado = 'activo' OR u.id_usuario = %s)
            ORDER BY u.nombre
        """, (guardia["id_usuario"],))

        fiscalizadores = cursor.fetchall()

        return render_template(
            "editar_guardia.html",
            guardia=guardia,
            fiscalizadores=fiscalizadores
        )

    finally:
        cursor.close()


@guardias_bp.route("/eliminar_guardia/<int:id>", methods=["POST"])
def eliminar_guardia(id):

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    cursor = conexion.cursor()

    try:
        _eliminar_guardias_con_archivos(cursor, [id])

        conexion.commit()

        flash("Guardia eliminada correctamente", "success")
        return redirigir_con_filtros("guardias.ver_guardias", "filtro_guardias")

    except Exception as e:

        conexion.rollback()

        print("ERROR ELIMINAR GUARDIA:", e)

        return error_interno()

    finally:

        cursor.close()


@guardias_bp.route("/eliminar_guardias", methods=["POST"])
def eliminar_guardias():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    ids = request.form.getlist("ids_guardia")
    if not ids:
        flash("No seleccionó ninguna guardia", "error")
        return redirigir_con_filtros("guardias.ver_guardias", "filtro_guardias")

    ids_int = []
    for valor in ids:
        try:
            ids_int.append(int(valor))
        except (TypeError, ValueError):
            return datos_invalidos("Identificador de guardia inválido")

    cursor = conexion.cursor()

    try:
        _eliminar_guardias_con_archivos(cursor, ids_int)
        conexion.commit()
        flash(f"Se eliminaron {cursor.rowcount} guardias correctamente", "success")
    except Exception as e:
        conexion.rollback()
        print("ERROR ELIMINAR GUARDIAS:", e)
        flash("Ocurrió un error al eliminar las guardias", "error")
    finally:
        cursor.close()

    return redirigir_con_filtros("guardias.ver_guardias", "filtro_guardias")


# ==========================
# ADMIN — ASISTENCIA
# ==========================
# Registrar asistencia (asistió, falta, justificado) desde el panel admin
@guardias_bp.route("/asistencia/<int:id_guardia>/<estado>", methods=["POST"])
def registrar_asistencia(id_guardia, estado):

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    if estado not in ["asistio", "falta", "justificado"]:
        return datos_invalidos("Estado inválido")

    cursor = conexion.cursor()

    try:
        cursor.execute("""
            SELECT id_asistencia
            FROM asistencia
            WHERE id_guardia = %s
        """, (id_guardia,))

        existe = cursor.fetchone()

        if existe:
            # Si la guardia ya tiene una compensación registrada, bloquear el cambio
            cursor.execute("""
                SELECT id_compensacion
                FROM compensaciones
                WHERE id_guardia = %s
            """, (id_guardia,))

            if cursor.fetchone():
                flash("No se puede cambiar la asistencia: elimina primero la compensación registrada", "error")
                return redirigir_con_filtros("guardias.asistencia_admin", "filtro_asistencias")

            cursor.execute("""
                UPDATE asistencia
                SET estado = %s
                WHERE id_guardia = %s
            """, (estado, id_guardia))
        else:
            cursor.execute("""
                INSERT INTO asistencia (id_guardia, estado)
                VALUES (%s, %s)
            """, (id_guardia, estado))

        conexion.commit()

        mensajes = {
            "asistio": "Asistencia marcada como 'Asistió'",
            "falta": "Asistencia marcada como 'Falta'",
            "justificado": "Asistencia marcada como 'Justificado'"
        }
        flash(mensajes.get(estado, "Asistencia actualizada"), "success")
        return redirigir_con_filtros("guardias.asistencia_admin", "filtro_asistencias")

    finally:
        cursor.close()


# ADMIN — Listado de asistencias con filtros por usuario, fecha y estado
@guardias_bp.route("/asistencias")
def asistencia_admin():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo") != "admin":
        return acceso_no_autorizado()

    guardar_filtros("filtro_asistencias")

    cursor = conexion.cursor(dictionary=True)

    id_usuario = request.args.get("id_usuario", "").strip()
    fecha_desde = request.args.get("fecha_desde", "").strip()
    fecha_hasta = request.args.get("fecha_hasta", "").strip()
    asistencia = request.args.get("asistencia", "").strip()

    try:
        # =========================
        # 1. GUARDIAS CON FILTROS
        # =========================
        sql = "SELECT rg.*, u.foto, CONCAT(u.nombre, ' ', u.apellidos) AS fiscalizador FROM resumen_guardias rg LEFT JOIN usuarios u ON rg.id_usuario = u.id_usuario WHERE 1=1 "
        params = []

        if id_usuario:
            sql += " AND rg.id_usuario = %s "
            params.append(id_usuario)

        if fecha_desde:
            sql += " AND rg.fecha_guardia >= %s "
            params.append(fecha_desde)

        if fecha_hasta:
            sql += " AND rg.fecha_guardia <= %s "
            params.append(fecha_hasta)

        if asistencia:
            if asistencia == "pendiente":
                sql += " AND (rg.asistencia IS NULL OR rg.asistencia = '' OR rg.asistencia = 'pendiente' OR rg.asistencia NOT IN ('asistio','falta','justificado')) "
            else:
                sql += " AND rg.asistencia = %s "
                params.append(asistencia)

        sql += " ORDER BY rg.fecha_guardia DESC"

        cursor.execute(sql, tuple(params))
        guardias = cursor.fetchall()

        for g in guardias:
            fecha = g.get('fecha_guardia')
            if isinstance(fecha, date):
                g['dia_semana'] = DIAS_ES[fecha.weekday()]
            else:
                g['dia_semana'] = str(fecha) if fecha else '—'

        # =========================
        # 2. FISCALIZADORES
        # =========================
        cursor.execute("""
            SELECT DISTINCT
                u.id_usuario,
                u.nombre,
                u.apellidos
            FROM usuarios u
            INNER JOIN usuarios_roles ur
                ON u.id_usuario = ur.id_usuario
            INNER JOIN roles r
                ON ur.id_rol = r.id_rol
            WHERE r.nombre_rol = 'fiscalizador'
              AND u.estado = 'activo'
            ORDER BY u.nombre
        """)

        fiscalizadores = cursor.fetchall()

        return render_template(
            "asistencia_admin.html",
            guardias=guardias,
            fiscalizadores=fiscalizadores
        )

    finally:
        cursor.close()


# ==========================
# FISCALIZADOR — ASISTENCIA PROPIA
# ==========================
# Vista de asistencias del fiscalizador logueado
@guardias_bp.route("/mis_asistencias")
def asistencia():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo", "").lower() != "fiscalizador":
        return acceso_no_autorizado()

    fecha_desde = request.args.get("fecha_desde", "").strip()
    fecha_hasta = request.args.get("fecha_hasta", "").strip()
    asistencia = request.args.get("asistencia", "").strip()

    cursor = conexion.cursor(dictionary=True)

    sql = "SELECT * FROM resumen_guardias WHERE id_usuario = %s "
    params = [session["id_usuario"]]

    if fecha_desde:
        sql += " AND fecha_guardia >= %s "
        params.append(fecha_desde)

    if fecha_hasta:
        sql += " AND fecha_guardia <= %s "
        params.append(fecha_hasta)

    if asistencia:
        if asistencia == "pendiente":
            sql += " AND (asistencia IS NULL OR asistencia = '' OR asistencia = 'pendiente' OR asistencia NOT IN ('asistio','falta','justificado')) "
        else:
            sql += " AND asistencia = %s "
            params.append(asistencia)

    sql += " ORDER BY fecha_guardia DESC"

    cursor.execute(sql, params)
    datos = cursor.fetchall()

    for d in datos:
        fecha = d.get('fecha_guardia')
        if isinstance(fecha, date):
            d['dia_semana'] = DIAS_ES[fecha.weekday()]
        else:
            d['dia_semana'] = str(fecha) if fecha else '—'

    return render_template(
        "asistencia.html",
        datos=datos
    )


# ==========================
# FISCALIZADOR — MARCAR ASISTENCIA PROPIA
# ==========================
# Permite al fiscalizador registrar su asistencia del día
@guardias_bp.route("/registrar_asistencia")
def mi_asistencia():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo", "").lower() != "fiscalizador":
        return acceso_no_autorizado()

    cursor = conexion.cursor(dictionary=True)

    cursor.execute("""
        SELECT 
            id_guardia,
            fecha_guardia,
            tipo_dia,
            asistencia
        FROM resumen_guardias
        WHERE id_usuario = %s
        ORDER BY fecha_guardia DESC
    """, (session["id_usuario"],))

    datos = cursor.fetchall()

    hoy = date.today()
    limite = hoy - timedelta(days=1)  # ventana de 48 horas: permite hoy o ayer

    for d in datos:

        fecha = d["fecha_guardia"]

        if isinstance(fecha, date):
            d['dia_semana'] = DIAS_ES[fecha.weekday()]
        else:
            d['dia_semana'] = str(fecha) if fecha else '—'

        # 1. YA REGISTRADO
        if d["asistencia"] != "sin registro":
            d["estado_accion"] = "registrado"

        # 2. FUTURO
        elif fecha > hoy:
            d["estado_accion"] = "futuro"

        # 3. DENTRO DE 48 HORAS (HOY O AYER) — ACTIVO
        elif fecha >= limite:
            d["estado_accion"] = "hoy"

        # 4. PASADO SIN REGISTRO (fuera de las 48 horas)
        else:
            d["estado_accion"] = "cerrado"

    cursor.close()

    return render_template("asistencia_fiscalizadores.html", datos=datos)


# FISCALIZADOR — Registrar asistencia vía POST (autoservicio)
@guardias_bp.route("/marcar_asistencia", methods=["POST"])
def marcar_asistencia():

    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo", "").lower() != "fiscalizador":
        return acceso_no_autorizado()

    id_guardia = request.form.get("id_guardia")
    id_usuario = session.get("id_usuario")

    if not id_guardia or not id_usuario:
        return datos_invalidos("Datos inválidos")

    cursor = conexion.cursor(dictionary=True)

    try:

        # VERIFICAR QUE LA GUARDIA PERTENECE AL FISCALIZADOR Y ES DE HOY
        cursor.execute("""
            SELECT id_guardia, fecha_guardia
            FROM guardias
            WHERE id_guardia = %s AND id_usuario = %s
        """, (id_guardia, id_usuario))

        guardia = cursor.fetchone()

        if not guardia:
            return acceso_no_autorizado()

        hoy = date.today()
        limite = hoy - timedelta(days=1)  # ventana de 48 horas
        if not (limite <= guardia["fecha_guardia"] <= hoy):
            return redirect(url_for("guardias.mi_asistencia"))

        # VERIFICAR SI YA REGISTRÓ
        cursor.execute("""
            SELECT id_asistencia
            FROM asistencia
            WHERE id_guardia = %s
        """, (id_guardia,))

        existe = cursor.fetchone()

        if existe:
            flash("Ya registraste tu asistencia", "warning")
            return redirect(url_for("guardias.mi_asistencia"))

        # INSERTAR ASISTENCIA
        cursor.execute("""
            INSERT INTO asistencia (id_guardia, estado)
            VALUES (%s, 'asistio')
        """, (id_guardia,))

        conexion.commit()

        flash("Asistencia registrada correctamente", "success")

        return redirect(url_for("guardias.mi_asistencia"))

    except Exception as e:
        conexion.rollback()
        print("ERROR marcar_asistencia:", e)
        return error_interno()

    finally:
        cursor.close()


# ==========================
# FISCALIZADOR — MIS GUARDIAS
# ==========================
# Listado de guardias propias con filtros por año, rango, asistencia y estado
@guardias_bp.route("/mis_guardias")
def mis_guardias():

    # ================= VALIDAR SESIÓN =================
    if "usuario" not in session:
        return redirect(url_for("home"))

    if session.get("perfil_activo", "").lower() != "fiscalizador":
        return acceso_no_autorizado()

    anio = request.args.get("anio")
    fecha_desde = request.args.get("fecha_desde")
    fecha_hasta = request.args.get("fecha_hasta")
    asistencia = request.args.get("asistencia")
    estado_guardia = request.args.get("estado_guardia")

    cursor = conexion.cursor(dictionary=True)

    try:

        sql = """
            SELECT
                g.id_guardia,
                g.fecha_guardia,

                CASE
                    WHEN LOWER(COALESCE(a.estado, '')) IN ('asistio')
                        THEN 'realizada'
                    WHEN LOWER(COALESCE(a.estado, '')) IN ('falta')
                        THEN 'cancelada'
                    WHEN LOWER(COALESCE(a.estado, '')) IN ('justificado')
                        THEN 'justificada'
                    ELSE 'programada'
                END AS estado_guardia,

                ELT(DAYOFWEEK(g.fecha_guardia),
                    'Domingo','Lunes','Martes','Miércoles',
                    'Jueves','Viernes','Sábado') AS dia_semana,

                CASE
                    WHEN LOWER(COALESCE(a.estado, '')) IN ('asistio')
                        THEN 'Asistió'
                    WHEN LOWER(COALESCE(a.estado, '')) IN ('falta')
                        THEN 'Falta'
                    WHEN LOWER(COALESCE(a.estado, '')) IN ('justificado')
                        THEN 'Justificado'
                    ELSE 'Pendiente'
                END AS asistencia,

                c.fecha_compensacion

            FROM guardias g
            LEFT JOIN asistencia a ON g.id_guardia = a.id_guardia
            LEFT JOIN compensaciones c ON g.id_guardia = c.id_guardia

            WHERE g.id_usuario = %s
        """

        params = [session["id_usuario"]]

        # ================= FILTRO AÑO =================
        if anio:
            try:
                anio_int = int(anio)
            except (TypeError, ValueError):
                anio_int = None
            if anio_int:
                sql += " AND YEAR(g.fecha_guardia) = %s "
                params.append(anio_int)

        # ================= FILTRO DESDE =================
        if fecha_desde:
            sql += " AND g.fecha_guardia >= %s "
            params.append(fecha_desde)

        # ================= FILTRO HASTA =================
        if fecha_hasta:
            sql += " AND g.fecha_guardia <= %s "
            params.append(fecha_hasta)

        # ================= FILTRO ASISTENCIA =================
        if asistencia:
            if asistencia == "Asistió":
                sql += " AND LOWER(COALESCE(a.estado, '')) = 'asistio' "
            elif asistencia == "Falta":
                sql += " AND LOWER(COALESCE(a.estado, '')) = 'falta' "
            elif asistencia == "Justificado":
                sql += " AND LOWER(COALESCE(a.estado, '')) = 'justificado' "
            elif asistencia == "Pendiente":
                sql += " AND (a.estado IS NULL OR LOWER(COALESCE(a.estado, '')) NOT IN ('asistio','falta','justificado')) "

        # ================= FILTRO ESTADO GUARDIA =================
        if estado_guardia == "realizada":
            sql += " AND LOWER(COALESCE(a.estado, '')) IN ('asistio') "
        elif estado_guardia == "cancelada":
            sql += " AND LOWER(COALESCE(a.estado, '')) IN ('falta') "
        elif estado_guardia == "justificada":
            sql += " AND LOWER(COALESCE(a.estado, '')) IN ('justificado') "
        elif estado_guardia == "programada":
            sql += """ AND (
                a.estado IS NULL
                OR LOWER(COALESCE(a.estado, '')) NOT IN ('asistio','falta','justificado')
            ) """

        sql += " ORDER BY g.fecha_guardia DESC"

        cursor.execute(sql, tuple(params))
        guardias = cursor.fetchall()

        return render_template(
            "mi_guardias.html",
            guardias=guardias
        )

    finally:
        cursor.close()
