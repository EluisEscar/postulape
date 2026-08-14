# PostulaPe

Automatiza la búsqueda, filtrado y evaluación de ofertas laborales de Indeed,
Computrabajo y Bumeran. Opcionalmente usa Gemini o Groq para comparar cada
oferta con el CV base y generar un CV adaptado.

## Estructura

```text
postulape/
├── config.py              # búsqueda, filtros, LLM y rutas
├── cli.py                 # orquestación del flujo completo
├── scrapers/              # extracción y utilidades compartidas
├── services/              # cliente LLM, matcher y generación de CV
└── storage/               # Google Sheets, Excel y Supabase
personas/                   # CV y configuración por persona
main.py                    # punto de entrada compatible
cv_base.md                 # CV fuente del candidato
output/                    # CV y archivos locales generados
```

## Instalación

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
patchright install chromium
```

Coloca `credentials.json` de la cuenta de servicio en la raíz, comparte el
Google Sheet con esa cuenta y configura el proveedor LLM en `.env`, por ejemplo:

```powershell
$env:LLM_PROVIDER = "gemini"
$env:GEMINI_API_KEY = "tu_clave"
$env:SPREADSHEET_KEY = "id_del_google_sheet"
```

Los filtros, plataformas, palabras de búsqueda y modelo se ajustan en
`postulape/config.py`.

## Uso

```powershell
python main.py
python main.py --solo-scrape
python main.py --keywords "practicante,qa"
python main.py --persona esteban --paginas 2
```

También se puede ejecutar como módulo con `python -m postulape.cli`.

`--solo-scrape` deja las ofertas como pendientes. Una ejecución posterior sin
esa opción puede evaluarlas y actualizar la misma fila, sin duplicarla.
