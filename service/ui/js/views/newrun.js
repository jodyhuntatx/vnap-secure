// New run: from a template (with overrides), from scenario text, or with the builder.
import { get, post } from "../api.js";
import { clear, errorList, h, notice } from "../dom.js";
import { builder } from "./builder.js";

export async function newRunView(app, session) {
  const catalogue = await get("/scenarios");
  const tabs = h("div", { class: "tabs" });
  const pane = h("div", {});
  app.append(h("h1", {}, "New run"), tabs, pane);

  const views = {
    "From a template": () => templatePane(pane, session, catalogue),
    "Scenario text": () => textPane(pane, session, ""),
    "Builder": () => builder(pane, session, (text) => { select("Scenario text"); textPane(pane, session, text); }),
  };
  function select(name) {
    for (const b of tabs.children) b.classList.toggle("active", b.textContent === name);
  }
  for (const name of Object.keys(views)) {
    tabs.append(h("button", { type: "button", onclick: () => { select(name); clear(pane); views[name](); } }, name));
  }
  select("From a template");
  views["From a template"]();
}

// Validate / start controls shared by the panes. `request()` returns the scenario reference.
export function startControls(session, request, onErrors) {
  const duration = h("input", { type: "number", min: 1, value: 30 });
  const result = h("div", {});
  async function validate() {
    clear(result);
    const r = await post("/scenarios/validate", request());
    if (onErrors) onErrors(r.errors || []);
    if (r.valid) {
      result.append(h("p", { class: "badge ok" }, `valid: ${r.stations.length} station(s)`),
        h("p", { class: "muted" }, r.stations.map((s) => `${s.name} ${s.ip}${s.assigned.length ? " (assigned " + s.assigned.join(", ") + ")" : ""}`).join(" · ")));
    } else {
      result.append(errorList(r.errors));
    }
    return r.valid;
  }
  async function start() {
    try {
      if (!(await validate())) return;
      const run = await post("/runs", { ...request(), duration_minutes: Number(duration.value) || undefined });
      notice(`run ${run.id} queued`);
      location.hash = `#/run/${run.id}`;
    } catch (e) {
      clear(result);
      result.append(e.errors && e.errors.length ? errorList(e.errors) : h("p", { class: "errors" }, e.message));
      if (onErrors && e.errors) onErrors(e.errors);
    }
  }
  return h("div", {},
    h("div", { class: "row" },
      h("label", {}, "Duration (minutes)", duration),
      h("button", { type: "button", class: "secondary", onclick: () => validate().catch((e) => notice(e.message, "error")) }, "Validate"),
      h("button", { type: "button", onclick: start }, "Start")),
    result);
}

function overridesBox() {
  const box = h("textarea", { rows: 3, placeholder: "one override per line, e.g. control.client.interval=5" });
  box.lines = () => box.value.split("\n").map((l) => l.trim()).filter(Boolean);
  return box;
}

async function templatePane(pane, session, catalogue) {
  const names = [...(catalogue.templates || []).map((t) => ["templates", t]), ...(catalogue.scenarios || []).map((s) => ["scenarios", s])];
  if (!names.length) {
    pane.append(h("p", { class: "panel muted" }, "No templates available."));
    return;
  }
  const choice = h("select", {}, names.map(([kind, name]) => h("option", { value: `${kind}/${name}` }, kind === "templates" ? name : `${name} (catalogue)`)));
  const text = h("pre", {});
  const overrides = overridesBox();
  async function show() {
    const [kind, name] = choice.value.split("/");
    try { text.textContent = (await get(`/scenarios/${kind}/${name}`)).text; } catch (e) { text.textContent = e.message; }
  }
  choice.addEventListener("change", show);
  const request = () => {
    const [kind, name] = choice.value.split("/");
    return { [kind === "templates" ? "template" : "scenario"]: name, overrides: overrides.lines() };
  };
  pane.append(h("div", { class: "panel" },
    h("div", { class: "row" }, h("label", {}, "Template", choice)),
    h("h2", {}, "Scenario"), text,
    h("h2", {}, "Overrides"), overrides,
    startControls(session, request)));
  await show();
}

export function textPane(pane, session, initial) {
  clear(pane);
  const text = h("textarea", { rows: 24, spellcheck: "false" });
  text.value = initial || `description = "my scenario"

[defaults]
security = "certs-v3"

[pki]

[[stations]]
name = "rsu"
station_type = 15

[[stations]]
name = "car1"
pseudonyms = {}

[control]

[control.client]
mode = "periodic"
interval = 20
`;
  const overrides = overridesBox();
  pane.append(h("div", { class: "panel" },
    h("p", { class: "muted" }, "Scenario in TOML (", h("a", { href: "/api/schema", target: "_blank", rel: "noopener" }, "schema"),
      "). Addresses, IDs and certificate files are assigned by the service: leave them out."),
    text, h("h2", {}, "Overrides"), overrides,
    startControls(session, () => ({ scenario_text: text.value, overrides: overrides.lines() }))));
}
