"""
Capa de datos sobre Supabase (via supabase-py). Reemplaza a sheets_store.

Usa la SERVICE ROLE key (el backend/scraper actúa por encima de RLS).
Nunca expongas esa key en el frontend ni en el repo: va en el .env.

    pip install supabase

Config en .env:
    SUPABASE_URL=https://xxxx.supabase.co
    SUPABASE_SERVICE_KEY=eyJ...   (Settings → API → service_role)
    DEFAULT_USER_ID=uuid-del-usuario   (para correr el pipeline sin frontend)
"""

from postulape import config
from postulape.status import es_estado_terminal
from supabase import create_client

_client = None


def cliente():
    global _client
    if _client is None:
        if not config.SUPABASE_URL or not config.SUPABASE_SERVICE_KEY:
            raise RuntimeError(
                "Faltan SUPABASE_URL / SUPABASE_SERVICE_KEY en el .env"
            )
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_KEY)
    return _client


# ---------------------------------------------------------------------------
# JOBS — pool central compartido
# ---------------------------------------------------------------------------
def _fila_job(a: dict) -> dict:
    return {
        "id": str(a.get("id")),
        "plataforma": a.get("plataforma", ""),
        "titulo": a.get("titulo", ""),
        "empresa": a.get("empresa", ""),
        "ubicacion": a.get("ubicacion", ""),
        "distrito": a.get("distrito", ""),
        "departamento": a.get("departamento", ""),
        "modalidad": a.get("modalidad", ""),
        "antiguedad": a.get("antiguedad", ""),
        "descripcion": a.get("descripcion") or "",
        "link": a.get("link", ""),
    }


def cargar_ids_jobs() -> set:
    """IDs de avisos ya presentes en el pool (para no re-insertar)."""
    res = cliente().table("jobs").select("id").execute()
    return {r["id"] for r in (res.data or [])}


def agregar_jobs(avisos: list) -> int:
    """Upsert de avisos al pool (dedup por id). Devuelve cuántos se enviaron."""
    filas = [_fila_job(a) for a in avisos if a.get("id")]
    if not filas:
        return 0
    cliente().table("jobs").upsert(filas, on_conflict="id").execute()
    print(f"[DB] jobs upsert: {len(filas)} filas.")
    return len(filas)


def jobs_recientes(limit: int = 200) -> list:
    """Avisos del pool ordenados por fecha de scrapeo (para correr matches)."""
    res = (cliente().table("jobs").select("*")
           .order("scraped_at", desc=True).limit(limit).execute())
    return res.data or []


def keywords_de_perfiles() -> list:
    """Unión de todas las keywords guardadas en los perfiles (para el pool)."""
    res = cliente().table("profiles").select("keywords").execute()
    out = []
    for r in (res.data or []):
        for k in (r.get("keywords") or []):
            out.append(k)
    return out


# ---------------------------------------------------------------------------
# PROFILES — perfil guardado por usuario
# ---------------------------------------------------------------------------
def get_perfil(user_id: str) -> dict | None:
    res = (cliente().table("profiles").select("*")
           .eq("user_id", user_id).limit(1).execute())
    return (res.data or [None])[0]


def upsert_perfil(user_id: str, perfil: dict) -> dict:
    fila = {
        "user_id": user_id,
        "cv_texto": perfil.get("cv_texto", ""),
        "rubro": perfil.get("rubro", ""),
        "descripcion_rubro": perfil.get("descripcion_rubro", ""),
        "keywords": perfil.get("keywords_busqueda", []) or perfil.get("keywords", []),
        "ubicacion": perfil.get("ubicacion", "lima"),
        "modalidades": perfil.get("modalidades", []),
        "anios_experiencia": perfil.get("anios_experiencia", 0),
        "max_brecha_anios": perfil.get("max_brecha_anios", 2),
    }
    cliente().table("profiles").upsert(fila, on_conflict="user_id").execute()
    return fila


# ---------------------------------------------------------------------------
# MATCHES — veredicto por (usuario, aviso)
# ---------------------------------------------------------------------------
def ids_evaluados(user_id: str) -> set:
    """IDs con decisión terminal; los errores temporales se vuelven a intentar."""
    res = (cliente().table("matches").select("job_id,estado")
           .eq("user_id", user_id).execute())
    return {
        r["job_id"] for r in (res.data or [])
        if es_estado_terminal(r.get("estado"))
    }


def guardar_matches(user_id: str, avisos: list) -> int:
    filas = []
    for a in avisos:
        filas.append({
            "user_id": user_id,
            "job_id": str(a.get("id")),
            "aplica": bool(a.get("aplica")),
            "score": int(a.get("score", 0) or 0),
            "estado": a.get("estado", "Pendiente"),
            "motivo": a.get("motivo", ""),
            "keywords": a.get("keywords", []),
            "anios_requeridos": a.get("anios_requeridos"),
        })
    if not filas:
        return 0
    cliente().table("matches").upsert(filas, on_conflict="user_id,job_id").execute()
    print(f"[DB] matches upsert: {len(filas)} filas.")
    return len(filas)


# ---------------------------------------------------------------------------
# CVS — CVs generados por (usuario, aviso)
# ---------------------------------------------------------------------------
def guardar_cv(user_id: str, job_id: str, archivo_url: str, similitud=None) -> None:
    cliente().table("cvs").upsert({
        "user_id": user_id,
        "job_id": str(job_id),
        "archivo_url": archivo_url,
        "similitud": similitud,
    }, on_conflict="user_id,job_id").execute()


# ---------------------------------------------------------------------------
# STORAGE — CVs en un bucket PRIVADO, con enlaces temporales (firmados)
# ---------------------------------------------------------------------------
def _clave_valida(nombre: str) -> str:
    """Supabase Storage rechaza tildes/ñ/espacios y caracteres especiales en las
    rutas (error InvalidKey). Normaliza el nombre a ASCII seguro."""
    import re
    import unicodedata
    n = unicodedata.normalize("NFD", nombre)
    n = "".join(c for c in n if unicodedata.category(c) != "Mn")   # quita tildes
    n = n.replace("ñ", "n").replace("Ñ", "N")
    n = re.sub(r"[^A-Za-z0-9._-]+", "_", n)                        # resto -> _
    return re.sub(r"_+", "_", n).strip("_")


def subir_cv(ruta_local: str, persona: str, nombre_archivo: str = None) -> str:
    """Sube un .docx al bucket privado y devuelve su ruta interna en Storage.
    La ruta queda como '<persona>/<nombre_archivo>'. Requiere que el bucket
    config.BUCKET_CVS exista y sea privado."""
    import os
    nombre_archivo = nombre_archivo or os.path.basename(ruta_local)
    destino = f"{_clave_valida(persona)}/{_clave_valida(nombre_archivo)}"
    with open(ruta_local, "rb") as f:
        datos = f.read()
    try:
        cliente().storage.from_(config.BUCKET_CVS).upload(
            destino, datos,
            {"content-type": "application/vnd.openxmlformats-officedocument"
                             ".wordprocessingml.document",
             "upsert": "true"},
        )
    except Exception as e:
        print(f"[Storage] Error subiendo {destino}: {e}")
        return ""
    print(f"[Storage] Subido: {destino}")
    return destino


def enlace_temporal(ruta_storage: str, dias: int = None) -> str:
    """Genera un enlace FIRMADO y temporal para descargar el CV.
    dias: vigencia (por defecto config.CV_LINK_DIAS)."""
    if not ruta_storage:
        return ""
    dias = dias or config.CV_LINK_DIAS
    segundos = int(dias) * 24 * 3600
    try:
        res = cliente().storage.from_(config.BUCKET_CVS).create_signed_url(
            ruta_storage, segundos)
        # supabase-py devuelve dict con 'signedURL' o 'signedUrl' según versión
        return res.get("signedURL") or res.get("signedUrl") or ""
    except Exception as e:
        print(f"[Storage] Error generando enlace de {ruta_storage}: {e}")
        return ""


def subir_cv_y_registrar(ruta_local: str, persona: str, user_id: str,
                         job_id: str, similitud=None) -> str:
    """Sube el CV, guarda su ruta en la tabla 'cvs' y devuelve un enlace temporal."""
    ruta_storage = subir_cv(ruta_local, persona)
    if not ruta_storage:
        return ""
    if user_id:
        guardar_cv(user_id, job_id, ruta_storage, similitud)
    return enlace_temporal(ruta_storage)


def actualizar_descripcion(job_id: str, descripcion: str) -> None:
    """Cachea en el pool la descripción del detalle (para no re-scrapear)."""
    cliente().table("jobs").update({"descripcion": descripcion}) \
        .eq("id", str(job_id)).execute()


def matches_aplican(user_id: str) -> list:
    """Matches donde el usuario SÍ aplica (para mostrarle sus resultados)."""
    res = (cliente().table("matches").select("job_id, score, estado")
           .eq("user_id", user_id).eq("aplica", True)
           .order("score", desc=True).execute())
    return res.data or []
