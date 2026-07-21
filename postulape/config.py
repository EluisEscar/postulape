"""
Configuración central de PostulaPe.
Todo lo que quieras ajustar (keywords, filtros, proveedor LLM, rutas) vive aquí.
"""

import os

from dotenv import load_dotenv

# Carga el .env de la raíz del proyecto (junto a main.py)
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

# ---------------------------------------------------------------------------
# BÚSQUEDA
# ---------------------------------------------------------------------------
# Palabras clave a buscar en las 3 plataformas. El scraper corre una búsqueda
# por cada keyword. Pon las genéricas: el LLM luego filtra las que no aplican.
KEYWORDS = [
    "analista",
    "asistente",
    "desarrollador",
]

UBICACION = "lima"

# Qué plataformas correr en esta ejecución (comenta las que no quieras).
PLATAFORMAS_ACTIVAS = [
    "bumeran",
    "computrabajo",
    # "indeed",   # desactivado por ahora (Cloudflare lo bloquea seguido)
]

# ---------------------------------------------------------------------------
# FILTROS (se aplican DESPUÉS de scrapear, sobre el texto ya parseado)
# ---------------------------------------------------------------------------
MAX_DIAS_ANTIGUEDAD = 7          # descarta avisos publicados hace más de N días
MODALIDADES_PERMITIDAS = [       # en minúsculas; vacío [] = no filtrar por modalidad
    "híbrido",
    "hibrido",
    "remoto",
    "presencial",   # quita este si solo quieres híbrido/remoto
]
DEPARTAMENTOS_PERMITIDOS = ["lima"]   # vacío [] = no filtrar por ubicación

ANIOS_EXPERIENCIA = 1      # tus años reales de experiencia profesional
MAX_BRECHA_ANIOS = 2       # si la oferta pide más de ANIOS_EXPERIENCIA + esto, se descarta

# ---------------------------------------------------------------------------
# NAVEGADOR (Playwright)
# ---------------------------------------------------------------------------
# Indeed y Computrabajo funcionan mucho mejor con ventana visible (headless=False).
HEADLESS = False
PAUSA_ENTRE_PAGINAS_SEG = 2.0    # ir despacio reduce bloqueos

# ---------------------------------------------------------------------------
# LLM
# ---------------------------------------------------------------------------
# Proveedor: "gemini" | "groq" | "openrouter" | "ollama"
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "gemini")

# Modelo por proveedor. Ejemplos (verifica disponibilidad actual):
#   gemini:     "gemini-2.5-flash-lite" (mas RPD) | "gemini-2.5-flash"
#   groq:       "llama-3.1-8b-instant" | "llama-3.3-70b-versatile"
#   openrouter: "meta-llama/llama-3.3-70b-instruct:free"
#   ollama:     "llama3.1" | "qwen2.5:7b"   (deben estar descargados localmente)
LLM_MODEL = os.getenv("LLM_MODEL", "gemini-2.5-flash-lite")

# Modelo SOLO para clasificar títulos (Etapa 2). Tarea barata: puedes usar un
# modelo más chico/rápido. Por defecto usa el mismo que LLM_MODEL.
LLM_MODEL_CLASIF = os.getenv("LLM_MODEL_CLASIF", LLM_MODEL)

# Proveedor/modelo SOLO para generar el CV. Por defecto usa el mismo de arriba,
# pero puedes ponerlo en "ollama" para que tu CV no salga de tu máquina.
LLM_PROVIDER_CV = os.getenv("LLM_PROVIDER_CV", LLM_PROVIDER)
LLM_MODEL_CV = os.getenv("LLM_MODEL_CV", LLM_MODEL)

# Etapa 2 del embudo: cuántos títulos se mandan por llamada al clasificar.
TAM_LOTE_TITULOS = 40

# Pausa (segundos) entre llamadas al LLM en la Etapa 4, para respetar el límite
# de peticiones por minuto del tier gratis. Flash-Lite ~15 RPM -> 5s alcanza;
# si usas un modelo de 5 RPM, súbela a ~13.
PAUSA_LLM_SEG = int(os.getenv("PAUSA_LLM_SEG", "5"))

# API keys por variable de entorno (no las pongas hardcodeadas aquí).
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://localhost:11434")

# Umbral de score (0-100) a partir del cual se considera que "aplicas" y se
# genera CV. El LLM devuelve su propio veredicto booleano, pero esto te da
# un control fino adicional.
UMBRAL_APLICA = 60

# Si True, un pre-filtro barato por keywords descarta avisos obviamente ajenos
# (contable, tributario, legal, etc.) ANTES de llamar al LLM, para ahorrar tokens.
USAR_PREFILTRO = True
PREFILTRO_BLOCKLIST = [
    "contable", "contabilidad", "tributari", "legal", "abogado", "jurídico",
    "ventas", "vendedor", "comercial", "cobranza", "call center", "cajero",
    "enfermero", "enfermera", "médico", "odontolog", "docente", "profesor",
    "cocina", "cocinero", "mozo", "azafata", "dental", "limpieza", "seguridad",
    "vigilancia", "recepcionist", "almacén", "conductor", "chofer", "operario",
    "mecánico", "electricista", "gastronom", "veterinari", "psicolog",
]

# Lista de RESCATE técnico: si el título contiene alguna de estas señales, el
# aviso NO se descarta por blocklist (aunque tenga una palabra del blocklist).
# Se comparan como PALABRAS completas (no substring), para que "ti" no matchee
# "gestion". Evita banear roles tech como "Analista de Seguridad de la Información".
TECH_ALLOWLIST = [
    "ti", "tic", "sistemas", "software", "hardware", "programador", "programacion",
    "desarrollador", "desarrollo", "developer", "qa", "testing", "tester",
    "data", "datos", "bi", "base de datos", "soporte", "soporte tecnico",
    "informatica", "informatico", "redes", "cloud", "devops", "erp", "sap",
    "crm", "power bi", "full stack", "fullstack", "backend", "frontend",
    "ciberseguridad", "seguridad de la informacion", "seguridad informatica",
    "help desk", "helpdesk", "it", "python", "java", "web", "scrum",
]

# ---------------------------------------------------------------------------
# RUTAS
# ---------------------------------------------------------------------------
DIR_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RUTA_EXCEL = os.path.join(DIR_BASE, "output", "postulaciones.xlsx")
DIR_CVS = os.path.join(DIR_BASE, "output", "cvs")
RUTA_CV_BASE = os.path.join(DIR_BASE, "cv_base.md")

# ---------------------------------------------------------------------------
# GOOGLE SHEETS
# ---------------------------------------------------------------------------
GOOGLE_CREDENTIALS = os.path.join(DIR_BASE, "credentials.json")
SPREADSHEET_NAME = os.getenv("SPREADSHEET_NAME", "PostulaPe")
SPREADSHEET_KEY = os.getenv("SPREADSHEET_KEY", "1QgoqqmhY19_XeFSMgTbQvnTQPfJxfgjjNSZl0IVw0XU")
WORKSHEET_NAME = os.getenv("WORKSHEET_NAME", "Postulaciones")