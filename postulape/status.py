"""Estados compartidos del pipeline.

La deduplicación no debe confundir una fila existente con una evaluación
terminada: los estados pendientes o con error se pueden volver a procesar.
"""


def es_estado_terminal(estado) -> bool:
    """True cuando una oferta ya tiene una decisión que no requiere reintento."""
    normalizado = str(estado or "").strip().lower()
    return normalizado.startswith(("aplica", "no aplica", "descartado"))


def es_resultado_completo(estado, cv_generado="") -> bool:
    """True si la decisión está lista y, cuando aplica, ya existe su CV."""
    normalizado = str(estado or "").strip().lower()
    if not es_estado_terminal(normalizado):
        return False
    return not normalizado.startswith("aplica") or bool(str(cv_generado or "").strip())
