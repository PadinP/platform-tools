"""Puente de persistencia: componente navegador WebADB -> Python Streamlit -> Supabase.

No envía secretos a JavaScript. Requiere RFAF_WRITE_ENABLED=true para escribir.
AVISO: para un despliegue público, usar además autenticación/autorización por usuario.
"""
from __future__ import annotations

import math
import hmac
from pathlib import Path

import streamlit as st
import streamlit.components.v1 as components

_COMPONENT = components.declare_component(
    "rfaf_webadb", path=str(Path(__file__).resolve().parent / "webadb")
)

SQL_COLS = {
    "temporada", "categoria_origen", "competicion_origen", "grupo_origen",
    "equipo_origen", "jugador", "año_nacimiento", "club_en_temporada",
    "estado", "convocados", "titular", "suplente", "jugados", "total_goles",
    "media_goles", "amarillas", "rojas", "doble_amarilla",
    "competiciones_temporada",
}
INT_COLS = {"año_nacimiento", "convocados", "titular", "suplente", "jugados",
            "total_goles", "amarillas", "rojas", "doble_amarilla"}
IDENTITY = ("temporada", "competicion_origen", "grupo_origen", "equipo_origen",
            "jugador", "año_nacimiento")


def _validated_row(payload):
    if not isinstance(payload, dict):
        raise ValueError("La fila recibida no es un objeto")
    if not SQL_COLS.issubset(payload):
        raise ValueError("Faltan campos de jugadores: " + ", ".join(sorted(SQL_COLS - payload.keys())))
    if any(key not in SQL_COLS for key in payload):
        raise ValueError("Campos no permitidos en jugadores")
    row = {}
    for key in SQL_COLS:
        v = payload[key]
        if key in INT_COLS:
            if v is None or v == "":
                row[key] = None
            else:
                number = float(v)
                if not math.isfinite(number) or not number.is_integer() or abs(number) > 10**9:
                    raise ValueError("Número fuera de rango: " + key)
                row[key] = int(number)
        elif key == "media_goles":
            number = None if v is None or v == "" else float(v)
            if number is not None and not math.isfinite(number):
                raise ValueError("Media de goles inválida")
            row[key] = number
        else:
            if not isinstance(v, str) or len(v) > 4000:
                raise ValueError("Texto inválido: " + key)
            row[key] = v
    if not row["jugador"].strip() or not row["equipo_origen"].strip():
        raise ValueError("El jugador y el equipo son obligatorios")
    if row["temporada"] not in ("26-27", "25-26", "24-25"):
        raise ValueError("Temporada desconocida")
    return row


def _validated_checkpoint(payload):
    if not isinstance(payload, dict):
        raise ValueError("Checkpoint no es un objeto")
    wanted = ("jugadores", "equipos", "partidos", "grupos")
    result = {}
    for name in wanted:
        arr = payload.get(name, [])
        if not isinstance(arr, list) or len(arr) > 500000 or any(
            not isinstance(v, str) or len(v) > 400 for v in arr
        ):
            raise ValueError("Lista de checkpoint inválida: " + name)
        result[name] = arr
    anonymous = payload.get("anonymous_rows", {})
    if not isinstance(anonymous, dict) or len(anonymous) > 500000 or any(
        not isinstance(k, str) or not isinstance(v, str) or len(k) > 500 or len(v) > 500
        for k, v in anonymous.items()
    ):
        raise ValueError("Jugadores sin nombre: datos inválidos")
    result["anonymous_rows"] = anonymous
    return result


def _load_checkpoint(api):
    response = api("GET", "/rest/v1/checkpoints_rfaf",
                   params={"select": "data", "id": "eq.principal", "limit": "1"})
    rows = response.json()
    raw = rows[0].get("data") if rows else {}
    if raw is None:
        raw = {}
    if isinstance(raw, str):
        import json
        raw = json.loads(raw)
    return _validated_checkpoint(raw)


def _write_row(api, row):
    params = {"select": "id", "limit": "1"}
    for col in IDENTITY:
        value = row[col]
        params[col] = "is.null" if value is None else "eq." + str(value)
    existing = api("GET", "/rest/v1/jugadores_rfaf", params=params).json()
    if existing:
        api("PATCH", "/rest/v1/jugadores_rfaf",
            params={"id": "eq." + str(existing[0]["id"])}, json=row)
    else:
        api("POST", "/rest/v1/jugadores_rfaf", json=row)


def _save_checkpoint(api, data):
    api("POST", "/rest/v1/checkpoints_rfaf",
        params={"on_conflict": "id"},
        headers={"Prefer": "resolution=merge-duplicates"},
        json={"id": "principal", "data": data})



ALLOWED_EVENTS = {"usb_disconnect", "usb_reconnect", "recovery_error", "scraper_error", "write_error", "complete"}

def _write_diagnostic(api, payload, session):
    """Only important events. Never store secrets, raw HTML/XML or a device serial."""
    if not isinstance(payload, dict):
        raise ValueError("Evento de diagnóstico inválido")
    code = payload.get("code")
    if code not in ALLOWED_EVENTS:
        raise ValueError("Código de evento no permitido")
    details = str(payload.get("details", ""))[:450]
    # Avoid persisting URLs, tokens or unnecessarily large raw exceptions.
    import re
    details = re.sub(r"https?://\S+", "[URL omitida]", details)
    details = re.sub(r"(?i)(sb_secret_|sk-[a-z0-9_-]+)[a-z0-9_-]*", "[credencial omitida]", details)
    api("POST", "/rest/v1/logs_scraper", json={
        "sesion": session[:100], "evento": code,
        "nivel": "ERROR" if code in ("recovery_error", "scraper_error", "write_error") else "INFO",
        "detalle": details,
    })


def _admin_panel(api):
    password = str(st.secrets.get("RFAF_ADMIN_PASSWORD", ""))
    with st.expander("Administración · Diagnóstico técnico", expanded=False):
        if not password:
            st.info("Para consultar los registros, configura RFAF_ADMIN_PASSWORD en Streamlit Secrets. No afecta a la actualización de jugadores.")
            return
        if not st.session_state.get("rfaf_admin_ok", False):
            submitted = st.text_input("Contraseña de administrador", type="password", key="rfaf_admin_input")
            if st.button("Ver diagnósticos", key="rfaf_admin_access"):
                if hmac.compare_digest(submitted, password):
                    st.session_state.rfaf_admin_ok = True
                    st.rerun(scope="fragment")
                else:
                    st.error("Acceso denegado")
            return
        if st.button("Actualizar diagnóstico", key="rfaf_refresh_errors"):
            pass
        if st.button("Probar registro de diagnóstico", key="rfaf_test_log"):
            try:
                _write_diagnostic(api, {"code":"complete", "details":"Prueba de diagnóstico realizada desde el administrador"}, "admin-test")
                st.success("Prueba guardada en Supabase. Pulsa Actualizar diagnóstico para verla.")
            except Exception as exc:
                st.error(f"No se pudo guardar el evento de prueba: {type(exc).__name__}. Revisa los permisos y la tabla logs_scraper.")
        try:
            rows = api("GET", "/rest/v1/logs_scraper", params={
                "select": "creado_en,nivel,evento,detalle,sesion", "order": "creado_en.desc", "limit": "150"
            }).json()
            import pandas as pd
            st.dataframe(pd.DataFrame(rows), height=260, hide_index=True, width="stretch")
            if rows:
                st.download_button("Descargar diagnósticos JSON", data=__import__("json").dumps(rows, ensure_ascii=False, indent=2),
                    file_name="rfaf_diagnosticos.json", mime="application/json", key="rfaf_logs_export")
        except Exception as exc:
            st.warning(f"No se pudo consultar logs_scraper ({type(exc).__name__}). Comprueba la tabla y los permisos de Supabase.")
        if st.button("Cerrar sesión de administrador", key="rfaf_admin_logout"):
            st.session_state.rfaf_admin_ok = False
            st.rerun(scope="fragment")

@st.fragment

def render_webadb(api):
    """Se rerenderiza solo este panel después de una escritura, no toda la web."""
    st.subheader("Actualización de jugadores")
    # Funciona igual con .streamlit/secrets.toml local y con Secrets de Streamlit Cloud.
    # El valor por defecto es seguro: no permitir escrituras sin autorización.
    write_configured = st.secrets.get("RFAF_WRITE_ENABLED", False) is True
    # Sin contraseña adicional: conserva la clave de Supabase en st.secrets.
    # ATENCIÓN: no habilitar esta opción en un despliegue público sin autorización
    # del lado del servidor y límites contra escrituras abusivas.
    enabled = write_configured
    if not enabled:
        st.warning("Escritura deshabilitada. Añade RFAF_WRITE_ENABLED = true en Secrets y reinicia Streamlit.")

    if "rfaf_webadb_checkpoint" not in st.session_state:
        try:
            st.session_state.rfaf_webadb_checkpoint = _load_checkpoint(api)
        except Exception as exc:
            st.error(f"No puedo leer el checkpoint: {exc}")
            return
    if "rfaf_webadb_ack" not in st.session_state:
        st.session_state.rfaf_webadb_ack = None

    event = _COMPONENT(
        checkpoint=st.session_state.rfaf_webadb_checkpoint,
        ack=st.session_state.rfaf_webadb_ack,
        write_enabled=enabled,
        key="rfaf_webadb_control",
        default=None,
    )
    _admin_panel(api)
    if not isinstance(event, dict):
        return
    event_id, event_type, session = event.get("id"), event.get("type"), event.get("session")
    if not isinstance(event_id, int) or event_id < 1 or not isinstance(session, str) or len(session) > 100:
        return
    ack = st.session_state.rfaf_webadb_ack
    if ack and ack.get("id") == event_id and ack.get("session") == session:
        return

    try:
        if not enabled:
            raise PermissionError("Escritura deshabilitada en Streamlit Secrets")
        if event_type == "diagnostic":
            _write_diagnostic(api, event.get("payload"), session)
        elif event_type == "player":
            _write_row(api, _validated_row(event.get("payload")))
        elif event_type == "checkpoint":
            cp = _validated_checkpoint(event.get("payload"))
            _save_checkpoint(api, cp)
            st.session_state.rfaf_webadb_checkpoint = cp
        else:
            raise ValueError("Tipo de operación no autorizado")
        st.session_state.rfaf_webadb_ack = {"session": session, "id": event_id, "ok": True}
    except Exception as exc:
        # No exponer información secreta; identificar el error por su tipo.
        import logging
        logging.getLogger("rfaf.diagnostico").warning("Error en operación %s: %s", event_type, type(exc).__name__)
        # Do not persist raw server exceptions that might contain request metadata.
        st.session_state.rfaf_webadb_ack = {"session": session, "id": event_id, "ok": False,
                                          "error": "Error de escritura/diagnóstico: " + type(exc).__name__}
    st.rerun(scope="fragment")
