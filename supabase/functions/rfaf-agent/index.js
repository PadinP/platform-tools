// RFAF - Edge Function para vincular dispositivos y procesar trabajos.
// Las credenciales administrativas se usan SOLO aquí, nunca en Windows.
// La función es públicamente invocable, pero sus operaciones están protegidas
// por códigos temporales aleatorios o tokens revocables por dispositivo.
import { createClient } from "https://esm.sh/@supabase/supabase-js@2.58.0";

const PROJECT_URL = Deno.env.get("SUPABASE_URL")?.replace(/\/$/, "");
function adminKey() {
  try {
    const claves = JSON.parse(Deno.env.get("SUPABASE_SECRET_KEYS") || "{}");
    if (claves.default) return claves.default;
  } catch { /* retrocompatible */ }
  return Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
}
const KEY = adminKey();
if (!PROJECT_URL || !KEY) throw Error("Faltan credenciales de servicio del servidor");
const db = createClient(PROJECT_URL, KEY, { auth: { persistSession: false } });
const BUCKET = "futbolapp-data";

const json = (payload, status = 200) => new Response(JSON.stringify(payload), {
  status,
  headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
});
const fallo = (message, status = 400) => json({ error: message }, status);
const sha256 = async (input) => {
  const bytes = new TextEncoder().encode(input);
  const hash = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(hash), (b) => b.toString(16).padStart(2, "0")).join("");
};
const secureToken = () => Array.from(crypto.getRandomValues(new Uint8Array(32)),
  (b) => b.toString(16).padStart(2, "0")).join("");
const isUUID = (s) => typeof s === "string" &&
  /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(s);

async function auth(token) {
  if (typeof token !== "string" || !/^[0-9a-f]{64}$/.test(token)) return null;
  const hash = await sha256(token);
  const { data, error } = await db.from("agentes_scraper")
    .select("id,nombre")
    .eq("token_hash", hash)
    .eq("activo", true)
    .maybeSingle();
  if (error) throw error;
  return data;
}

async function trabajoPropio(id, agenteId) {
  if (!isUUID(id)) return null;
  const { data, error } = await db.from("solicitudes_scraper")
    .select("id,estado,agente_id")
    .eq("id", id)
    .eq("agente_id", agenteId)
    .maybeSingle();
  if (error) throw error;
  return data;
}

async function manejar(body) {
  const action = body?.action;
  if (action === "pair") {
    const code = typeof body.code === "string" ? body.code.trim() : "";
    if (!/^[A-Za-z0-9_-]{20,100}$/.test(code)) return fallo("Código inválido", 403);
    const hash = await sha256(code);
    // Update condicional: un código no puede ser consumido dos veces.
    const { data: codigo, error } = await db.from("codigos_agente")
      .update({ usado_en: new Date().toISOString() })
      .eq("codigo_hash", hash)
      .is("usado_en", null)
      .gt("vence_en", new Date().toISOString())
      .select("id")
      .maybeSingle();
    if (error) throw error;
    if (!codigo) return fallo("Código caducado o ya utilizado", 403);
    const token = secureToken();
    const nombre = String(body.name || "Windows").slice(0, 80).trim() || "Windows";
    const { data: dispositivo, error: e2 } = await db.from("agentes_scraper")
      .insert({ nombre, token_hash: await sha256(token), ultima_vez: new Date().toISOString() })
      .select("id,nombre")
      .single();
    if (e2) throw e2;
    return json({ name: dispositivo.nombre, agent_id: dispositivo.id, token });
  }

  const agente = await auth(body?.token);
  if (!agente) return fallo("Ordenador no vinculado, credencial inválida o revocada", 401);

  if (action === "heartbeat") {
    const { error } = await db.from("agentes_scraper")
      .update({ ultima_vez: new Date().toISOString() })
      .eq("id", agente.id).eq("activo", true);
    if (error) throw error;
    return json({ agent_id: agente.id, name: agente.nombre });
  }

  if (action === "claim") {
    const { data, error } = await db.rpc("reclamar_solicitud_agente", {
      p_agente_id: agente.id,
    });
    if (error) throw error;
    return json({ job: data?.[0] ? { id: data[0].id } : null });
  }

  if (action === "upload_url") {
    const trabajo = await trabajoPropio(body.job_id, agente.id);
    if (!trabajo || trabajo.estado !== "ejecutando") return fallo("Trabajo no autorizado", 403);
    const path = `trabajos/${trabajo.id}/jugadores_rfaf.csv`;
    const { data, error } = await db.storage.from(BUCKET)
      .createSignedUploadUrl(path, { upsert: true });
    if (error) throw error;
    const signed = data.signedUrl;
    const uploadUrl = /^https?:\/\//.test(signed) ? signed :
      `${PROJECT_URL}/storage/v1${signed.startsWith("/") ? "" : "/"}${signed}`;
    return json({ upload_url: uploadUrl });
  }

  if (action === "finish") {
    const trabajo = await trabajoPropio(body.job_id, agente.id);
    if (!trabajo || trabajo.estado !== "ejecutando") return fallo("Trabajo no autorizado", 403);
    const directorio = `trabajos/${trabajo.id}`;
    const { data, error } = await db.storage.from(BUCKET).list(directorio, { limit: 100 });
    if (error) throw error;
    const file = data?.find((f) => f.name === "jugadores_rfaf.csv");
    if (!file) return fallo("No se encuentra el CSV cargado", 409);
    const path = `${directorio}/jugadores_rfaf.csv`;
    const { error: e2 } = await db.from("solicitudes_scraper")
      .update({ estado: "completado", terminado_en: new Date().toISOString(),
        archivo: path, mensaje: `CSV publicado (${Number(body.bytes || 0)} bytes)` })
      .eq("id", trabajo.id).eq("agente_id", agente.id).eq("estado", "ejecutando");
    if (e2) throw e2;
    return json({ ok: true, path });
  }

  if (action === "fail") {
    const trabajo = await trabajoPropio(body.job_id, agente.id);
    if (!trabajo || trabajo.estado !== "ejecutando") return fallo("Trabajo no autorizado", 403);
    const mensaje = String(body.message || "Error desconocido").slice(0, 1000);
    const { error } = await db.from("solicitudes_scraper")
      .update({ estado: "error", terminado_en: new Date().toISOString(), mensaje })
      .eq("id", trabajo.id).eq("agente_id", agente.id).eq("estado", "ejecutando");
    if (error) throw error;
    return json({ ok: true });
  }

  return fallo("Acción desconocida", 404);
}

Deno.serve(async (req) => {
  if (req.method !== "POST") return fallo("Solo POST", 405);
  try {
    const contentLength = Number(req.headers.get("content-length") || 0);
    if (contentLength > 10000) return fallo("Solicitud demasiado grande", 413);
    const body = await req.json();
    return await manejar(body);
  } catch (exc) {
    console.error("RFAF Agent error", exc);
    return fallo("Error del servidor. Revisa los logs de Edge Functions.", 500);
  }
});
