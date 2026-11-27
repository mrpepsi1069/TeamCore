// Talks to the FastAPI backend. The session token lives in localStorage and is sent as a Bearer header; no cookies are used.
import { state } from "./state.js";

export const API = (window.TEAMCORE_CONFIG?.API_BASE || "").replace(/\/$/, "");
export const getToken = () => localStorage.getItem("tc_token");
export const setToken = (t) => (t ? localStorage.setItem("tc_token", t) : localStorage.removeItem("tc_token"));

export class ApiError extends Error {
  constructor(status, detail) {
    const d = detail && typeof detail === "object" ? detail : { message: detail };
    super(d.message || (typeof detail === "string" ? detail : "Request failed"));
    this.status = status;
    this.errors = d.errors || null;
  }
}

const UNREACHABLE = "The TeamCore API didn't answer — it may be restarting. Try again in a moment; if it keeps happening, check your connection and the API address in config.js.";

// The fetch throws — instead of answering with a status — when no usable response comes back at all: the network
// is down, or the reply arrived without CORS headers (a proxy error page while the API restarts or wakes up).
// A GET changes nothing, so it is safe to retry a couple of times before giving up on the page.
export async function api(path, { method = "GET", body, raw = false } = {}) {
  const headers = {};
  const t = getToken();
  if (t) headers.Authorization = `Bearer ${t}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  const attempts = method === "GET" ? 3 : 1;
  let res;
  for (let attempt = 1; ; attempt++) {
    try {
      res = await fetch(API + path, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined });
      break;
    } catch {
      if (attempt >= attempts) throw new ApiError(0, UNREACHABLE);
      await new Promise((r) => setTimeout(r, 700 * attempt));
    }
  }
  if (res.status === 401) {
    setToken(null);
    state.user = null;
    location.hash = "#/";
    location.reload();
    throw new ApiError(401, "Session expired");
  }
  if (raw) {
    if (!res.ok) throw new ApiError(res.status, (await res.json().catch(() => ({}))).detail);
    return res;
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, data.detail);
  return data;
}

export const g = (path, opts) => api(`/api/guilds/${state.gid}${path}`, opts);
