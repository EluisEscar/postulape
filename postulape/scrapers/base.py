"""
Utilidades compartidas por los 3 scrapers y por main.py.
"""

import re
import unicodedata


def slugify(texto: str) -> str:
    """minúsculas, sin tildes, espacios -> guiones (formato de URLs)."""
    texto = texto.strip().lower()
    texto = unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii")
    texto = re.sub(r"[^a-z0-9\s-]", "", texto)
    texto = re.sub(r"\s+", "-", texto)
    return texto


def quitar_tildes(texto: str) -> str:
    if not texto:
        return ""
    return unicodedata.normalize("NFKD", texto).encode("ascii", "ignore").decode("ascii").lower()


def parsear_dias_antiguedad(texto: str):
    """Convierte 'Publicado hace 3 días' / 'hace 5 horas' / 'hoy' a un número de días.
    Devuelve None si no logra interpretarlo (no lo descartamos por las dudas)."""
    if not texto:
        return None
    t = quitar_tildes(texto)

    if "hoy" in t or "hora" in t or "minuto" in t or "recien" in t:
        return 0

    m = re.search(r"(\d+)\s*dia", t)
    if m:
        return int(m.group(1))

    m = re.search(r"(\d+)\s*semana", t)
    if m:
        return int(m.group(1)) * 7

    m = re.search(r"(\d+)\s*mes", t)
    if m:
        return int(m.group(1)) * 30

    return None


def pasa_filtro_antiguedad(aviso: dict, max_dias: int) -> bool:
    dias = parsear_dias_antiguedad(aviso.get("antiguedad"))
    if dias is None:
        return True  # si no sabemos, no descartamos
    return dias <= max_dias


def pasa_filtro_modalidad(aviso: dict, permitidas: list) -> bool:
    if not permitidas:
        return True
    modalidad = quitar_tildes(aviso.get("modalidad") or "")
    if not modalidad:
        return True  # muchas ofertas no declaran modalidad en la tarjeta
    permitidas_norm = [quitar_tildes(m) for m in permitidas]
    return any(p in modalidad for p in permitidas_norm)


def pasa_filtro_departamento(aviso: dict, permitidos: list) -> bool:
    if not permitidos:
        return True
    depto = quitar_tildes(aviso.get("departamento") or aviso.get("ubicacion") or "")
    if not depto:
        return True
    permitidos_norm = [quitar_tildes(d) for d in permitidos]
    return any(p in depto for p in permitidos_norm)


def separar_ubicacion(ubicacion: str):
    """'San Isidro, Lima' -> ('San Isidro', 'Lima')."""
    distrito = departamento = None
    if ubicacion:
        partes = [p.strip() for p in ubicacion.split(",")]
        if len(partes) == 2:
            distrito, departamento = partes
        elif len(partes) == 1:
            distrito = departamento = partes[0]
        else:
            distrito, departamento = partes[0], partes[-1]
    return distrito, departamento
