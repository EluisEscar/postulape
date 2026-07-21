"""
Almacenamiento en Google Sheets (gspread + cuenta de servicio).
MISMA interfaz y columnas que excel_store, para ser intercambiable:
    cargar_ids_existentes(ruta=None) -> set
    agregar_avisos(avisos, ruta=None) -> dict

    pip install gspread

Requisitos:
  1. credentials.json (cuenta de servicio) en la raíz del proyecto.
  2. Crear un Google Sheet y COMPARTIRLO como Editor con el email de la cuenta
     de servicio (campo "client_email" dentro de credentials.json).
  3. Habilitar Google Sheets API y Google Drive API en el proyecto.
  4. En config.py: SPREADSHEET_NAME (o SPREADSHEET_KEY) y WORKSHEET_NAME.
"""

import gspread

from postulape import config

ENCABEZADOS = [
    "id", "plataforma", "titulo", "empresa", "distrito", "departamento",
    "modalidad", "antiguedad", "descripcion", "link", "estado", "aplica", "cv",
]

_MAX_CELDA = 45000   # limite por celda en Sheets es 50000
_cliente = None


def _sanitizar(valor):
    """Evita que Sheets interprete texto como formula (=, +, -, @)."""
    if isinstance(valor, str) and valor[:1] in ("=", "+", "-", "@"):
        return "'" + valor
    return valor


def _abrir_hoja():
    global _cliente
    if _cliente is None:
        _cliente = gspread.service_account(filename=config.GOOGLE_CREDENTIALS)

    if getattr(config, "SPREADSHEET_KEY", ""):
        sh = _cliente.open_by_key(config.SPREADSHEET_KEY)
    else:
        try:
            sh = _cliente.open(config.SPREADSHEET_NAME)
        except gspread.SpreadsheetNotFound:
            sh = _cliente.create(config.SPREADSHEET_NAME)

    try:
        ws = sh.worksheet(config.WORKSHEET_NAME)
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=config.WORKSHEET_NAME, rows=1000, cols=len(ENCABEZADOS))

    if not ws.acell("A1").value:
        ws.update([ENCABEZADOS], "A1")
    return ws


def cargar_ids_existentes(ruta=None) -> set:
    """Lee la columna 'id' (col A) para deduplicar. 'ruta' se ignora (compat.)."""
    try:
        ws = _abrir_hoja()
        col = ws.col_values(1)  # incluye encabezado
        return {str(v) for v in col[1:] if v}
    except Exception as e:
        print(f"[Sheets] No se pudieron leer ids existentes: {e}")
        return set()


def agregar_avisos(avisos, ruta=None) -> dict:
    """Inserta avisos nuevos (dedup por id) en una escritura batch. 'ruta' se
    ignora; se usa por compatibilidad con la firma de excel_store."""
    ws = _abrir_hoja()
    existentes = cargar_ids_existentes()

    filas, insertados, duplicados = [], 0, 0
    for a in avisos:
        aviso_id = str(a.get("id") or "")
        if not aviso_id or aviso_id in existentes:
            duplicados += 1
            continue
        fila = [
            aviso_id,
            a.get("plataforma", ""),
            a.get("titulo", ""),
            a.get("empresa", ""),
            a.get("distrito", ""),
            a.get("departamento", ""),
            a.get("modalidad", ""),
            a.get("antiguedad", ""),
            (a.get("descripcion") or "")[:_MAX_CELDA],
            a.get("link", ""),
            a.get("estado", "Pendiente"),
            ("Sí" if a.get("aplica") else "No"),
            a.get("cv_generado", ""),
        ]
        filas.append([_sanitizar(v) for v in fila])
        existentes.add(aviso_id)
        insertados += 1

    if filas:
        ws.append_rows(filas, value_input_option="RAW")

    print(f"[Sheets] {insertados} nuevos insertados, {duplicados} duplicados omitidos.")
    return {"insertados": insertados, "duplicados": duplicados}