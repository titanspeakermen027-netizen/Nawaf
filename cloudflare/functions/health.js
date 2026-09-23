import { proxyToUpstream } from "./_proxy.js";

export function onRequest(context) {
  return proxyToUpstream(context.request, context.env, "/health");
}
