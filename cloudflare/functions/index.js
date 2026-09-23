import { json, getConfig } from "./_proxy.js";

export function onRequest({ request, env }) {
  if (request.method !== "GET" && request.method !== "HEAD") {
    return json({ ok: false, error: "method_not_allowed" }, 405);
  }

  const config = getConfig(env);

  return json({
    ok: config.ok,
    service: "nawaf-roblox-pages-proxy",
    endpoints: ["/health", "/v1/errors"],
    configured: config.ok,
  });
}
