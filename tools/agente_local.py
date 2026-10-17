"""Agente local RFAF para pruebas en el ordenador propietario.

Requiere Python y .streamlit/secrets.toml en la raíz del proyecto.
No distribuir con claves privilegiadas de Supabase.
El scraper v13 permanece sin modificaciones: el checkpoint depende de su lógica.
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
import tomllib
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

import requests

# Permite ejecutar desde la raíz o desde tools/.
HERE = Path(__file__).resolve().parent
BASE = HERE.parent if HERE.name.lower() == "tools" else HERE
CONFIG = BASE / ".streamlit" / "secrets.toml"
SCRAPER = BASE / "scraper" / "scrapp_v13.py"
POLL = 5
ADB_EXE = BASE / "tools" / ("adb.exe" if os.name == "nt" else "adb")
LOG_LIMIT = 12000  # Supabase guarda solo los últimos 12 KB de consola por trabajo.

if not CONFIG.exists():
    raise SystemExit(f"No se encuentra {CONFIG}. Configura los Secrets locales.")
config = tomllib.loads(CONFIG.read_text(encoding="utf-8"))
URL = str(config.get("SUPABASE_URL", "")).rstrip("/").removesuffix("/rest/v1")
KEY = str(config.get("SUPABASE_SECRET_KEY") or config.get("SUPABASE_SERVICE_ROLE_KEY") or "")
if not URL.startswith("https://") or not KEY:
    raise SystemExit("Faltan SUPABASE_URL o SUPABASE_SECRET_KEY en Secrets locales.")
HEADERS = {"apikey": KEY, "Content-Type": "application/json"}
if not KEY.startswith(("sb_secret_", "sb_publishable_")):
    HEADERS["Authorization"] = f"Bearer {KEY}"


def api(method, route, **kwargs):
    headers = dict(HEADERS)
    headers.update(kwargs.pop("headers", {}))
    resp = requests.request(method, URL + route, headers=headers, timeout=30, **kwargs)
    if not resp.ok:
        raise RuntimeError(f"Supabase HTTP {resp.status_code}: {resp.text[:400]}")
    return resp


def android_connected():
    """Comprobar conexión ADB sin capturas ni dumps XML."""
    try:
        adb = str(ADB_EXE) if ADB_EXE.is_file() else "adb"
        result = subprocess.run([adb, "devices"], capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=10)
        if result.returncode != 0:
            return False
        return any(line.strip().endswith("\tdevice")
                   for line in result.stdout.splitlines()[1:])
    except (OSError, subprocess.TimeoutExpired):
        return False


def stamp():
    return datetime.now(timezone.utc).isoformat()


def patch(job_id, payload, expected_state=None):
    params = {"id": f"eq.{job_id}"}
    if expected_state:
        params["estado"] = f"eq.{expected_state}"
    return api("PATCH", "/rest/v1/solicitudes_scraper",
               params=params, json=({**payload, "mensaje": with_heartbeat(payload["mensaje"])}
                                     if "mensaje" in payload else payload),
               headers={"Prefer": "return=representation"}).json()


def remote_state(job_id):
    rows = api("GET", "/rest/v1/solicitudes_scraper",
               params={"select": "estado", "id": f"eq.{job_id}", "limit": "1"}).json()
    return rows[0]["estado"] if rows else None


def with_heartbeat(message):
    # Marcador técnico eliminado al mostrar la consola en Streamlit.
    message = __import__("re").sub(r"(?:^|\n)\[RFAF_HEARTBEAT:[^\]\n]+\]", "", message)
    return message[-LOG_LIMIT:] + f"\n[RFAF_HEARTBEAT:{stamp()}]"


def tail_log(lines):
    return with_heartbeat("\n".join(lines))


def finish(job_id, status, logs, previous_state):
    payload = {"estado": status, "mensaje": tail_log(logs), "terminado_en": stamp()}
    # No sobrescribir una orden de detener aún pendiente.
    changed = patch(job_id, payload, expected_state=previous_state)
    return bool(changed)


def consume_output(pipe, events):
    try:
        for line in pipe:
            events.put(line.rstrip("\r\n"))
    finally:
        events.put(None)


def stop_process(proc):
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=8)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


def process_job(job):
    job_id = job["id"]
    claimed = patch(job_id, {"estado": "ejecutando", "iniciado_en": stamp(),
                             "terminado_en": None, "mensaje": "Iniciando scraper v13..."},
                    expected_state="pendiente")
    if not claimed:
        return

    logs = deque(maxlen=350)
    logs.append("Iniciando scraper v13...")
    if not android_connected():
        patch(job_id, {"estado": "pausado", "mensaje":
              "⏸ Móvil no detectado por ADB. Conecta el móvil y pulsa Reanudar."},
              expected_state="ejecutando")
        print("Móvil desconectado. Esperando reconexión.", flush=True)
        return
    last_log_push = 0.0
    last_check = 0.0
    proc = None
    stopped = False
    usb_paused = False
    usb_misses = 0
    last_usb_check = 0.0
    try:
        if not SCRAPER.exists():
            raise RuntimeError(f"No se encuentra {SCRAPER}")
        env = os.environ.copy()
        env.update({"PYTHONIOENCODING": "utf-8:replace",
                    "RFAF_SUPABASE_URL": URL,
                    "RFAF_SUPABASE_KEY": KEY})
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
        proc = subprocess.Popen([sys.executable, "-u", str(SCRAPER)], cwd=BASE,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, encoding="utf-8", errors="replace",
                                bufsize=1, env=env, creationflags=flags)
        events = queue.Queue()
        threading.Thread(target=consume_output, args=(proc.stdout, events), daemon=True).start()
        output_finished = False

        while not output_finished or proc.poll() is None:
            try:
                item = events.get(timeout=0.35)
                if item is None:
                    output_finished = True
                elif item:
                    print(item, flush=True)
                    logs.append(item)
            except queue.Empty:
                pass

            now = time.monotonic()
            if now - last_check >= 1.5:
                last_check = now
                try:
                    state = remote_state(job_id)
                    if state == "detener":
                        stopped = True
                        logs.append("Detención solicitada desde Streamlit.")
                        stop_process(proc)
                        break
                except Exception as ex:
                    print(f"No se pudo consultar orden de parada: {ex}", flush=True)

            if now - last_usb_check >= 3 and not stopped:
                last_usb_check = now
                if android_connected():
                    usb_misses = 0
                else:
                    usb_misses += 1
                    if usb_misses >= 2:
                        # Detener el proceso: v13 retomará desde su último checkpoint persistido.
                        # Confirmar antes si hubo una orden de parada manual.
                        if remote_state(job_id) == "detener":
                            stopped = True
                        else:
                            usb_paused = True
                        stop_process(proc)
                        break

            if now - last_log_push >= 2 and not stopped and not usb_paused:
                last_log_push = now
                try:
                    # No pisar estado='detener' con un mensaje del scraper.
                    patch(job_id, {"mensaje": tail_log(logs)}, expected_state="ejecutando")
                except Exception as ex:
                    print(f"Error al enviar consola: {ex}", flush=True)

        if stopped:
            # Al pulsar STOP, limpiar automáticamente la consola en SQL.
            finish(job_id, "detenido", ["⏹ Detenido por el usuario. Pulsa Reanudar para continuar."],
                   previous_state="detener")
            print("Scraper detenido. Consola limpiada.", flush=True)
        elif usb_paused:
            # No limpiar el log de desconexión: permitir diagnosticar por qué se pausó.
            logs.append("⏸ Móvil desconectado. Ejecución pausada; pulsa Reanudar tras conectarlo.")
            patch(job_id, {"estado": "pausado", "mensaje": tail_log(logs)},
                  expected_state="ejecutando")
            print("Móvil desconectado. Esperando reconexión.", flush=True)
        else:
            code = proc.wait()
            if code != 0:
                # Si el scraper termina antes de que la vigilancia periódica detecte
                # el cable desconectado, distinguirlo de un error ordinario.
                if not android_connected():
                    logs.append("⏸ Conexión USB/ADB perdida. Ejecución pausada; pulsa Reanudar.")
                    if remote_state(job_id) == "detener":
                        finish(job_id, "detenido", ["⏹ Detenido por el usuario. Pulsa Reanudar para continuar."], previous_state="detener")
                    else:
                        patch(job_id, {"estado": "pausado", "mensaje": tail_log(logs)}, expected_state="ejecutando")
                    print("USB desconectado. En pausa hasta reconectar.", flush=True)
                    return
                raise RuntimeError(f"Scraper terminó con código {code}.")
            logs.append("Scraper finalizado. Jugadores y checkpoint gestionados por v13.")
            # Resolver una orden 'detener' que haya llegado justo al terminar.
            if not finish(job_id, "completado", logs, previous_state="ejecutando"):
                if remote_state(job_id) == "detener":
                    logs.append("La ejecución ya había finalizado cuando llegó la detención.")
                    finish(job_id, "completado", logs, previous_state="detener")
            print("Sincronización finalizada.", flush=True)
    except Exception as exc:
        logs.append(f"ERROR: {exc}")
        print(f"ERROR: {exc}", flush=True)
        if proc is not None and proc.poll() is None:
            stop_process(proc)
        try:
            if not finish(job_id, "error", logs, previous_state="ejecutando"):
                if remote_state(job_id) == "detener":
                    finish(job_id, "detenido", ["⏹ Detenido por el usuario. Pulsa Reanudar para continuar."],
                           previous_state="detener")
        except Exception as failure:
            print(f"No se pudo actualizar el estado: {failure}", flush=True)


def latest_job():
    rows = api("GET", "/rest/v1/solicitudes_scraper",
        params={"select": "id,estado,mensaje", "order": "creado_en.desc", "limit": "1"}).json()
    return rows[0] if rows else None


def agent_heartbeat():
    job = latest_job()
    if job is None:
        # Solo Streamlit crea una solicitud. El agente espera sin generar filas.
        return
    patch(job["id"], {"mensaje": with_heartbeat(job.get("mensaje") or "")},
          expected_state=job["estado"])


def recover_after_restart():
    job = latest_job()
    if not job:
        return
    if job["estado"] == "detener":
        patch(job["id"], {"estado": "detenido", "terminado_en": stamp(),
              "mensaje": "⏹ Agente reiniciado. Ejecución anterior cerrada; consola limpia."},
              expected_state="detener")
    elif job["estado"] == "ejecutando":
        # El proceso pertenece a la sesión previa. NO reanudar por cuenta propia.
        patch(job["id"], {"estado": "pausado", "mensaje":
              "⚠️ El agente anterior dejó de responder. Comprueba que no exista otro scraper activo y pulsa Reanudar.",
              "terminado_en": stamp()}, expected_state="ejecutando")


def main():
    print("Agente RFAF activo. Esperando órdenes de Streamlit.", flush=True)
    recover_after_restart()
    while True:
        try:
            job = latest_job()
            if job and job["estado"] == "pendiente":
                process_job(job)
            else:
                agent_heartbeat()
                time.sleep(POLL)
        except Exception as exc:
            print(f"Comunicación: {exc}", flush=True)
            time.sleep(10)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Agente detenido.")
