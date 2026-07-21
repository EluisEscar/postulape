# PostulaPe

Automatiza la búsqueda, filtrado y evaluación de ofertas laborales de Indeed,
Computrabajo y Bumeran. Opcionalmente usa un LLM para comparar cada oferta con
el CV base y generar un CV adaptado.

## Estructura

```text
postulape/
├── config.py              # búsqueda, filtros, LLM y rutas
├── cli.py                 # orquestación del flujo completo
├── scrapers/              # extracción y utilidades compartidas
├── services/              # cliente LLM, matcher y generación de CV
└── storage/               # persistencia en Excel
main.py                    # punto de entrada compatible
cv_base.md                 # CV fuente del candidato
output/                    # Excel y CV generados
```

## Instalación

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
patchright install chromium
```

Configura el proveedor y su clave en variables de entorno, por ejemplo:

```powershell
$env:LLM_PROVIDER = "openrouter"
$env:OPENROUTER_API_KEY = "tu_clave"
```

Los filtros, plataformas, palabras de búsqueda y modelo se ajustan en
`postulape/config.py`.

## Uso

```powershell
python main.py
python main.py --solo-scrape
python main.py --keywords "practicante,qa"
```

También se puede ejecutar como módulo con `python -m postulape.cli`.
