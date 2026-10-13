"""Puente entre Supabase y el scraper Android ejecutado en Windows.

Arrancar en la raíz del proyecto: python agente_local.py
Requiere .env con SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY.
"""
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE = Path(__file__).resolve().parent
load_dotenv(BASE / '.env')
URL = os.getenv('SUPABASE_URL', '').rstrip('/')
KEY = os.getenv('SUPABASE_SERVICE_ROLE_KEY', '')
POLL_SECONDS = 10

if not URL or not KEY:
    raise SystemExit('Faltan SUPABASE_URL y SUPABASE_SERVICE_ROLE_KEY en .env')

HEADERS = {'apikey': KEY, 'Authorization': f'Bearer {KEY}', 'Content-Type': 'application/json'}


def api(method, endpoint, **kwargs):
    response = requests.request(method, URL + endpoint, headers=HEADERS, timeout=30, **kwargs)
    response.raise_for_status()
    return response


def update_job(job_id, status, message):
    api('PATCH', f'/rest/v1/solicitudes_scraper?id=eq.{job_id}', json={
        'estado': status,
        'mensaje': message[:1200],
        **({'iniciado_en': datetime.now().astimezone().isoformat()} if status == 'ejecutando' else {}),
        **({'terminado_en': datetime.now().astimezone().isoformat()} if status in ('completado', 'error') else {}),
    })


def publish_csv():
    path = BASE / 'jugadores_rfaf.csv'
    if not path.is_file() or path.stat().st_size == 0:
        raise RuntimeError('El scraper no produjo jugadores_rfaf.csv con contenido.')
    headers = dict(HEADERS)
    headers.update({'Content-Type': 'text/csv; charset=utf-8', 'x-upsert': 'true'})
    r = requests.put(URL + '/storage/v1/object/futbolapp-data/jugadores_rfaf.csv',
                     data=path.read_bytes(), headers=headers, timeout=120)
    r.raise_for_status()
    return path.stat().st_size


def process_job(job):
    job_id = job['id']
    # Reserva condicional: evita que varios agentes ejecuten el mismo trabajo.
    r = api('PATCH', f'/rest/v1/solicitudes_scraper?id=eq.{job_id}&estado=eq.pendiente',
            headers={**HEADERS, 'Prefer': 'return=representation'},
            json={'estado': 'ejecutando', 'mensaje': 'Iniciando scraper en Windows',
                  'iniciado_en': datetime.now().astimezone().isoformat()})
    if not r.json():
        return

    try:
        scraper = BASE / 'scraper' / 'scrapp_v13.py'
        if not scraper.exists():
            raise FileNotFoundError(f'No existe {scraper}')
        log_path = BASE / 'scraper_ultimo.log'
        print(f'[{job_id}] Ejecutando el scraper. Log: {log_path}', flush=True)
        with log_path.open('w', encoding='utf-8') as log:
            completed = subprocess.run([sys.executable, '-u', str(scraper)],
                                       cwd=BASE, stdout=log, stderr=subprocess.STDOUT,
                                       check=False)
        if completed.returncode != 0:
            tail = log_path.read_text(encoding='utf-8', errors='replace')[-900:]
            raise RuntimeError(f'Scraper terminó con código {completed.returncode}. {tail}')
        size = publish_csv()
        update_job(job_id, 'completado', f'CSV actualizado en Supabase: {size:,} bytes')
        print('Scraping finalizado y CSV publicado.', flush=True)
    except Exception as exc:
        print(f'Error en solicitud {job_id}: {exc}', flush=True)
        try:
            update_job(job_id, 'error', str(exc))
        except Exception as update_exc:
            print(f'No se pudo comunicar el error a Supabase: {update_exc}', flush=True)


def main():
    print('Agente local FutbolApp: esperando solicitudes. Ctrl+C para terminar.')
    while True:
        try:
            jobs = api('GET', '/rest/v1/solicitudes_scraper?select=id,estado&estado=eq.pendiente&order=creado_en.asc&limit=1').json()
            if jobs:
                process_job(jobs[0])
            else:
                time.sleep(POLL_SECONDS)
        except requests.RequestException as exc:
            print(f'Problema de conexión con Supabase: {exc}', flush=True)
            time.sleep(15)


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('\nAgente detenido.')
