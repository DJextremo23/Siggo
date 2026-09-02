"""Pruebas de validación de tipos de archivo permitidos en el módulo de informes."""

from mis_informes import archivo_permitido


def test_pdf_permitido():
    """Los archivos PDF deben ser aceptados."""
    assert archivo_permitido("reporte.pdf")


def test_docx_permitido():
    """Los archivos DOCX deben ser aceptados."""
    assert archivo_permitido("documento.docx")


def test_xlsx_permitido():
    """Los archivos XLSX deben ser aceptados."""
    assert archivo_permitido("datos.xlsx")


def test_xlsm_permitido():
    """Los archivos XLSM (con macros) deben ser aceptados."""
    assert archivo_permitido("macro.xlsm")


def test_txt_no_permitido():
    """Los archivos TXT no deben ser aceptados."""
    assert not archivo_permitido("archivo.txt")


def test_exe_no_permitido():
    """Los archivos ejecutables no deben ser aceptados."""
    assert not archivo_permitido("virus.exe")


def test_sin_extension_no_permitido():
    """Los archivos sin extensión no deben ser aceptados."""
    assert not archivo_permitido("archivo")


def test_mayusculas_extension():
    """Las extensiones en mayúsculas deben ser aceptadas igualmente."""
    assert archivo_permitido("reporte.PDF")
    assert archivo_permitido("reporte.DOCX")
