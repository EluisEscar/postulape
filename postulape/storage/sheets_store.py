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
_SIGUIENTE_FILA_CACHE = None

_INTENTOS_APPEND = 3
_ESPERA_APPEND_SEG = 1
_INTENTOS_LECTURA = 3
_ESPERA_LECTURA_SEG = 1


class ErrorLecturaSheets(RuntimeError):
    """La deduplicación no pudo leer la hoja tras agotar los reintentos."""


def usar_hoja(worksheet_name=None, spreadsheet_key=None):
    """Apunta el almacén a la hoja (y opcionalmente al Sheet) de una persona."""
    global _HOJA_OVERRIDE, _KEY_OVERRIDE
    global _HOJA_CACHE, _INDICE_IDS_CACHE, _SIGUIENTE_FILA_CACHE
    nueva_hoja = worksheet_name or None
    nueva_key = spreadsheet_key or None
    if (nueva_hoja, nueva_key) != (_HOJA_OVERRIDE, _KEY_OVERRIDE):
        # Una persona distinta nunca debe heredar la hoja ni los ids anteriores.
        _HOJA_CACHE = None
        _INDICE_IDS_CACHE = None
        _SIGUIENTE_FILA_CACHE = None
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
    ws = _abrir_hoja()
    if refrescar or _INDICE_IDS_CACHE is None or _SIGUIENTE_FILA_CACHE is None:
        # La lectura completa permite respetar contenido fuera de la columna A
        # al calcular la siguiente fila física segura.
        _cachear_indice_desde_filas(ws.get_all_values())
    return ws, _INDICE_IDS_CACHE


def _cachear_indice_desde_filas(valores):
    """Puebla la caché de ids y la siguiente fila física disponible."""
    global _INDICE_IDS_CACHE, _SIGUIENTE_FILA_CACHE
    _INDICE_IDS_CACHE = {
        str(fila[0]): numero
        for numero, fila in enumerate(valores[1:], start=2)
        if fila and fila[0]
    }
    _SIGUIENTE_FILA_CACHE = max(2, len(valores) + 1)


def _leer_con_reintentos(descripcion, operacion):
    """Ejecuta una lectura con backoff y propaga un error inequívoco al final."""
    ultimo_error = None
    for intento in range(1, _INTENTOS_LECTURA + 1):
        try:
            return operacion()
        except Exception as e:
            ultimo_error = e
            if intento < _INTENTOS_LECTURA:
                espera = _ESPERA_LECTURA_SEG * (2 ** (intento - 1))
                print(f"[Sheets] No se pudo {descripcion} (intento {intento}/"
                      f"{_INTENTOS_LECTURA}): {e}. Reintentando en {espera}s...")
                time.sleep(espera)

    raise ErrorLecturaSheets(
        f"No se pudo {descripcion} tras {_INTENTOS_LECTURA} intentos: "
        f"{ultimo_error}"
    ) from ultimo_error


def cargar_ids_existentes(ruta=None, refrescar=False) -> set:
    """IDs para deduplicar, cacheados por hoja. ``refrescar=True`` fuerza lectura.

    ``ruta`` se ignora y se conserva únicamente por compatibilidad.
    """
    def leer():
        _ws, indice = _indice_ids(refrescar=refrescar)
        return set(indice)

    return _leer_con_reintentos("leer los ids existentes", leer)


def cargar_ids_procesados(ruta=None) -> set:
    """IDs con decisión terminal; pendientes y errores quedan reintentables."""
    def leer():
        valores = _abrir_hoja().get_all_values()
        if not valores:
            _cachear_indice_desde_filas(valores)
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

    return _leer_con_reintentos("leer los estados existentes", leer)


def _filas_reportadas(respuesta):
    """Extrae ``updatedRows``; None significa que Google no lo informó."""
    try:
        return int(respuesta["updatedRows"])
    except (KeyError, TypeError, ValueError):
        return None


def _filas_del_rango(rango):
    """Devuelve las filas inicial/final de un rango A:M, ignorando el nombre."""
    coincidencia = re.search(
        r"(?:^|!)\$?A\$?(\d+):\$?M\$?(\d+)$", str(rango or ""),
        re.IGNORECASE,
    )
    if not coincidencia:
        return None
    return int(coincidencia.group(1)), int(coincidencia.group(2))


def _respuesta_confirma(respuesta, elementos, fila_inicial):
    """Confirma cantidad, rango e ids devueltos por ``values.update``."""
    esperadas = len(elementos)
    fila_final = fila_inicial + esperadas - 1
    if _filas_reportadas(respuesta) != esperadas:
        return False
    if _filas_del_rango((respuesta or {}).get("updatedRange")) != (
            fila_inicial, fila_final):
        return False

    valores = ((respuesta or {}).get("updatedData") or {}).get("values") or []
    ids_dev_runtime = [str(fila[0]) for fila in valores if fila]
    ids_esperados = [aviso_id for aviso_id, _fila in elementos]
    return ids_dev_runtime == ids_esperados


def _registrar_confirmados(elementos, fila_inicial):
    """Registra ids en sus filas explícitas y avanza el siguiente destino."""
    global _INDICE_IDS_CACHE, _SIGUIENTE_FILA_CACHE
    if _INDICE_IDS_CACHE is None:
        _INDICE_IDS_CACHE = {}

    for posicion, (aviso_id, _fila) in enumerate(elementos):
        _INDICE_IDS_CACHE[aviso_id] = fila_inicial + posicion
    _SIGUIENTE_FILA_CACHE = fila_inicial + len(elementos)


def _insertar_con_confirmacion(ws, elementos):
    """Escribe filas explícitas con backoff; nunca usa la tabla lógica de Google."""
    pendientes = list(elementos)
    confirmados = []

    for intento in range(1, _INTENTOS_APPEND + 1):
        respuesta = None
        fila_inicial = _SIGUIENTE_FILA_CACHE
        fila_final = fila_inicial + len(pendientes) - 1
        rango_esperado = f"A{fila_inicial}:M{fila_final}"
        try:
            respuesta = ws.update(
                [fila for _aviso_id, fila in pendientes],
                rango_esperado,
                value_input_option="RAW",
                include_values_in_response=True,
            )
            if _respuesta_confirma(respuesta, pendientes, fila_inicial):
                rango = respuesta.get("updatedRange", rango_esperado)
                print(f"[Sheets] API confirmó {len(pendientes)} fila(s) en {rango} "
                      "con ids correctos.")
                _registrar_confirmados(pendientes, fila_inicial)
                confirmados.extend(aviso_id for aviso_id, _fila in pendientes)
                pendientes = []
                break

            print(f"[Sheets] Advertencia: la API no confirmó cantidad, rango e ids "
                  f"para {rango_esperado}. Respuesta cruda: {respuesta!r}")
        except Exception as e:
            print(f"[Sheets] Error escribiendo {rango_esperado} (intento {intento}/"
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
        confirmados, sin_confirmar = _insertar_con_confirmacion(ws, filas)
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
