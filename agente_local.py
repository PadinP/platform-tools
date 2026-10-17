"""Agente RFAF para Windows, sin credenciales administrativas de Supabase.

Primer inicio: introducir un código de vinculación generado en Streamlit.
El token personal se guarda en el Administrador de credenciales de Windows
mediante keyring. No subir el token ni las claves de Supabase a GitHub.

Uso: python agente_local.py
Dependencias: requests, keyring (ver requirements-agente.txt)
"""

from __future__ import annotations

import socket
import subprocess
import sys
import time
from pathlib import Path

import keyring
import requests

BASE = Path(__file__).resolve().parent
SUPABASE_URL = "https://cesntzjhgvxbefmmjblk.supabase.co"  # URL pública, no es una clave.
CREDENTIAL_SERVICE = "RFAF-Scraper-Agent"
POLL_SECONDS = 10


class AgentAPIError(RuntimeError):
    pass


def obtener_url() -> str:
    """URL pública de Supabase; no requiere .env ni claves privadas."""
    return SUPABASE_URL.rstrip("/")


class Agent:
    def __init__(self, supabase_url: str):
        self.supabase_url = supabase_url
        self.endpoint = f"{supabase_url}/functions/v1/rfaf-agent"
        self.token = keyring.get_password(CREDENTIAL_SERVICE, supabase_url)
        self.agent_id = None

    def llamar(self, accion: str, **campos):
        try:
            r = requests.post(
                self.endpoint,
                json={"action": accion, **({"token": self.token} if self.token else {}), **campos},
                timeout=35,
            )
            r.raise_for_status()
            return r.json()
        except requests.HTTPError as exc:
            mensaje = r.text[:350] if r is not None else str(exc)
            raise AgentAPIError(f"HTTP {r.status_code}: {mensaje}") from exc
        except (requests.RequestException, ValueError) as exc:
            raise AgentAPIError(f"Error de comunicación con Supabase: {exc}") from exc

    def vincular(self):
        if not self.token:
            print("Este ordenador todavía no está vinculado.")
            print("Abre Streamlit > Administrar ordenadores > Generar código temporal.")
            codigo = input("Pega aquí el código de vinculación: ").strip()
            if not codigo:
                raise SystemExit("No se introdujo ningún código.")
            result = self.llamar(
                "pair", code=codigo, name=socket.gethostname()[:80]
            )
            token = result["token"]
            # Se almacena en el almacén de credenciales del usuario de Windows.
            # No se crea ningún archivo de secretos en la carpeta del repositorio.
            try:
                keyring.set_password(CREDENTIAL_SERVICE, self.supabase_url, token)
            except Exception as exc:
                raise SystemExit(f"No se pudo guardar el token en Windows: {exc}") from exc
            self.token = token
            print(f"Ordenador vinculado: {result['name']}")
        try:
            estado = self.llamar("heartbeat")
        except AgentAPIError as exc:
            raise SystemExit(
                f"No se pudo autenticar el agente: {exc}. "
                "Si ha sido revocado, elimina su credencial guardada y vuelve a vincularlo."
            ) from exc
        self.agent_id = estado["agent_id"]
        print(f"Agente autorizado: {estado['name']} (ID {self.agent_id})")

    def ejecutar_scraper(self, job_id: str):
        scraper = BASE / "scraper" / "scrapp_v13.py"
        adb = BASE / "tools" / "adb.exe"
        if not scraper.is_file():
            raise FileNotFoundError(f"Falta el scraper: {scraper}")
        if not adb.is_file():
            raise FileNotFoundError(
                f"Falta {adb}. Conserva la carpeta tools/ del repositorio."
            )

        log_path = BASE / "scraper_ultimo.log"
        print(f"[{job_id}] Scraper iniciado. Log: {log_path}", flush=True)
        with log_path.open("w", encoding="utf-8") as log:
            proceso = subprocess.Popen(
                [sys.executable, "-u", str(scraper)],
                cwd=str(BASE),
                stdout=log,
                stderr=subprocess.STDOUT,
            )
            ultimo_pulso = 0.0
            try:
                while proceso.poll() is None:
                    time.sleep(5)
                    if time.monotonic() - ultimo_pulso > 30:
                        try:
                            self.llamar("heartbeat")
                        except AgentAPIError as exc:
                            print(f"Aviso: heartbeat fallido: {exc}", flush=True)
                        ultimo_pulso = time.monotonic()
            except KeyboardInterrupt:
                proceso.terminate()
                try:
                    proceso.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proceso.kill()
                    proceso.wait()
                raise RuntimeError("Scraping interrumpido manualmente")

        if proceso.returncode != 0:
            tail = log_path.read_text(encoding="utf-8", errors="replace")[-850:]
            raise RuntimeError(f"Scraper terminó con código {proceso.returncode}. {tail}")

        csv_path = BASE / "jugadores_rfaf.csv"
        if not csv_path.is_file() or csv_path.stat().st_size < 100:
            raise RuntimeError("No se generó un CSV válido para publicar.")
        return csv_path

    def publicar(self, job_id: str, ruta: Path):
        # El servidor entrega una URL firmada y limitada a este trabajo.
        # No se envía ninguna clave service_role al ordenador.
        resp = self.llamar("upload_url", job_id=job_id)
        with ruta.open("rb") as archivo:
            respuesta = requests.put(
                resp["upload_url"],
                data=archivo,
                headers={"Content-Type": "text/csv; charset=utf-8"},
                timeout=180,
            )
        if respuesta.status_code >= 400:
            raise RuntimeError(f"Error HTTP {respuesta.status_code} al subir el CSV")
        self.llamar("finish", job_id=job_id, bytes=ruta.stat().st_size)

    def procesar(self, job: dict):
        job_id = job["id"]
        try:
            ruta = self.ejecutar_scraper(job_id)
            self.publicar(job_id, ruta)
            print(f"[{job_id}] CSV publicado. Trabajo completado.", flush=True)
        except Exception as exc:
            print(f"[{job_id}] ERROR: {exc}", flush=True)
            try:
                self.llamar("fail", job_id=job_id, message=str(exc)[:1000])
            except AgentAPIError as api_exc:
                print(f"No se pudo reportar el error: {api_exc}", flush=True)

    def iniciar(self):
        self.vincular()
        print("Agente activo. Ctrl+C para detener. Abre la app FutbolAragon en el móvil.")
        ultimo_pulso = 0.0
        while True:
            try:
                if time.monotonic() - ultimo_pulso >= 30:
                    self.llamar("heartbeat")
                    ultimo_pulso = time.monotonic()
                respuesta = self.llamar("claim")
                job = respuesta.get("job")
                if job:
                    self.procesar(job)
                else:
                    time.sleep(POLL_SECONDS)
            except AgentAPIError as exc:
                print(f"Comunicación interrumpida: {exc}", flush=True)
                time.sleep(15)


def main():
    try:
        Agent(obtener_url()).iniciar()
    except KeyboardInterrupt:
        print("\nAgente detenido.")


if __name__ == "__main__":
    main()
