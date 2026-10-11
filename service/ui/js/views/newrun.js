// New run: choose a template, which becomes the new scenario's text; edit it as text or in
// the builder; validate and start it, or save it as a template of one's own.
import { del, get, post } from "../api.js";
import { append, clear, errorList, h, notice } from "../dom.js";
import { builder } from "./builder.js";

const BLANK = `description = "my scenario"

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
const TABS = ["Scenario text", "Builder"];

export async function newRunView(app, session) {
  let catalogue = await get("/scenarios");
  let source = { key: "blank", name: "", text: BLANK };   // what was imported: {key, name, text, ref?, saved?}
  let text = BLANK;          // the new scenario, as of the last change of tab
  let active = TABS[0];
  let area = null;           // the textarea of "Scenario text"
  let editor = null;         // the builder of "Builder"
  const choice = h("select", {});
  const remove = h("button", { type: "button", class: "danger", title: "Delete the selected saved template", onclick: removeTemplate }, "Delete template");
  const tabs = h("div", { class: "tabs" });
  const pane = h("div", {});
  const result = h("div", {});
  const overrides = h("textarea", { rows: 3, placeholder: "one override per line, e.g. control.client.interval=5" });
  const duration = h("input", { type: "number", min: 1, value: 30 });
  const saveName = h("input", { placeholder: "template name", maxlength: 64 });
  const generic = h("input", { type: "checkbox" });
  const shared = h("input", { type: "checkbox" });

  // ------------------------------------------------------------ the scenario being edited
  // In the builder the text changes only when something was edited there: writing the model
  // back as TOML would drop the text's comments for nothing.
  const current = () => (active === "Builder" ? (editor && editor.changed() ? editor.toml() : text) : area.value);
  const edited = () => current() !== source.text;
  const overrideLines = () => overrides.value.split("\n").map((l) => l.trim()).filter(Boolean);
  // an unchanged template or catalogue scenario is started by its name, anything else as text
  function request() {
    const now = current();
    if (source.ref && now === source.text) return { ...source.ref, overrides: overrideLines() };
    return { scenario_text: now, overrides: overrideLines(), name: source.name || undefined };
  }

  async function show(name) {
    let parsed = null;
    if (name === "Builder") {
      try {
        parsed = (await post("/scenarios/parse", { scenario_text: text })).scenario;
      } catch (e) {
        notice(`The builder cannot read the text: ${e.message}`, "error");
        name = "Scenario text";
      }
    }
    active = name;
    editor = null;
    for (const b of tabs.children) b.classList.toggle("active", b.textContent === name);
    clear(pane);
    clear(result);
    if (name === "Builder") {
      editor = await builder(pane, parsed);
      return;
    }
    area = h("textarea", { rows: 24, spellcheck: "false" });
    area.value = text;
    pane.append(h("div", { class: "panel" },
      h("p", { class: "muted" }, "Scenario in TOML (", h("a", { href: "/api/schema", target: "_blank", rel: "noopener" }, "schema"),
        "). Addresses, IDs and certificate files are assigned by the service: leave them out. A string in \"…\" stays on one line; ",
        "write a description of several lines as ", h("code", {}, '"""…"""'), "."),
      area));
  }
  async function select(name) {
    text = current();
    await show(name);
  }

  // ------------------------------------------------------------ templates
  function fillChoice() {
    const mine = (t) => t.owner === session.user.username;
    append(clear(choice), [
      h("option", { value: "blank" }, "(blank scenario)"),
      (catalogue.templates || []).map((t) => h("option", { value: `templates/${t}` }, t)),
      (catalogue.saved || []).map((t) => h("option", { value: `saved/${t.id}` }, `${t.name} (saved${mine(t) ? "" : ` by ${t.owner}`}${t.shared ? ", shared" : ""})`)),
      (catalogue.scenarios || []).map((s) => h("option", { value: `scenarios/${s}` }, `${s} (catalogue)`))]);
    choice.value = source.key;
    if (choice.value !== source.key) choice.value = "blank";   // e.g. the saved template was deleted
    const saved = (catalogue.saved || []).find((t) => `saved/${t.id}` === choice.value);
    remove.hidden = !(saved && (mine(saved) || session.user.role === "admin"));
  }
  async function load(keyOf) {
    const [kind, name] = keyOf.split("/");
    if (kind === "blank") return { key: keyOf, name: "", text: BLANK };
    if (kind === "saved") {
      const t = await get(`/templates/${name}`);
      return { key: keyOf, name: t.name, text: t.text, saved: t };
    }
    const t = await get(`/scenarios/${kind}/${name}`);
    return { key: keyOf, name, text: t.text, ref: { [kind === "templates" ? "template" : "scenario"]: name } };
  }
  async function importTemplate() {
    if (edited() && !confirm("Replace the scenario you edited with the selected template?")) {
      choice.value = source.key;
      return;
    }
    try {
      source = await load(choice.value);
    } catch (e) {
      notice(e.message, "error");
      choice.value = source.key;
      return;
    }
    text = source.text;
    saveName.value = source.saved && source.saved.owner === session.user.username ? source.name : "";
    shared.checked = !!(source.saved && source.saved.shared);
    fillChoice();
    await show(active);
  }
  async function saveTemplate() {
    const body = { name: saveName.value.trim(), scenario_text: current(), shared: shared.checked, genericize: generic.checked };
    clear(result);
    try {
      let t;
      try {
        t = await post("/templates", body);
      } catch (e) {
        if (e.status !== 409 || !confirm(`Replace your template ${body.name}?`)) throw e;
        t = await post("/templates", { ...body, replace: true });
      }
      const renamed = Object.entries(t.renamed || {}).filter(([from, to]) => from !== to);
      notice(`template ${t.name} saved` + (renamed.length ? `: ${renamed.map(([from, to]) => `${from} → ${to}`).join(", ")}` : ""));
      catalogue = await get("/scenarios");
      source = { key: `saved/${t.id}`, name: t.name, text: t.text, saved: t };
      text = t.text;
      generic.checked = false;
      fillChoice();
      await show(active);
    } catch (e) {
      showErrors(e);
    }
  }
  async function removeTemplate() {
    if (!source.saved || !confirm(`Delete the saved template ${source.name}? The scenario stays here for editing.`)) return;
    try {
      await del(`/templates/${source.saved.id}`);
      notice(`template ${source.name} deleted`);
      catalogue = await get("/scenarios");
      source = { key: "blank", name: source.name, text: "" };   // nothing to go back to: the text counts as edited
      fillChoice();
    } catch (e) {
      notice(e.message, "error");
    }
  }

  // ------------------------------------------------------------ validate and start
  function showErrors(e) {
    clear(result);
    result.append(e.errors && e.errors.length ? errorList(e.errors) : h("p", { class: "errors" }, e.message));
    if (editor) editor.showErrors(e.errors || []);
  }
  async function validate() {
    clear(result);
    const r = await post("/scenarios/validate", request());
    if (editor) editor.showErrors(r.errors || []);
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
      showErrors(e);
    }
  }

  choice.addEventListener("change", importTemplate);
  for (const name of TABS) {
    tabs.append(h("button", { type: "button", onclick: () => select(name).catch((e) => notice(e.message, "error")) }, name));
  }
  app.append(h("h1", {}, "New run"),
    h("div", { class: "panel" },
      h("div", { class: "row" }, h("label", {}, "Start from the template", choice), remove),
      h("p", { class: "muted" }, "The template's text becomes your new scenario. Edit it as text or in the builder; both edit the same scenario. ",
        "The builder keeps what its forms do not show, but not the text's comments.")),
    tabs, pane,
    h("div", { class: "panel" },
      h("h2", {}, "Overrides"), overrides,
      h("div", { class: "row" },
        h("label", {}, "Duration (minutes)", duration),
        h("button", { type: "button", class: "secondary", onclick: () => validate().catch(showErrors) }, "Validate"),
        h("button", { type: "button", onclick: start }, "Start")),
      result),
    h("div", { class: "panel" },
      h("h2", {}, "Save as a template"),
      h("div", { class: "row" },
        h("label", {}, "Name", saveName),
        h("label", { class: "inline", title: "Stations are renamed rsu1, rsu2, … (road-side units) and vehicle1, vehicle2, …; mix zones zone1, zone2, …. The scenario here gets the new names too." },
          generic, "generic names"),
        h("label", { class: "inline", title: "Every user of this service can start from the template; only you can change or delete it." },
          shared, "shared with all users"),
        h("button", { type: "button", class: "secondary", onclick: saveTemplate }, "Save template")),
      h("p", { class: "muted" }, "Saves the scenario as it is now (without the overrides). It must be valid. Saved templates appear in the list above.")));
  fillChoice();
  await show(active);
}
