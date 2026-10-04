import { getStore } from "@netlify/blobs";

// adresa: /.netlify/functions/moderate
// Lista produselor ascunse sau sterse din consola. Citirea e publica (pagina de bio ascunde produsele),
// modificarile cer PIN-ul consolei (salvat doar ca hash).

const KEY = "state";

function reply(body, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store", "access-control-allow-origin": "*" },
  });
}

async function sha(text) {
  const buf = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text));
  return [...new Uint8Array(buf)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function publicView(st) {
  const items = st.items || {};
  return {
    pin_set: Boolean(st.pin),
    hidden: Object.keys(items).filter((k) => items[k].action === "hide"),
    deleted: Object.keys(items).filter((k) => items[k].action === "delete"),
    items,
  };
}

export default async (req) => {
  const store = getStore({ name: "trendixeu-moderation", consistency: "strong" });
  const st = (await store.get(KEY, { type: "json" })) || { items: {} };
  if (req.method === "OPTIONS") return reply({ ok: true });
  if (req.method !== "POST") return reply(publicView(st));

  let data = {};
  try { data = JSON.parse((await req.text()) || "{}"); } catch { data = {}; }
  const pin = String(data.pin || "").trim();
  if (pin.length < 4) return reply({ ok: false, error: "PIN-ul trebuie să aibă minim 4 caractere" }, 400);

  if (data.action === "setpin") {
    if (st.pin) return reply({ ok: false, error: "PIN-ul e deja setat" }, 403);
    st.salt = crypto.randomUUID();
    st.pin = await sha(st.salt + pin);
    await store.setJSON(KEY, st);
    return reply({ ok: true, ...publicView(st) });
  }

  if (!st.pin || (await sha(st.salt + pin)) !== st.pin) {
    await new Promise((r) => setTimeout(r, 800)); // incetineste ghicitul
    return reply({ ok: false, error: "PIN greșit" }, 403);
  }
  if (data.action === "check") return reply({ ok: true, ...publicView(st) });

  const id = String(data.id || "").replace(/[^\w-]/g, "").slice(0, 60);
  if (!id) return reply({ ok: false, error: "lipsește produsul" }, 400);
  st.items = st.items || {};
  if (data.action === "restore") {
    delete st.items[id];
  } else if (data.action === "hide" || data.action === "delete") {
    st.items[id] = {
      action: data.action,
      title: String(data.title || "").slice(0, 140),
      num: data.num ? Number(data.num) || null : null,
      section: String(data.section || "").slice(0, 20),
      at: new Date().toISOString(),
    };
  } else {
    return reply({ ok: false, error: "acțiune necunoscută" }, 400);
  }
  await store.setJSON(KEY, st);
  return reply({ ok: true, ...publicView(st) });
};
