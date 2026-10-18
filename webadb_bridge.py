"""Puente de persistencia: componente navegador WebADB -> Python Streamlit -> Supabase.

No envía secretos a JavaScript. Requiere RFAF_WRITE_ENABLED=true para escribir.
AVISO: para un despliegue público, usar además autenticación/autorización por usuario.
"""
from __future__ import annotations

import math
import hmac
import re
import logging
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



def _safe_http_error(exc):
    """Devuelve el estado HTTP y los campos no secretos del error PostgREST.

    No imprime URL, cabeceras, tokens ni volcados de la petición.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return f"{type(exc).__name__}: no se obtuvo respuesta HTTP (revisa conexión y servidor)"
    status = getattr(response, "status_code", None)
    desc = {400: "Petición inválida / columna o política incompatible",
            401: "Clave o autenticación incorrecta",
            403: "Permisos insuficientes / políticas RLS",
            404: "Ruta o tabla no encontrada",
            409: "Conflicto de restricción o registro",
            500: "Error interno de Supabase"}.get(status, "Error HTTP")
    safe = []
    try:
        payload = response.json()
        if isinstance(payload, dict):
            for field in ("code", "message", "hint", "details"):
                value = payload.get(field)
                if isinstance(value, (str, int, float)) and value:
                    safe.append(f"{field}: {value}")
    except (ValueError, TypeError, AttributeError):
        pass
    detail = " | ".join(safe)[:800]
    # Defense in depth: redact keys, URLs and bearer tokens from server messages.
    detail = re.sub(r"(?i)\b(?:sb_secret_|sb_publishable_|sk-[a-z0-9_-]+)[a-z0-9_-]*", "[credencial omitida]", detail)
    detail = re.sub(r"(?i)bearer\s+\S+", "Bearer [credencial omitida]", detail)
    detail = re.sub(r"https?://\S+", "[URL omitida]", detail)
    return f"HTTP {status} — {desc}" + (f" | {detail}" if detail else "")


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


def _friendly_incident(row):
    """Traduce eventos técnicos sin perder el detalle original."""
    code = row.get("evento", "")
    titles = {
        "usb_disconnect": ("Móvil desconectado", "warning", "Conecta el móvil por USB y abre la app RFAF."),
        "usb_reconnect": ("Conexión recuperada", "success", "El móvil volvió a conectarse."),
        "recovery_error": ("No se pudo reanudar", "error", "Abre la app RFAF e intenta reanudar la actualización."),
        "scraper_error": ("Error durante la actualización", "error", "Revisa los detalles técnicos de esta incidencia."),
        "write_error": ("Error al guardar los jugadores", "error", "Revisa la conexión con Supabase."),
        "complete": ("Actualización finalizada", "success", "El proceso informó que terminó."),
    }
    if row.get("sesion") == "admin-test":
        return "Prueba de diagnóstico", "success", "El diagnóstico pudo escribir en Supabase."
    return titles.get(code, ("Evento de diagnóstico", "info", "Consulta los detalles técnicos."))


def _group_incidents(rows):
    """Agrupa avisos USB duplicados de la misma sesión en <=15 s."""
    from datetime import datetime
    ordered = sorted(rows, key=lambda r: r.get("creado_en") or "")
    grouped = []
    for row in ordered:
        current = dict(row)
        try:
            current["_dt"] = datetime.fromisoformat(str(row.get("creado_en", "")).replace("Z", "+00:00"))
        except ValueError:
            current["_dt"] = None
        if (grouped and current.get("evento") == "usb_disconnect" and
            grouped[-1].get("evento") == "usb_disconnect" and
            grouped[-1].get("sesion") == current.get("sesion") and
            current["_dt"] is not None and grouped[-1]["_dt"] is not None and
            0 <= (current["_dt"] - grouped[-1]["_dt"]).total_seconds() <= 15):
            grouped[-1]["_duplicates"] += 1
            grouped[-1]["_dt"] = current["_dt"]
            grouped[-1]["creado_en"] = current.get("creado_en")
            grouped[-1]["detalle"] = current.get("detalle")
        else:
            current["_duplicates"] = 1
            grouped.append(current)
    return list(reversed(grouped))


def _incident_history(api):
    """Consulta el historial al abrirse el panel y en los reruns de su fragmento."""
    try:
        rows = api("GET", "/rest/v1/logs_scraper", params={
            "select": "creado_en,nivel,evento,detalle,sesion",
            "order": "creado_en.desc", "limit": "200",
        }).json()
        if not isinstance(rows, list):
            raise ValueError("Supabase no devolvió una lista de incidencias")
    except Exception as exc:
        st.warning("No se pudo consultar el diagnóstico: " + _safe_http_error(exc))
        return

    incidents = [r for r in _group_incidents(rows) if r.get("sesion") != "admin-test"]
    errors = sum(r.get("nivel") == "ERROR" for r in incidents)
    disconnects = sum(r.get("evento") == "usb_disconnect" for r in incidents)
    recovered = sum(r.get("evento") == "usb_reconnect" for r in incidents)
    c1, c2, c3 = st.columns(3)
    c1.metric("Errores", errors)
    c2.metric("Desconexiones", disconnects)
    c3.metric("Reconexiones", recovered)
    st.caption("Últimos 200 eventos consultados; los avisos USB repetidos se agrupan.")
    if not incidents:
        st.info("Todavía no hay incidencias registradas.")
        return
    st.markdown("**Últimas incidencias**")
    from datetime import datetime
    from zoneinfo import ZoneInfo
    for idx, incident in enumerate(incidents[:20]):
        name, kind, explanation = _friendly_incident(incident)
        try:
            dt = datetime.fromisoformat(str(incident.get("creado_en", "")).replace("Z", "+00:00"))
            stamp = dt.astimezone(ZoneInfo("Europe/Madrid")).strftime("%d/%m/%Y %H:%M:%S")
        except (ValueError, KeyError, OverflowError):
            stamp = str(incident.get("creado_en", ""))
        symbol = {"warning": "⚠️", "error": "🔴", "success": "✅"}.get(kind, "ℹ️")
        with st.container(border=True):
            st.markdown(f"**{symbol} {name}** · {stamp}")
            st.write(explanation)
            with st.expander("Ver detalles técnicos", expanded=False):
                st.code("\n".join([
                    f"Fecha: {stamp}",
                    f"Evento: {incident.get('evento', '')}",
                    f"Nivel: {incident.get('nivel', '')}",
                    f"Detalle: {incident.get('detalle', '')}",
                    f"Sesión: {incident.get('sesion', '')}",
                ]), language="text")
                if incident.get("_duplicates", 1) > 1:
                    st.caption(f"{incident['_duplicates']} avisos agrupados en esta incidencia")


def _admin_panel(api):
    password = str(st.secrets.get("RFAF_ADMIN_PASSWORD", ""))
    with st.expander("Administración · Diagnóstico técnico", expanded=False):
        if not password:
            st.info("Para consultar el diagnóstico, configura RFAF_ADMIN_PASSWORD en Streamlit Secrets. Esto no afecta a la actualización.")
            return
        if not st.session_state.get("rfaf_admin_ok", False):
            submitted = st.text_input("Contraseña de administrador", type="password", key="rfaf_admin_input")
            if st.button("Entrar al diagnóstico", key="rfaf_admin_access"):
                if hmac.compare_digest(submitted, password):
                    st.session_state.rfaf_admin_ok = True
                    st.rerun(scope="fragment")
                else:
                    st.error("Acceso denegado")
            return
        _incident_history(api)
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
        logging.getLogger("rfaf.diagnostico").warning("Fallo en operación %s: %s", event_type, _safe_http_error(exc))
        # Do not persist raw server exceptions that might contain request metadata.
        st.session_state.rfaf_webadb_ack = {"session": session, "id": event_id, "ok": False,
                                          "error": "Error de escritura/diagnóstico: " + _safe_http_error(exc)}
    st.rerun(scope="fragment")
