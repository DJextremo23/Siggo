"""Constantes y funciones auxiliares compartidas entre blueprints."""

import os
from datetime import date, datetime, timedelta, timezone

# Días de la semana en español para mostrar en las vistas
DIAS_ES = ['Lunes', 'Martes', 'Miércoles', 'Jueves', 'Viernes', 'Sábado', 'Domingo']

# Meses abreviados en español para los bloques de fecha
MESES_ABREV = ['Ene', 'Feb', 'Mar', 'Abr', 'May', 'Jun', 'Jul', 'Ago', 'Sep', 'Oct', 'Nov', 'Dic']

# Nombres completos de los meses en español
MESES_ES = ['Enero', 'Febrero', 'Marzo', 'Abril', 'Mayo', 'Junio',
            'Julio', 'Agosto', 'Septiembre', 'Octubre', 'Noviembre', 'Diciembre']

def _zona_horaria():
    valor = os.getenv("DB_TIMEZONE", "-05:00").strip()
    try:
        signo = -1 if valor.startswith("-") else 1
        hh, mm = valor.lstrip("+-").split(":")
        return timezone(signo * timedelta(hours=int(hh), minutes=int(mm)))
    except Exception:
        return timezone(timedelta(hours=-5))


ZONA_HORARIA = _zona_horaria()


def hoy_local():
    return datetime.now(ZONA_HORARIA).date()


def balance_vacaciones_fifo(anio_ingreso, dias_tomados_total):
    """Calcula el balance de vacaciones con atribución FIFO (igual que reportes).

    Los días tomados se descuentan primero de los períodos anteriores al año
    actual; el excedente se atribuye al año en curso. Devuelve
    (dias_tomados_este_anio, dias_pendientes_este_anio, dias_pendientes_anteriores).
    """
    anio_actual = date.today().year
    ingreso = int(anio_ingreso or anio_actual)
    tomados = int(dias_tomados_total or 0)
    if anio_actual <= ingreso:
        return 0, 0, 0
    ent_antes = (anio_actual - ingreso - 1) * 30
    dias_tomados_este_anio = min(30, max(0, tomados - ent_antes))
    dias_pendientes_este_anio = max(0, 30 - dias_tomados_este_anio)
    dias_pendientes_anteriores = max(0, ent_antes - tomados)
    return dias_tomados_este_anio, dias_pendientes_este_anio, dias_pendientes_anteriores
