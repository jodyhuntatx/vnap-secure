// Small DOM helpers. Text always goes in through textContent, never innerHTML, so names,
// scenario text and event messages cannot inject markup.

export function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === undefined || value === null || value === false) continue;
    if (key === "class") el.className = value;
    else if (key.startsWith("on") && typeof value === "function") el.addEventListener(key.slice(2), value);
    else if (key === "value") el.value = value;
    else if (key === "checked") el.checked = !!value;
    else el.setAttribute(key, value === true ? "" : String(value));
  }
  append(el, children);
  return el;
}

export function append(el, children) {
  for (const child of children.flat(Infinity)) {
    if (child === undefined || child === null || child === false) continue;
    el.append(child instanceof Node ? child : document.createTextNode(String(child)));
  }
  return el;
}

export function clear(el) { while (el.firstChild) el.removeChild(el.firstChild); return el; }

export function table(headers, rows) {
  return h("div", { class: "scroll" }, h("table", {},
    h("thead", {}, h("tr", {}, headers.map((x) => h("th", {}, x)))),
    h("tbody", {}, rows.map((r) => h("tr", {}, r.map((c) => h("td", {}, c)))))));
}

export function badge(text, kind) { return h("span", { class: "badge " + (kind || text || "") }, text); }

export function notice(message, kind) {
  const el = document.getElementById("notice");
  el.textContent = message;
  el.className = "notice " + (kind || "info");
  el.hidden = !message;
  clearTimeout(notice.timer);
  if (message && kind !== "error") notice.timer = setTimeout(() => { el.hidden = true; }, 5000);
}

export function errorList(errors) {
  if (!errors || !errors.length) return null;
  return h("ul", { class: "errors" }, errors.map((e) => h("li", {}, e.path ? [h("code", {}, e.path), " ", e.message] : e.message || e.text)));
}

export function time(t) {
  if (!t) return "";
  const d = new Date(t * 1000);
  return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function remaining(deadline) {
  if (!deadline) return "";
  const s = Math.round(deadline - Date.now() / 1000);
  if (s <= 0) return "due";
  return s >= 3600 ? `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

// timers and streams of the current view, stopped when the view changes
const cleanups = [];
export function onLeave(fn) { cleanups.push(fn); }
export function leave() { while (cleanups.length) { try { cleanups.pop()(); } catch (e) { /* ignore */ } } }
export function every(ms, fn) { const id = setInterval(fn, ms); onLeave(() => clearInterval(id)); return id; }
