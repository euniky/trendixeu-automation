import { getStore } from "@netlify/blobs";

export const config = { path: ["/api/stats", "/api/stats/*"] };

export default async (req) => {
  const store = getStore({ name: "trendixeu-stats", consistency: "strong" });
  const includeTest = new URL(req.url).searchParams.has("test");

  const { blobs: clickKeys } = await store.list({ prefix: "clicks/" });
  const clicks = (await Promise.all(clickKeys.map((b) => store.get(b.key, { type: "json" }))))
    .filter((c) => c && (includeTest || !String(c.id).startsWith("__")));

  const { blobs: viewKeys } = await store.list({ prefix: "views/" });
  const views = {};
  await Promise.all(viewKeys.map(async (b) => {
    const v = await store.get(b.key, { type: "json" });
    views[b.key.slice("views/".length)] = (v && v.n) || 0;
  }));

  return new Response(JSON.stringify({ generated: new Date().toISOString(), clicks, views }), {
    headers: { "content-type": "application/json", "cache-control": "no-store" },
  });
};
