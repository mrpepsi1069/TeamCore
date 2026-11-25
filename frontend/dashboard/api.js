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

export async function api(path, { method = "GET", body, raw = false } = {}) {
  const headers = {};
  const t = getToken();
  if (t) headers.Authorization = `Bearer ${t}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  let res;
  try {
    res = await fetch(API + path, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined });
  } catch {
    throw new ApiError(0, "Can't reach the TeamCore API. Check your connection or the API address in config.js.");
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
