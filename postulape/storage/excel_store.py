"""
Almacenamiento en Excel (openpyxl). 11 columnas. Deduplica por 'id'.
Sanitiza texto para que Excel no interprete fórmulas (=, +, -, @).
Migra encabezados si el archivo existente tiene un esquema distinto.

    pip install openpyxl
"""

import os

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

ENCABEZADOS = [
    "id", "plataforma", "titulo", "empresa", "distrito", "departamento",
    "modalidad", "antiguedad", "descripcion", "link", "estado",
]

_IDX_ID = ENCABEZADOS.index("id")
_MAX_CELDA = 32000
VERDE = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")


def _sanitizar(valor):
    """Evita inyección de fórmulas: si un texto empieza con = + - @, le antepone
    una comilla simple para que Excel lo trate como texto."""
    if isinstance(valor, str) and valor[:1] in ("=", "+", "-", "@"):
        return "'" + valor
    return valor


def _asegurar_libro(ruta: str):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    if os.path.exists(ruta):
        wb = load_workbook(ruta)
        ws = wb["Postulaciones"] if "Postulaciones" in wb.sheetnames else wb.active
        ws.title = "Postulaciones"
        # Migración: si los encabezados actuales no coinciden, los reescribe.
        actuales = [c.value for c in ws[1]] if ws.max_row >= 1 else []
        if actuales[:len(ENCABEZADOS)] != ENCABEZADOS:
            for j, nombre in enumerate(ENCABEZADOS, start=1):
                ws.cell(row=1, column=j, value=nombre).font = Font(bold=True)
        return wb, ws
    wb = Workbook()
    ws = wb.active
    ws.title = "Postulaciones"
    ws.append(ENCABEZADOS)
    for celda in ws[1]:
        celda.font = Font(bold=True)
    ws.freeze_panes = "A2"
    wb.save(ruta)
    return wb, ws


def cargar_ids_existentes(ruta: str) -> set:
    """Lee la columna 'id' para saber qué avisos ya están guardados."""
    if not os.path.exists(ruta):
        return set()
    wb = load_workbook(ruta, read_only=True)
    ws = wb["Postulaciones"] if "Postulaciones" in wb.sheetnames else wb.active
    ids = set()
    for i, fila in enumerate(ws.iter_rows(values_only=True)):
        if i == 0:
            continue
        if fila and len(fila) > _IDX_ID and fila[_IDX_ID]:
            ids.add(str(fila[_IDX_ID]))
    wb.close()
    return ids


def agregar_avisos(avisos: list, ruta: str) -> dict:
    """Inserta avisos nuevos deduplicando por 'id'. Los que aplican se resaltan
    en verde. Guarda el veredicto resumido en 'estado'."""
    wb, ws = _asegurar_libro(ruta)
    existentes = cargar_ids_existentes(ruta)

    insertados = duplicados = 0
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
        ]
        ws.append([_sanitizar(v) for v in fila])
        if a.get("aplica"):
            for celda in ws[ws.max_row]:
                celda.fill = VERDE
        existentes.add(aviso_id)
        insertados += 1

    wb.save(ruta)
    print(f"[Excel] {insertados} nuevos insertados, {duplicados} duplicados omitidos.")
    return {"insertados": insertados, "duplicados": duplicados}
