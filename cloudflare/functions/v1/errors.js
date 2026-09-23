import { proxyToUpstream } from "../_proxy.js";

export async function onRequest(context) {
  return proxyToUpstream(context.request, context.env, "/v1/errors");
}
