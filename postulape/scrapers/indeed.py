"""
Scraper de Indeed Perú.

Indeed usa Cloudflare + su propio anti-bot. Playwright "de fábrica" es
detectado al instante en 2026. Aquí usamos PATCHRIGHT (fork de Playwright
parcheado, gratis, casi drop-in) + PERFIL PERSISTENTE, de modo que:
  - El navegador parece un Chrome real (menos fingerprints de automatización).
  - La cookie cf_clearance queda guardada en el perfil: pasas el challenge UNA
    vez (a mano si aparece) y las siguientes corridas entran directo.
  - Corres con IP residencial (tu casa) = el factor más importante para Cloudflare.

Instalación (una sola vez):
    pip install patchright
    patchright install chromium
    # channel="chrome" usa tu Chrome real instalado (recomendado). Si no tienes
    # Chrome, quita channel=... y usará el chromium de patchright.

URL (parámetros estables):
    https://pe.indeed.com/jobs?q={kw}&l={loc}&fromage={dias}&start={offset}
    start en múltiplos de 10; id estable = data-jk de la tarjeta.
"""

import time
import urllib.parse
import os

# Patchright expone la MISMA API que Playwright. Si no está instalado, cae a
# Playwright normal (que probablemente será bloqueado, pero no rompe el import).
try:
    from patchright.sync_api import sync_playwright
    _USANDO_PATCHRIGHT = True
except ImportError:
    from playwright.sync_api import sync_playwright
    _USANDO_PATCHRIGHT = False

from postulape.scrapers.base import separar_ubicacion

# Perfil persistente: guarda cookies/clearance entre corridas.
DIR_PERFIL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    ".perfil_indeed",
)


def construir_url(keyword, ubicacion, fromage=7, start=0):
    params = {"q": keyword, "l": ubicacion, "fromage": fromage, "start": start}
    return "https://pe.indeed.com/jobs?" + urllib.parse.urlencode(params)


def _hay_challenge(page) -> bool:
    """Detecta la pantalla de Cloudflare ('Un momento…', Turnstile, etc.)."""
    try:
        titulo = (page.title() or "").lower()
    except Exception:
        titulo = ""
    if any(t in titulo for t in ("just a moment", "un momento", "attention required")):
        return True
    # Widget de Cloudflare Turnstile o iframe de challenge
    return bool(page.query_selector("iframe[src*='challenges.cloudflare.com'], #challenge-form"))


def _esperar_paso_de_challenge(page, timeout_seg=90):
    """Si aparece Cloudflare, espera a que se resuelva. Con headless=False puedes
    resolverlo tú a mano en la ventana; con perfil persistente, la próxima vez
    normalmente ya no aparece."""
    if not _hay_challenge(page):
        return True
    print("[Indeed] ⚠️  Cloudflare detectado. Si ves un checkbox/CAPTCHA en la "
          "ventana, resuélvelo. Esperando hasta que pase...")
    limite = time.time() + timeout_seg
    while time.time() < limite:
        if not _hay_challenge(page):
            print("[Indeed] ✔ Challenge superado.")
            return True
        time.sleep(2)
    print("[Indeed] ✖ No se superó el challenge a tiempo.")
    return False


def _extraer_descripcion_detalle(page) -> str | None:
    """Extrae la descripción COMPLETA desde la página de detalle del aviso.
    El contenedor real es div.react-native-html-content.simple-job-description-html
    (confirmado en el DOM actual de Indeed PE). Como esa clase puede aparecer más
    de una vez (p. ej. el bloque de sueldo), nos quedamos con el texto más largo.
    Fallback: #jobDescriptionText (markup antiguo)."""
    candidatos = page.query_selector_all(
        "div.react-native-html-content.simple-job-description-html, #jobDescriptionText"
    )
    mejor, mejor_len = None, 0
    for c in candidatos:
        try:
            texto = c.inner_text().strip()
        except Exception:
            continue
        if len(texto) > mejor_len:
            mejor, mejor_len = texto, len(texto)
    return mejor


def _traer_descripcion(page, aviso, pausa_detalle) -> None:
    """Navega al detalle del aviso y rellena aviso['descripcion'] con el texto
    completo. Mantiene lo que ya había si algo falla."""
    if not aviso.get("link"):
        return
    try:
        page.goto(aviso["link"], wait_until="domcontentloaded", timeout=45000)
        if _hay_challenge(page):
            if not _esperar_paso_de_challenge(page):
                return
        page.wait_for_selector(
            "div.react-native-html-content.simple-job-description-html, #jobDescriptionText",
            timeout=10000,
        )
        desc = _extraer_descripcion_detalle(page)
        if desc:
            aviso["descripcion"] = desc
    except Exception as e:
        print(f"[Indeed] No se pudo traer descripción de {aviso['id']}: {e}")
    finally:
        time.sleep(pausa_detalle)


def extraer_avisos(page) -> list[dict]:
    avisos = []
    tarjetas = page.query_selector_all("div.job_seen_beacon, [data-testid='slider_item']")

    for tarjeta in tarjetas:
        link_el = tarjeta.query_selector("a[data-jk], h2 a, a.jcs-JobTitle")
        if not link_el:
            continue
        job_id = link_el.get_attribute("data-jk")
        href = link_el.get_attribute("href") or ""
        if not job_id:
            import re
            m = re.search(r"jk=([a-f0-9]+)", href)
            job_id = m.group(1) if m else href

        titulo_el = tarjeta.query_selector("h2 span[title], h2 a span, h2")
        titulo = titulo_el.inner_text().strip() if titulo_el else None

        empresa_el = tarjeta.query_selector("[data-testid='company-name'], span.companyName")
        empresa = empresa_el.inner_text().strip() if empresa_el else None

        ubi_el = tarjeta.query_selector("[data-testid='text-location'], div.companyLocation")
        ubicacion = ubi_el.inner_text().strip() if ubi_el else None

        modalidad = None
        if ubicacion:
            tl = ubicacion.lower()
            for k in ("remoto", "híbrido", "hibrido", "presencial"):
                if k in tl:
                    modalidad = ubicacion
                    break

        desc_el = tarjeta.query_selector("div.job-snippet, [class*='underShelfFooter'] li, ul li")
        descripcion = desc_el.inner_text().strip() if desc_el else None

        distrito, departamento = separar_ubicacion(ubicacion)
        link = f"https://pe.indeed.com/viewjob?jk={job_id}" if job_id else href

        avisos.append({
            "id": f"indeed-{job_id}",
            "plataforma": "Indeed",
            "titulo": titulo,
            "empresa": empresa,
            "ubicacion": ubicacion,
            "distrito": distrito,
            "departamento": departamento,
            "modalidad": modalidad,
            "antiguedad": None,
            "descripcion": descripcion,
            "link": link,
        })
    return avisos


def traer_descripciones(avisos, headless=False, pausa_detalle=2.0):
    """ETAPA 3 del embudo: entra al detalle SOLO de la lista de avisos dada
    (los sobrevivientes) y les rellena la descripción. Usa el perfil persistente."""
    if not avisos:
        return
    os.makedirs(DIR_PERFIL, exist_ok=True)
    with sync_playwright() as p:
        contexto = p.chromium.launch_persistent_context(
            user_data_dir=DIR_PERFIL,
            channel="chrome",
            headless=headless,
            no_viewport=True,
        )
        page = contexto.pages[0] if contexto.pages else contexto.new_page()
        for i, a in enumerate(avisos, 1):
            print(f"[Indeed] Descripción {i}/{len(avisos)}: {a.get('titulo')}")
            _traer_descripcion(page, a, pausa_detalle)
        contexto.close()


def scrapear_indeed(keyword, ubicacion="lima", fromage=7, headless=False, pausa=3.0,
                    max_paginas=20, traer_descripcion=True, pausa_detalle=2.0):
    if not _USANDO_PATCHRIGHT:
        print("[Indeed] ⚠️  Patchright NO está instalado; usando Playwright normal "
              "(Cloudflare probablemente bloqueará). Instala: pip install patchright")

    todos, ids_vistos = [], set()
    os.makedirs(DIR_PERFIL, exist_ok=True)

    with sync_playwright() as p:
        # Contexto persistente: NO seteamos user_agent ni headers custom —
        # patchright maneja el fingerprint mejor si lo dejas por defecto.
        contexto = p.chromium.launch_persistent_context(
            user_data_dir=DIR_PERFIL,
            channel="chrome",        # usa Chrome real; quítalo si no lo tienes
            headless=headless,
            no_viewport=True,
        )
        page = contexto.pages[0] if contexto.pages else contexto.new_page()

        for pagina in range(max_paginas):
            start = pagina * 10
            url = construir_url(keyword, ubicacion, fromage, start)
            print(f"[Indeed] Página {pagina + 1} (start={start}): {url}")
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=45000)
            except Exception as e:
                print(f"[Indeed] Error: {e}")
                break

            # Manejo de Cloudflare
            if _hay_challenge(page):
                if not _esperar_paso_de_challenge(page):
                    break

            try:
                page.wait_for_selector("div.job_seen_beacon", timeout=15000)
            except Exception:
                print("[Indeed] Sin resultados en esta página (o bloqueo persistente).")
                break

            nuevos = [a for a in extraer_avisos(page) if a["id"] not in ids_vistos]
            if not nuevos:
                print(f"[Indeed] Sin avisos nuevos en página {pagina + 1}, fin.")
                break
            for a in nuevos:
                ids_vistos.add(a["id"])
                todos.append(a)
            print(f"[Indeed] {len(nuevos)} nuevos en página {pagina + 1}.")

            # Traer la descripción completa desde el detalle de cada aviso nuevo.
            # (Esto navega una vez por aviso: más lento y más expuesto a Cloudflare,
            #  pero es la única forma de tener el texto completo para el LLM.)
            if traer_descripcion:
                for a in nuevos:
                    _traer_descripcion(page, a, pausa_detalle)
                # Volvemos al listado para la siguiente página.
                # (el goto del inicio del loop ya apunta a la página siguiente)

            time.sleep(pausa)

        contexto.close()
    return todos


if __name__ == "__main__":
    import sys
    kw = sys.argv[1] if len(sys.argv) > 1 else "ingeniero de software"
    res = scrapear_indeed(kw, "lima", fromage=7, headless=False, max_paginas=2)
    print(f"\nTotal: {len(res)}\n")
    for r in res[:10]:
        desc_len = len(r["descripcion"]) if r.get("descripcion") else 0
        print(f"- [{r['id']}] {r['titulo']} | {r['empresa']} | {r['distrito']} | desc={desc_len} chars")
        print(f"  {r['link']}")