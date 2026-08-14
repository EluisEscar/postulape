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
from postulape.status import es_resultado_completo

ENCABEZADOS = [
    "id", "plataforma", "titulo", "empresa", "distrito", "departamento",
    "modalidad", "antiguedad", "descripcion", "link", "estado", "aplica", "cv",
]

# Overrides por persona (los setea personas/cli). Si están vacíos usa config.
_HOJA_OVERRIDE = None
_KEY_OVERRIDE = None


def usar_hoja(worksheet_name=None, spreadsheet_key=None):
    """Apunta el almacén a la hoja (y opcionalmente al Sheet) de una persona."""
    global _HOJA_OVERRIDE, _KEY_OVERRIDE
    _HOJA_OVERRIDE = worksheet_name or None
    _KEY_OVERRIDE = spreadsheet_key or None


def _nombre_hoja():
    return _HOJA_OVERRIDE or config.WORKSHEET_NAME


def _key_sheet():
    return _KEY_OVERRIDE or getattr(config, "SPREADSHEET_KEY", "")


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

    if _key_sheet():
        sh = _cliente.open_by_key(_key_sheet())
    else:
        try:
            sh = _cliente.open(config.SPREADSHEET_NAME)
        except gspread.SpreadsheetNotFound:
            sh = _cliente.create(config.SPREADSHEET_NAME)

    try:
        ws = sh.worksheet(_nombre_hoja())
    except gspread.WorksheetNotFound:
        ws = sh.add_worksheet(title=_nombre_hoja(), rows=1000, cols=len(ENCABEZADOS))

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


def cargar_ids_procesados(ruta=None) -> set:
    """IDs con decisión terminal; pendientes y errores quedan reintentables."""
    try:
        valores = _abrir_hoja().get_all_values()
        if not valores:
            return set()
        encabezados = valores[0]
        idx_id = encabezados.index("id")
        idx_estado = encabezados.index("estado")
        idx_cv = encabezados.index("cv")
        return {
            str(fila[idx_id])
            for fila in valores[1:]
            if len(fila) > max(idx_id, idx_estado, idx_cv)
            and fila[idx_id]
            and es_resultado_completo(fila[idx_estado], fila[idx_cv])
        }
    except Exception as e:
        print(f"[Sheets] No se pudieron leer estados existentes: {e}")
        return set()


def agregar_avisos(avisos, ruta=None, actualizar_existentes=False) -> dict:
    """Inserta ofertas y, opcionalmente, actualiza filas existentes por ID.

    ``ruta`` se conserva por compatibilidad con ``excel_store``. El modo de
    actualización permite completar una fila creada por ``--solo-scrape``.
    """
    ws = _abrir_hoja()
    valores = ws.get_all_values()
    filas_por_id = {
        str(fila[0]): numero
        for numero, fila in enumerate(valores[1:], start=2)
        if fila and fila[0]
    }
    existentes = set(filas_por_id)

    filas, actualizaciones = [], []
    insertados = actualizados = duplicados = 0
    for a in avisos:
        aviso_id = str(a.get("id") or "")
        if not aviso_id:
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
        fila = [_sanitizar(v) for v in fila]
        if aviso_id in existentes:
            if actualizar_existentes:
                actualizaciones.append((filas_por_id[aviso_id], fila))
                actualizados += 1
            else:
                duplicados += 1
            continue
        filas.append(fila)
        existentes.add(aviso_id)
        insertados += 1

    if filas:
        ws.append_rows(filas, value_input_option="RAW")
    for numero, fila in actualizaciones:
        ws.update([fila], f"A{numero}:M{numero}", value_input_option="RAW")

    print(f"[Sheets] {insertados} insertados, {actualizados} actualizados, "
          f"{duplicados} duplicados omitidos.")
    return {"insertados": insertados, "actualizados": actualizados,
            "duplicados": duplicados}
