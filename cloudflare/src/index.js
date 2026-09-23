const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, HEAD, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, X-API-Key",
  "Cache-Control": "no-store",
};

function json(data, status = 200, extraHeaders = {}) {
  return new Response(JSON.stringify(data), {
    status,
    headers: {
      "Content-Type": "application/json; charset=utf-8",
      ...corsHeaders,
      ...extraHeaders,
    },
  });
}

function normalizeBaseUrl(value) {
  const raw = String(value || "").trim();
  if (!raw) return null;

  try {
    const url = new URL(raw);

    if (!["http:", "https:"].includes(url.protocol)) {
      return null;
    }

    url.pathname = url.pathname.replace(/\/+$/, "");
    url.search = "";
    url.hash = "";

    return url;
  } catch {
    return null;
  }
}

function getConfig(env) {
  const upstream = normalizeBaseUrl(env.ROBLOX_UPSTREAM_URL);
  const apiKey = String(env.ROBLOX_MONITOR_API_KEY || "").trim();

  return {
    ok: Boolean(upstream && apiKey),
    upstream,
    apiKey,
  };
}

function buildUpstreamUrl(baseUrl, pathname) {
  const url = new URL(baseUrl.toString());
  const basePath = url.pathname.replace(/\/+$/, "");
  url.pathname = basePath + pathname;
  return url;
}

function methodNotAllowed(allow) {
  return json(
    { ok: false, error: "method_not_allowed" },
    405,
    { Allow: allow },
  );
}

async function proxyRequest(request, env, pathname, requireApiKey = true) {
  const config = getConfig(env);

  if (!config.upstream || !config.apiKey) {
    return json(
      { ok: false, error: "proxy_not_configured" },
      500,
    );
  }

  if (requireApiKey) {
    const suppliedKey = request.headers.get("X-API-Key") || "";

    if (suppliedKey !== config.apiKey) {
      return json({ ok: false, error: "unauthorized" }, 401);
    }
  }

  const upstreamUrl = buildUpstreamUrl(config.upstream, pathname);

  const headers = new Headers();
  headers.set("X-API-Key", config.apiKey);

  const contentType = request.headers.get("Content-Type");
  if (contentType) {
    headers.set("Content-Type", contentType);
  }

  const accept = request.headers.get("Accept");
  if (accept) {
    headers.set("Accept", accept);
  }

  try {
    const response = await fetch(upstreamUrl, {
      method: request.method,
      headers,
      body: request.method === "POST" ? request.body : undefined,
    });

    const responseHeaders = new Headers(response.headers);

    for (const [key, value] of Object.entries(corsHeaders)) {
      responseHeaders.set(key, value);
    }

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: responseHeaders,
    });
  } catch (error) {
    console.error("Roblox monitor upstream fetch failed:", error);

    return json(
      { ok: false, error: "upstream_unreachable" },
      502,
    );
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const pathname = url.pathname;
    const method = request.method.toUpperCase();

    if (method === "OPTIONS") {
      return new Response(null, {
        status: 204,
        headers: corsHeaders,
      });
    }

    if (pathname === "/" && (method === "GET" || method === "HEAD")) {
      const config = getConfig(env);

      return json({
        ok: true,
        service: "nawaf-roblox-worker",
        configured: config.ok,
        endpoints: ["/health", "/v1/errors"],
      });
    }

    if (pathname === "/health") {
      if (method !== "GET" && method !== "HEAD") {
        return methodNotAllowed("GET, HEAD, OPTIONS");
      }

      return proxyRequest(request, env, "/health", false);
    }

    if (pathname === "/v1/errors") {
      if (method !== "POST") {
        return methodNotAllowed("POST, OPTIONS");
      }

      return proxyRequest(request, env, "/v1/errors", true);
    }

    return json(
      { ok: false, error: "not_found" },
      404,
    );
  },
};
