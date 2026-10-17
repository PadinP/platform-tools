"""Puente RFAF: utiliza los Secrets locales de Streamlit, sin .env.

Uso: python agente_local.py (mantener abierto en el PC conectado al móvil).
Esta autenticación aprovecha la configuración local solo para pruebas en TU PC.
No distribuir este archivo con Secrets a clientes. Para otros PC, autorización limitada.
"""
from __future__ import annotations

import subprocess
import sys
import time
import tomllib
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import requests

BASE = Path(__file__).resolve().parent
CONFIG = BASE / ".streamlit" / "secrets.toml"
SCRAPER = BASE / "scraper" / "scrapp_v13.py"
POLL = 5

if not CONFIG.exists():
    raise SystemExit("Primero configura .streamlit/secrets.toml (el que ya usa Streamlit local).")
config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
URL = str(config.get("SUPABASE_URL", "")).rstrip("/").removesuffix("/rest/v1")
KEY = str(config.get("SUPABASE_SECRET_KEY") or config.get("SUPABASE_SERVICE_ROLE_KEY") or "")
if not URL.startswith("https://") or not KEY:
    raise SystemExit("Secrets locales incompletos: SUPABASE_URL y SUPABASE_SECRET_KEY")
HEADERS = {"apikey": KEY, "Content-Type": "application/json"}
if not KEY.startswith(("sb_secret_", "sb_publishable_")):
    HEADERS["Authorization"] = f"Bearer {KEY}"


def api(method, route, **kwargs):
    headers = dict(HEADERS)
    headers.update(kwargs.pop("headers", {}))
    resp = requests.request(method, URL + route, headers=headers, timeout=90, **kwargs)
    if not resp.ok:
        raise RuntimeError(f"Supabase HTTP {resp.status_code}: {resp.text[:600]}")
    return resp


def update(job_id, state, lines, finished=False):
    data = {"estado": state, "mensaje": "\n".join(lines)[-1100:]}
    if finished:
        data["terminado_en"] = datetime.now(timezone.utc).isoformat()
    api("PATCH", "/rest/v1/solicitudes_scraper",
        params={"id": f"eq.{job_id}"}, json=data)



def procesar(job):
    job_id = job["id"]
    claimed = api("PATCH", "/rest/v1/solicitudes_scraper",
        params={"id": f"eq.{job_id}", "estado": "eq.pendiente"},
        headers={"Prefer": "return=representation"},
        json={"estado": "ejecutando", "iniciado_en": datetime.now(timezone.utc).isoformat(),
              "mensaje": "Iniciando scraper v13..."}).json()
    if not claimed:
        return
    history = deque(maxlen=12)
    last_sent = 0.0
    def show(line, force=False):
        nonlocal last_sent
        line = line.strip()
        if line:
            print(line, flush=True)
            history.append(line)
        if force or time.monotonic() - last_sent >= 2:
            update(job_id, "ejecutando", history)
            last_sent = time.monotonic()
    try:
        if not SCRAPER.exists():
            raise RuntimeError(f"No se encuentra {SCRAPER}")
        import os
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8:replace"
        env["RFAF_SUPABASE_URL"] = URL
        env["RFAF_SUPABASE_KEY"] = KEY
        proc = subprocess.Popen([sys.executable, "-u", str(SCRAPER)],
                                cwd=BASE, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                encoding="utf-8", errors="replace", bufsize=1, env=env)
        for line in proc.stdout:
            show(line)
        code = proc.wait()
        if code != 0:
            raise RuntimeError(f"Scraper terminó con código {code}.")
        history.append("Scraper finalizado. Jugadores y checkpoint ya guardados en SQL.")
        update(job_id, "completado", history, finished=True)
        print("Sincronización en Supabase confirmada.", flush=True)
    except Exception as exc:
        history.append(f"ERROR: {exc}")
        print(f"ERROR: {exc}", flush=True)
        try:
            update(job_id, "error", history, finished=True)
        except Exception as failure:
            print(f"No se pudo marcar error: {failure}", flush=True)


def main():
    print("Agente RFAF activo. Conecta Android por USB y deja abierta FutbolAragon.", flush=True)
    while True:
        try:
            jobs = api("GET", "/rest/v1/solicitudes_scraper",
                params={"select": "id,estado", "estado": "eq.pendiente",
                        "order": "creado_en.asc", "limit": "1"}).json()
            if jobs:
                procesar(jobs[0])
            else:
                time.sleep(POLL)
        except Exception as exc:
            print(f"Comunicación: {exc}", flush=True)
            time.sleep(10)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Agente detenido.")
