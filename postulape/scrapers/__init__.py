"""Scrapers de las plataformas de empleo soportadas."""

from .bumeran import scrapear_bumeran
from .computrabajo import scrapear_computrabajo
from .indeed import scrapear_indeed

__all__ = ["scrapear_bumeran", "scrapear_computrabajo", "scrapear_indeed"]
