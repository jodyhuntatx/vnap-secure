// Calls to the service API. The session cookie goes along automatically; state-changing
// requests carry the session's CSRF token.

let csrf = null;

export class ApiError extends Error {
  constructor(status, message, errors, data) {
    super(message);
    this.status = status;
    this.errors = errors || [];   // field-level validation errors: {path, message, text}
    this.data = data || {};       // the whole error body (e.g. totp_required)
  }
}

export function setCsrf(token) { csrf = token; }

export async function api(method, path, body) {
  const headers = {};
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrf) headers["X-CSRF-Token"] = csrf;
  let response;
  try {
    response = await fetch("/api" + path, {
      method, headers, credentials: "same-origin",
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    throw new ApiError(0, "the service is not reachable");
  }
  let data = null;
  const text = await response.text();
  try { data = text ? JSON.parse(text) : null; } catch (e) { data = { detail: text }; }
  if (!response.ok) {
    let message = (data && (data.detail || data.error)) || response.statusText;
    if (Array.isArray(message)) message = message.map((d) => d.msg || JSON.stringify(d)).join("; ");
    throw new ApiError(response.status, String(message), data && data.errors, data);
  }
  return data;
}

export const get = (path) => api("GET", path);
export const post = (path, body) => api("POST", path, body === undefined ? {} : body);
export const patch = (path, body) => api("PATCH", path, body);
export const del = (path) => api("DELETE", path);
