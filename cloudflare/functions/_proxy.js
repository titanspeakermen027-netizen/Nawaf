const ALLOWED_METHODS = {
  "/health": new Set(["GET", "HEAD"]),
  "/v1/errors": new Set(["POST", "OPTIONS"]),
};

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Methods": "GET,HEAD,POST,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type, X-API-Key",
    "Cache-Control": "no-store",
  };
}

function json(data, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      ...corsHeaders(),
      ...extraHeaders,
    },
  });
}

function normalizeBaseUrl(value) {
  const raw = String(value || "").trim();
  if (!raw) return null;

  let url;
  try {
    url = new URL(raw);
  } catch {
    return null;
  }

  if (!["http:", "https:"].includes(url.protocol)) return null;
  url.pathname = url.pathname.replace(/\/+$/, "");
  return url;
}

function buildUpstreamUrl(baseUrl, pathname) {
  const url = new URL(baseUrl.toString());
  const basePath = url.pathname.replace(/\/+$/, "");
  url.pathname = basePath + pathname;
  url.search = "";
  return url;
}

export function getConfig(env) {
  const upstream = normalizeBaseUrl(env.ROBLOX_UPSTREAM_URL);
  const apiKey = String(env.ROBLOX_MONITOR_API_KEY || "").trim();

  if (!upstream) {
    return { ok: false, error: "ROBLOX_UPSTREAM_URL is not configured" };
  }

  if (!apiKey) {
    return { ok: false, error: "ROBLOX_MONITOR_API_KEY is not configured" };
  }

  return { ok: true, upstream, apiKey };
}

export function isAllowedMethod(pathname, method) {
  return ALLOWED_METHODS[pathname]?.has(method) ?? false;
}

export function unauthorized() {
  return json({ ok: false, error: "unauthorized" }, 401);
}

export async function proxyToUpstream(request, env, pathname) {
  const config = getConfig(env);
  if (!config.ok) {
    return json({ ok: false, error: "proxy_not_configured" }, 500);
  }

  const incomingMethod = request.method.toUpperCase();

  if (incomingMethod === "OPTIONS") {
    return new Response(null, {
      status: 204,
      headers: corsHeaders(),
    });
  }

  if (!isAllowedMethod(pathname, incomingMethod)) {
    return json({ ok: false, error: "method_not_allowed" }, 405);
  }

  const suppliedKey = request.headers.get("X-API-Key") || "";
  if (suppliedKey !== config.apiKey) {
    return unauthorized();
  }

  const upstreamUrl = buildUpstreamUrl(config.upstream, pathname);
  const headers = new Headers();
  headers.set("X-API-Key", config.apiKey);

  const contentType = request.headers.get("Content-Type");
  if (contentType) headers.set("Content-Type", contentType);

  const accept = request.headers.get("Accept");
  if (accept) headers.set("Accept", accept);

  const body = incomingMethod === "POST" ? request.body : undefined;

  let upstreamResponse;
  try {
    upstreamResponse = await fetch(upstreamUrl.toString(), {
      method: incomingMethod,
      headers,
      body,
    });
  } catch (error) {
    console.error("Roblox monitor upstream fetch failed", error);
    return json({ ok: false, error: "upstream_unreachable" }, 502);
  }

  const responseHeaders = new Headers(upstreamResponse.headers);
  for (const [key, value] of Object.entries(corsHeaders())) {
    responseHeaders.set(key, value);
  }

  return new Response(upstreamResponse.body, {
    status: upstreamResponse.status,
    statusText: upstreamResponse.statusText,
    headers: responseHeaders,
  });
}
