"""
Prueba de conexión a Google Sheets: escribe 2 filas de ejemplo.
No scrapea ni usa Gemini.

Uso (desde la raíz del proyecto POSTULAPE/POSTULAPE):
    python -m postulape.test.test_sheets
"""

from postulape import config
from postulape.storage import sheets_store


def main():
    print("Backend credenciales:", config.GOOGLE_CREDENTIALS)
    print("Sheet:", config.SPREADSHEET_KEY or config.SPREADSHEET_NAME,
          "| worksheet:", config.WORKSHEET_NAME)

    # ids con timestamp para que no choquen con la deduplicación si repites
    import time
    t = int(time.time())

    ejemplo = [
        {
            "id": f"test-{t}-1",
            "plataforma": "TEST",
            "titulo": "Fila de prueba 1",
            "empresa": "Empresa Demo",
            "distrito": "Miraflores",
            "departamento": "Lima",
            "modalidad": "Remoto",
            "antiguedad": "hace 1 día",
            "descripcion": "Esto es una prueba de escritura en Google Sheets.",
            "link": "https://ejemplo.com/aviso1",
            "estado": "Pendiente",
        },
        {
            "id": f"test-{t}-2",
            "plataforma": "TEST",
            "titulo": "Fila de prueba 2 =SUMA(1;2)",  # prueba anti-fórmula
            "empresa": "Otra Demo",
            "distrito": "Surco",
            "departamento": "Lima",
            "modalidad": "Híbrido",
            "antiguedad": "hace 3 días",
            "descripcion": "Segunda fila de prueba.",
            "link": "https://ejemplo.com/aviso2",
            "estado": "Pendiente",
        },
    ]

    print("\nEscribiendo 2 filas de prueba...")
    resultado = sheets_store.agregar_avisos(ejemplo)
    print("Resultado:", resultado)

    print("\nIds actualmente en la hoja:")
    ids = sheets_store.cargar_ids_existentes()
    print(f"  total: {len(ids)}")
    for x in list(ids)[:5]:
        print("  -", x)

    print("\n✔ Si ves las 2 filas TEST en tu Google Sheet, la conexión funciona.")


if __name__ == "__main__":
    main()