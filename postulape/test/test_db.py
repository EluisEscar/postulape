"""
Prueba de conexión a Supabase: inserta 2 avisos de prueba en 'jobs' y los lee.
No scrapea ni usa el LLM. Sirve para validar credenciales y RLS/service key.

Uso (desde la carpeta que contiene 'postulape', junto a main.py):
    python -m postulape.test.test_db
"""

import time

from postulape import config
from postulape.storage import db


def main():
    print("SUPABASE_URL:", (config.SUPABASE_URL or "(vacío)")[:40], "...")
    print("Service key:", "OK" if config.SUPABASE_SERVICE_KEY else "FALTA")

    t = int(time.time())
    ejemplo = [
        {"id": f"test-{t}-1", "plataforma": "TEST", "titulo": "Aviso de prueba 1",
         "empresa": "Demo SAC", "ubicacion": "Lima", "distrito": "Miraflores",
         "departamento": "Lima", "modalidad": "Remoto", "antiguedad": "hoy",
         "descripcion": "Prueba de escritura en Supabase.", "link": "https://x.com/1"},
        {"id": f"test-{t}-2", "plataforma": "TEST", "titulo": "Aviso de prueba 2",
         "empresa": "Demo 2", "ubicacion": "Lima", "distrito": "Surco",
         "departamento": "Lima", "modalidad": "Híbrido", "antiguedad": "ayer",
         "descripcion": "Segunda prueba.", "link": "https://x.com/2"},
    ]

    print("\nInsertando 2 jobs de prueba...")
    n = db.agregar_jobs(ejemplo)
    print(f"Enviados: {n}")

    ids = db.cargar_ids_jobs()
    print(f"\nTotal de jobs en el pool: {len(ids)}")
    recientes = db.jobs_recientes(limit=5)
    print("Últimos avisos:")
    for j in recientes:
        print(f"  - [{j.get('plataforma')}] {j.get('titulo')}  ({j.get('id')})")

    print("\n✔ Si ves las filas TEST en la tabla 'jobs' de Supabase, la conexión funciona.")
    print("  (Bórralas luego para no ensuciar el pool.)")


if __name__ == "__main__":
    main()