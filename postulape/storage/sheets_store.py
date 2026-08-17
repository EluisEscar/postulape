"""
Almacenamiento en Google Sheets (gspread + cuenta de servicio).
MISMA interfaz y columnas que excel_store, para ser intercambiable:
    cargar_ids_existentes(ruta=None, refrescar=False) -> set
    agregar_avisos(avisos, ruta=None) -> dict

    pip install gspread

Requisitos:
  1. credentials.json (cuenta de servicio) en la raíz del proyecto.
  2. Crear un Google Sheet y COMPARTIRLO como Editor con el email de la cuenta
     de servicio (campo "client_email" dentro de credentials.json).
  3. Habilitar Google Sheets API y Google Drive API en el proyecto.
  4. En config.py: SPREADSHEET_NAME (o SPREADSHEET_KEY) y WORKSHEET_NAME.
"""

import re
import time

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
_HOJA_CACHE = None
_INDICE_IDS_CACHE = None

_INTENTOS_APPEND = 3
_ESPERA_APPEND_SEG = 1


def usar_hoja(worksheet_name=None, spreadsheet_key=None):
    """Apunta el almacén a la hoja (y opcionalmente al Sheet) de una persona."""
    global _HOJA_OVERRIDE, _KEY_OVERRIDE, _HOJA_CACHE, _INDICE_IDS_CACHE
    nueva_hoja = worksheet_name or None
    nueva_key = spreadsheet_key or None
    if (nueva_hoja, nueva_key) != (_HOJA_OVERRIDE, _KEY_OVERRIDE):
        # Una persona distinta nunca debe heredar la hoja ni los ids anteriores.
        _HOJA_CACHE = None
        _INDICE_IDS_CACHE = None
    _HOJA_OVERRIDE = nueva_hoja
    _KEY_OVERRIDE = nueva_key


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
    global _cliente, _HOJA_CACHE
    if _HOJA_CACHE is not None:
        return _HOJA_CACHE

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
    _HOJA_CACHE = ws
    return _HOJA_CACHE


def _indice_ids(refrescar=False):
    """Devuelve ``(worksheet, {id: numero_fila})`` usando una caché por hoja."""
    global _INDICE_IDS_CACHE
    ws = _abrir_hoja()
    if refrescar or _INDICE_IDS_CACHE is None:
        columna = ws.col_values(1)  # incluye encabezado
        _INDICE_IDS_CACHE = {
            str(valor): numero
            for numero, valor in enumerate(columna[1:], start=2)
            if valor
        }
    return ws, _INDICE_IDS_CACHE


def _cachear_indice_desde_filas(valores):
    """Aprovecha una lectura completa ya realizada para poblar la caché."""
    global _INDICE_IDS_CACHE
    _INDICE_IDS_CACHE = {
        str(fila[0]): numero
        for numero, fila in enumerate(valores[1:], start=2)
        if fila and fila[0]
    }


def cargar_ids_existentes(ruta=None, refrescar=False) -> set:
    """IDs para deduplicar, cacheados por hoja. ``refrescar=True`` fuerza lectura.

    ``ruta`` se ignora y se conserva únicamente por compatibilidad.
    """
    try:
        _ws, indice = _indice_ids(refrescar=refrescar)
        return set(indice)
    except Exception as e:
        print(f"[Sheets] No se pudieron leer ids existentes: {e}")
        return set()


def cargar_ids_procesados(ruta=None) -> set:
    """IDs con decisión terminal; pendientes y errores quedan reintentables."""
    try:
        valores = _abrir_hoja().get_all_values()
        if not valores:
            return set()
        _cachear_indice_desde_filas(valores)
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


def _filas_reportadas(respuesta):
    """Extrae ``updates.updatedRows``; None significa que Google no lo informó."""
    try:
        return int(respuesta["updates"]["updatedRows"])
    except (KeyError, TypeError, ValueError):
        return None


def _registrar_confirmados(elementos, respuesta=None):
    """Agrega ids confirmados a la caché y conserva su fila si viene en la API."""
    global _INDICE_IDS_CACHE
    if _INDICE_IDS_CACHE is None:
        _INDICE_IDS_CACHE = {}

    fila_inicial = None
    rango = ((respuesta or {}).get("updates") or {}).get("updatedRange", "")
    coincidencia = re.search(r"![A-Z]+(\d+):[A-Z]+\d+$", rango, re.IGNORECASE)
    if coincidencia:
        fila_inicial = int(coincidencia.group(1))

    for posicion, (aviso_id, _fila) in enumerate(elementos):
        numero = fila_inicial + posicion if fila_inicial is not None else None
        _INDICE_IDS_CACHE[aviso_id] = numero


def _append_con_confirmacion(ws, elementos):
    """Inserta filas con backoff y devuelve ``(confirmados, sin_confirmar)``."""
    pendientes = list(elementos)
    confirmados = []

    for intento in range(1, _INTENTOS_APPEND + 1):
        respuesta = None
        try:
            respuesta = ws.append_rows(
                [fila for _aviso_id, fila in pendientes],
                value_input_option="RAW",
            )
            reportadas = _filas_reportadas(respuesta)
            if reportadas == len(pendientes):
                rango = ((respuesta.get("updates") or {})
                         .get("updatedRange", "rango no informado"))
                print(f"[Sheets] API confirmó {reportadas} fila(s) en {rango}.")
                _registrar_confirmados(pendientes, respuesta)
                confirmados.extend(aviso_id for aviso_id, _fila in pendientes)
                pendientes = []
                break

            reportadas_txt = "no informado" if reportadas is None else reportadas
            print(f"[Sheets] Advertencia: append_rows confirmó {reportadas_txt}/"
                  f"{len(pendientes)} filas. Respuesta cruda: {respuesta!r}")
        except Exception as e:
            print(f"[Sheets] Error en append_rows (intento {intento}/"
                  f"{_INTENTOS_APPEND}): {e}")

        # Si hubo respuesta parcial o timeout después de escribir, releer evita
        # duplicar las filas que sí alcanzaron a persistirse.
        try:
            _ws, indice_actual = _indice_ids(refrescar=True)
            encontrados = [item for item in pendientes if item[0] in indice_actual]
            confirmados.extend(aviso_id for aviso_id, _fila in encontrados)
            pendientes = [item for item in pendientes if item[0] not in indice_actual]
        except Exception as e:
            print(f"[Sheets] No se pudo verificar la escritura: {e}")

        if not pendientes:
            break
        if intento < _INTENTOS_APPEND:
            espera = _ESPERA_APPEND_SEG * (2 ** (intento - 1))
            print(f"[Sheets] Reintentando {len(pendientes)} fila(s) en {espera}s...")
            time.sleep(espera)

    return confirmados, [aviso_id for aviso_id, _fila in pendientes]


def agregar_avisos(avisos, ruta=None, actualizar_existentes=False) -> dict:
    """Inserta ofertas y, opcionalmente, actualiza filas existentes por ID.

    ``ruta`` se conserva por compatibilidad con ``excel_store``. El modo de
    actualización permite completar una fila creada por ``--solo-scrape``.
    """
    ws, filas_por_id = _indice_ids()
    existentes = set(filas_por_id)

    filas, actualizaciones = [], []
    actualizados = duplicados = 0
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
        filas.append((aviso_id, fila))
        existentes.add(aviso_id)

    preparados = len(filas)
    confirmados, sin_confirmar = [], []
    if filas:
        confirmados, sin_confirmar = _append_con_confirmacion(ws, filas)
    for numero, fila in actualizaciones:
        if numero is None:
            # Solo puede ocurrir si el id se añadió a caché sin updatedRange.
            _ws, filas_por_id = _indice_ids(refrescar=True)
            numero = filas_por_id.get(str(fila[0]))
        if numero is None:
            print(f"[Sheets] ERROR: no se encontró la fila para actualizar {fila[0]}.")
            continue
        ws.update([fila], f"A{numero}:M{numero}", value_input_option="RAW")
    insertados = len(confirmados)

    if sin_confirmar:
        print(f"[Sheets] ERROR: {len(sin_confirmar)} fila(s) sin confirmar tras "
              f"{_INTENTOS_APPEND} intentos: {', '.join(sin_confirmar)}")

    print(f"[Sheets] {insertados} insertados confirmados, {actualizados} actualizados, "
          f"{duplicados} duplicados omitidos.")
    return {"insertados": insertados, "actualizados": actualizados,
            "duplicados": duplicados, "preparados": preparados,
            "sin_confirmar": sin_confirmar}
