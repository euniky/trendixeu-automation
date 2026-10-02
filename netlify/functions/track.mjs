import { getStore } from "@netlify/blobs";

// adresa: /.netlify/functions/track

const BOTS = /bot|crawl|spider|slurp|preview|facebookexternalhit|bytespider|headless|lighthouse/i;

function reply(body = { ok: true }, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
}

export default async (req) => {
  let data = {};
  if (req.method === "POST") {
    try { data = JSON.parse((await req.text()) || "{}"); } catch { data = {}; }
  } else {
    data = Object.fromEntries(new URL(req.url).searchParams);
  }
  const type = data.e === "view" ? "view" : data.e === "click" ? "click" : null;
  if (!type) return reply({ ok: false, error: "tip necunoscut" }, 400);

  const id = String(data.id || "").replace(/[^\w-]/g, "").slice(0, 40);
  const isTest = id.startsWith("__");
  if (!isTest && BOTS.test(req.headers.get("user-agent") || "")) return reply({ ok: true, skipped: "bot" });

  const day = new Date().toISOString().slice(0, 10);
  const cutoff = new Date(Date.now() - 90 * 864e5).toISOString().slice(0, 10);
  const store = getStore({ name: "trendixeu-stats", consistency: "strong" });

  if (type === "view") {
    const key = `views/${day}`;
    const cur = (await store.get(key, { type: "json" })) || { n: 0 };
    cur.n += 1;
    await store.setJSON(key, cur);
    return reply();
  }

  if (!id) return reply({ ok: false, error: "lipseste id" }, 400);
  const key = `clicks/${id}`;
  const cur = (await store.get(key, { type: "json" })) || { id, title: "", section: "", total: 0, days: {} };
  if (data.title) cur.title = String(data.title).slice(0, 120);
  if (data.s) cur.section = String(data.s).slice(0, 20);
  cur.total += 1;
  cur.days[day] = (cur.days[day] || 0) + 1;
  for (const d of Object.keys(cur.days)) if (d < cutoff) delete cur.days[d];
  cur.last = new Date().toISOString();
  await store.setJSON(key, cur);
  return reply();
};
