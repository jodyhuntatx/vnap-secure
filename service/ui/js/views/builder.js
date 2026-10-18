// Scenario builder: stations, routes, crossings and mix zones in forms and on a map. It edits
// a scenario read from its text (fromScenario) and writes it back as TOML (toToml). Keys the
// forms do not show are kept as they are; comments of the text are not.
import { clear, h } from "../dom.js";
import { crossingLayer, getUiConfig, makeMap, offset, stationColor, zoneColor } from "./mapview.js";

let CENTER = [40.208106, -8.4197756];   // replaced by the service's origin (ui-config)
const SILENT_ALL = "Radio silence after each identity change, for every vehicle with pseudonyms ([defaults] pseudonyms). A vehicle's own value replaces it. Empty: 0, no silence.";
const SILENT_OWN = "This vehicle's own silent period. Empty: the value for all vehicles (shown in grey).";
const DMZ_HELP = "A mix zone that travels with the vehicles: a vehicle changes its pseudonym when at least 'min cars' participating vehicles, itself included, are within the radius of it, at most once per cooldown. Needs pseudonyms and movement.";

function newStation(kind, n) {
  const vehicle = kind === "vehicle";
  return {
    extra: {}, envExtra: {}, pseudoExtra: {}, hasPosition: !vehicle, dmzOn: false, dmzMin: "", color: "",
    kind, name: vehicle ? `car${n}` : (n > 1 ? `rsu${n}` : "rsu"),
    position: vehicle ? offset(CENTER, 0, 60 * n) : [...CENTER],
    pseudonyms: vehicle, silent_min_ms: "", silent_max_ms: "",   // "": the value for all vehicles
    mobility: "none", route: [], speed_kmh: 36, loop: true, start_s: 1.5 * (n - 1),
    crossing: [...CENTER], arm_m: 150, start_arm: ["east", "north", "west", "south"][(n - 1) % 4],
  };
}

// ------------------------------------------------------------ TOML
const q = (s) => JSON.stringify(String(s));
const n = (x) => String(Number(x) || 0);
const f = (x) => String(Number((Number(x) || 0).toFixed(7)));   // degrees: 7 decimals are about 1 cm
const pt = (p) => `[${f(p[0])}, ${f(p[1])}]`;
const key = (k) => (/^[A-Za-z0-9_-]+$/.test(k) ? k : q(k));
const isTable = (v) => v !== null && typeof v === "object" && !Array.isArray(v);

// any value on one line (tables as inline tables)
function value(v) {
  if (typeof v === "string") return q(v);
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  if (Array.isArray(v)) return `[${v.map(value).join(", ")}]`;
  return inline(pairs(v));
}
function pairs(obj) {
  return Object.entries(obj || {}).filter(([, v]) => v !== null && v !== undefined).map(([k, v]) => `${key(k)} = ${value(v)}`);
}
const inline = (parts) => (parts.length ? `{ ${parts.join(", ")} }` : "{}");

// silent_min_ms / silent_max_ms that are set ("" leaves the key out, so the value comes from elsewhere)
function silent(obj) {
  return ["silent_min_ms", "silent_max_ms"].filter((k) => obj[k] !== "" && obj[k] !== undefined).map((k) => `${k} = ${n(obj[k])}`);
}

// `obj` without `known` keys: what the forms do not show
function rest(obj, known) {
  return Object.fromEntries(Object.entries(obj || {}).filter(([k]) => !known.includes(k)));
}

// a vehicle can take part in the dynamic mix zone when it changes pseudonyms and moves
export function canMix(m, s) {
  return s.kind === "vehicle" && s.pseudonyms && m.signed && ((s.mobility === "route" && s.route.length >= 2) || s.mobility === "crossing");
}

export function toToml(m) {
  const lines = [`description = ${q(m.description)}`];
  lines.push(...pairs(Object.fromEntries(Object.entries(m.extra).filter(([, v]) => !isTable(v)))));
  lines.push("", "[defaults]", `security = ${q(m.signed ? m.security : "none")}`);
  // [defaults] pseudonyms: keys for every vehicle with pseudonyms; a vehicle writes only what it sets itself
  const shared = [...pairs(m.pseudo.extra), ...silent(m.pseudo)];
  if (shared.length) lines.push(`pseudonyms = ${inline(shared)}`);
  lines.push(...pairs(m.defaultsExtra), "");
  const anyPseudo = m.signed && m.stations.some((s) => s.kind === "vehicle" && s.pseudonyms);
  if (m.signed && m.hasPki) {
    lines.push("[pki]", `initial = ${n(m.pki.initial)}`, `refill_at = ${n(m.pki.refill_at)}`, `batch = ${n(m.pki.batch)}`, ...pairs(m.pkiExtra), "");
  }
  for (const s of m.stations) {
    lines.push("[[stations]]", `name = ${q(s.name)}`);
    if (s.kind === "rsu") lines.push("station_type = 15");
    else if (s.station_type !== undefined) lines.push(`station_type = ${n(s.station_type)}`);
    if (s.color) lines.push(`color = ${q(s.color)}`);
    lines.push(...pairs(s.extra));
    const moves = s.kind === "vehicle" && ((s.mobility === "route" && s.route.length >= 2) || s.mobility === "crossing");
    const env = pairs(s.envExtra);
    if (!moves || s.hasPosition) env.unshift(`VANETZA_LATITUDE = ${q(f(s.position[0]))}`, `VANETZA_LONGITUDE = ${q(f(s.position[1]))}`);
    if (env.length) lines.push(`env = ${inline(env)}`);
    if (s.kind === "vehicle" && s.pseudonyms && m.signed) {
      lines.push(`pseudonyms = ${inline([...pairs(s.pseudoExtra), ...silent(s)])}`);
    }
    if (moves && s.mobility === "route") {
      lines.push(`mobility = ${inline([`route = [${s.route.map(pt).join(", ")}]`, `speed_kmh = ${n(s.speed_kmh)}`, `start_s = ${n(s.start_s)}`, `loop = ${!!s.loop}`])}`);
    }
    if (moves && s.mobility === "crossing") {
      lines.push(`mobility = ${inline([`crossing = ${pt(s.crossing)}`, `arm_m = ${n(s.arm_m)}`, `start_arm = ${q(s.start_arm)}`, `speed_kmh = ${n(s.speed_kmh)}`, `start_s = ${n(s.start_s)}`])}`);
    }
    if (canMix(m, s)) {   // station scope: only what differs from the run scope
      const own = [];
      if (s.dmzOn !== m.dmz.all) own.push(`enabled = ${s.dmzOn}`);
      if (s.dmzOn && s.dmzMin !== "" && Number(s.dmzMin) !== Number(m.dmz.min_vehicles)) own.push(`min_vehicles = ${Math.floor(Number(s.dmzMin))}`);
      if (own.length) lines.push(`dynamic_mix_zone = ${inline(own)}`);
    }
    lines.push("");
  }
  const dynamic = m.dmz.has || m.dmz.all || m.stations.some((s) => canMix(m, s) && s.dmzOn);
  const moving = m.stations.some((s) => s.kind === "vehicle" && s.mobility !== "none");
  if (m.hasControl || anyPseudo || moving || m.zones.length || dynamic) {
    lines.push("[control]", ...pairs(m.controlExtra), "", "[control.client]", `mode = ${q(m.client.mode)}`);
    if (m.client.mode === "periodic") lines.push(`interval = ${n(m.client.interval)}`);
    lines.push(...pairs(m.clientExtra), "");
    if (dynamic) {
      lines.push("[control.dynamic_mix_zone]", `enabled = ${m.dmz.all}`, `min_vehicles = ${Math.floor(Number(m.dmz.min_vehicles) || 2)}`,
        `radius_m = ${n(m.dmz.radius_m)}`, `cooldown_s = ${n(m.dmz.cooldown_s)}`, ...pairs(m.dmz.extra), "");
    }
    for (const z of m.zones) {
      lines.push("[[control.mix_zones]]", `name = ${q(z.name)}`, `center = ${pt(z.center)}`, `radius_m = ${n(z.radius_m)}`,
        ...(Number(z.min_vehicles) > 1 ? [`min_vehicles = ${Math.floor(Number(z.min_vehicles))}`] : []), ...pairs(z.extra), "");
    }
  }
  if (m.eavesdropper.enabled) {
    lines.push("[eavesdropper]", `link_window = ${n(m.eavesdropper.link_window)}`,
      `link_by = [${m.eavesdropper.link_by.map(q).join(", ")}]`, ...pairs(m.eavExtra), "");
  }
  for (const [k, v] of Object.entries(m.extra)) if (isTable(v)) lines.push(`[${key(k)}]`, ...pairs(v), "");
  return lines.join("\n");
}

// The builder's model of a scenario (the parsed text, as /api/scenarios/parse returns it).
export function fromScenario(sc, center) {
  const table = (v) => (isTable(v) ? v : {});
  const point = (p, fallback) => (Array.isArray(p) && p.length === 2 && p.every((x) => typeof x === "number") ? [p[0], p[1]] : [...fallback]);
  const num = (v, fallback) => (typeof v === "number" ? v : fallback);
  const defaults = table(sc.defaults), pki = table(sc.pki), control = table(sc.control), client = table(control.client);
  const shared = table(defaults.pseudonyms);
  const opt = (v) => (typeof v === "number" ? v : "");   // a key that is not there stays ""
  const eav = table(sc.eavesdropper);
  const security = typeof defaults.security === "string" ? defaults.security : "none";
  const zone = control.dynamic_mix_zone;
  const dmz = { has: isTable(zone), all: isTable(zone) && zone.enabled !== false, min_vehicles: num(table(zone).min_vehicles, 2),
    radius_m: num(table(zone).radius_m, 50), cooldown_s: num(table(zone).cooldown_s, 30),
    extra: rest(table(zone), ["enabled", "min_vehicles", "radius_m", "cooldown_s"]) };
  const stations = (Array.isArray(sc.stations) ? sc.stations : []).filter(isTable).map((st, i) => {
    const env = table(st.env), mob = table(st.mobility), pseudo = isTable(st.pseudonyms) ? st.pseudonyms : null;
    const lat = parseFloat(env.VANETZA_LATITUDE), lon = parseFloat(env.VANETZA_LONGITUDE);
    const hasPosition = Number.isFinite(lat) && Number.isFinite(lon);
    const route = Array.isArray(mob.route) ? mob.route.map((p) => point(p, center)) : [];
    return {
      kind: st.station_type === 15 ? "rsu" : "vehicle", name: String(st.name ?? `station${i + 1}`),
      station_type: st.station_type === 15 ? undefined : st.station_type,
      extra: rest(st, ["name", "station_type", "env", "pseudonyms", "mobility", "dynamic_mix_zone", "color"]),
      color: typeof st.color === "string" && /^#[0-9a-fA-F]{6}$/.test(st.color) ? st.color : "",
      dmzOn: isTable(st.dynamic_mix_zone) ? st.dynamic_mix_zone.enabled !== false : dmz.all,
      dmzMin: num(table(st.dynamic_mix_zone).min_vehicles, ""),
      envExtra: rest(env, ["VANETZA_LATITUDE", "VANETZA_LONGITUDE"]), hasPosition,
      position: hasPosition ? [lat, lon] : [...center],
      pseudonyms: !!pseudo, silent_min_ms: opt(pseudo && pseudo.silent_min_ms), silent_max_ms: opt(pseudo && pseudo.silent_max_ms),
      pseudoExtra: rest(pseudo, ["silent_min_ms", "silent_max_ms"]),
      mobility: "route" in mob ? "route" : ("crossing" in mob ? "crossing" : "none"),
      route, speed_kmh: num(mob.speed_kmh, 50), loop: mob.loop === true, start_s: num(mob.start_s, 0),
      crossing: point(mob.crossing, center), arm_m: num(mob.arm_m, 150),
      start_arm: ["north", "east", "south", "west"].includes(mob.start_arm) ? mob.start_arm : "east",
    };
  });
  return {
    description: typeof sc.description === "string" ? sc.description : "",
    extra: rest(sc, ["description", "defaults", "pki", "stations", "control", "eavesdropper"]),
    signed: security !== "none", security: security !== "none" ? security : "certs-v3", defaultsExtra: rest(defaults, ["security", "pseudonyms"]),
    pseudo: { silent_min_ms: opt(shared.silent_min_ms), silent_max_ms: opt(shared.silent_max_ms),
      extra: rest(shared, ["silent_min_ms", "silent_max_ms"]) },
    hasPki: isTable(sc.pki) && pki.enabled !== false,
    pki: { initial: num(pki.initial, 8), refill_at: num(pki.refill_at, 2), batch: num(pki.batch, 8) },
    pkiExtra: rest(pki, ["initial", "refill_at", "batch", "enabled"]),
    stations,
    hasControl: isTable(sc.control), controlExtra: rest(control, ["client", "mix_zones", "dynamic_mix_zone"]), dmz,
    client: { mode: typeof client.mode === "string" ? client.mode : "periodic", interval: num(client.interval, 30) },
    clientExtra: rest(client, ["mode", "interval"]),
    zones: (Array.isArray(control.mix_zones) ? control.mix_zones : []).filter(isTable).map((z, i) => ({
      name: String(z.name ?? `zone${i}`), center: point(z.center, center), radius_m: num(z.radius_m, 40),
      min_vehicles: num(z.min_vehicles, 1), extra: rest(z, ["name", "center", "radius_m", "min_vehicles"]) })),
    eavesdropper: { enabled: isTable(sc.eavesdropper) && eav.enabled !== false, link_window: num(eav.link_window, 3),
      link_by: Array.isArray(eav.link_by) ? eav.link_by.filter((k) => k === "position" || k === "timing") : ["position", "timing"] },
    eavExtra: rest(eav, ["enabled", "link_window", "link_by"]),
  };
}

// Builds the forms and the map in `pane` for the parsed scenario `sc`. Returns the editor:
// toml() is the scenario as text, changed() whether anything was edited, showErrors(errors)
// marks the stations that validation complained about.
export async function builder(pane, sc) {
  CENTER = (await getUiConfig()).origin || CENTER;
  const m = fromScenario(sc, CENTER);
  const initial = toToml(m);
  let mode = null;   // what a map click sets: {what: "position"|"route"|"crossing"|"zone", target}
  const cards = h("div", {});
  const zonesBox = h("div", {});
  const modeLine = h("p", { class: "muted" }, "Click a 'set on map' button, then the map.");
  const mapEl = h("div", { class: "map" });
  const errorsBox = h("div", {});
  const layers = L.layerGroup();

  // ------------------------------------------------------------ inputs bound to the model
  const num = (obj, key, attrs) => h("input", { type: "number", value: obj[key], onchange: (e) => { obj[key] = Number(e.target.value); draw(); }, ...attrs });
  // a number that may be left empty ("": the key is not written); the placeholder shows what applies then
  const optNum = (obj, key, placeholder, after) => h("input", { type: "number", min: 0, value: obj[key], placeholder,
    onchange: (e) => { obj[key] = e.target.value === "" ? "" : Math.max(0, Math.floor(Number(e.target.value) || 0)); (after || draw)(); } });
  const inherited = (key) => String(m.pseudo[key] === "" ? 0 : m.pseudo[key]);
  const txt = (obj, key) => h("input", { value: obj[key], onchange: (e) => { obj[key] = e.target.value.trim(); renderCards(); } });
  const chk = (obj, key, after) => h("input", { type: "checkbox", checked: obj[key], onchange: (e) => { obj[key] = e.target.checked; (after || draw)(); } });
  const pick = (obj, key, options, after) => h("select", { onchange: (e) => { obj[key] = e.target.value; (after || draw)(); } },
    options.map((o) => h("option", { value: o, selected: obj[key] === o }, o)));
  const setOnMap = (what, target, label) => h("button", { type: "button", class: "secondary", onclick: () => {
    mode = { what, target };
    modeLine.textContent = `Click the map: ${label}` + (what === "route" ? " (each click adds a waypoint; 'clear route' starts over)" : "");
  } }, label);

  // run scope of the dynamic mix zone: "all vehicles" switches every vehicle on or off
  const allBox = h("input", { type: "checkbox", onchange: (e) => {
    m.dmz.all = e.target.checked;
    for (const s of m.stations) s.dmzOn = m.dmz.all;
    renderCards();
  } });

  function renderCards() {
    clear(cards);
    m.stations.forEach((s, i) => {
      const parts = [
        h("div", { class: "row" },
          h("label", {}, s.kind === "rsu" ? "RSU name" : "Vehicle name", txt(s, "name")),
          setOnMap("position", s, `place ${s.name}`),
          h("button", { type: "button", class: "danger", onclick: () => { m.stations.splice(i, 1); renderCards(); } }, "Remove")),
      ];
      if (s.kind === "vehicle") {
        parts.push(h("div", { class: "row" },
          h("label", { class: "inline" }, chk(s, "pseudonyms", renderCards), "pseudonyms (ID changes)"),
          s.pseudonyms ? [
            h("label", { title: SILENT_OWN }, "silent min ms", optNum(s, "silent_min_ms", inherited("silent_min_ms"))),
            h("label", { title: SILENT_OWN }, "silent max ms", optNum(s, "silent_max_ms", inherited("silent_max_ms")))] : null,
          h("label", {}, "Movement", pick(s, "mobility", ["none", "route", "crossing"], renderCards)),
          h("label", { title: "Colour of this vehicle and its route on the maps, here and in the run." }, "Colour",
            h("input", { type: "color", value: s.color || stationColor(5), onchange: (e) => { s.color = e.target.value; renderCards(); } })),
          s.color ? h("button", { type: "button", class: "secondary", title: "Back to the colour all vehicles have by default",
            onclick: () => { s.color = ""; renderCards(); } }, "default colour") : null));
        if (canMix(m, s)) {
          parts.push(h("div", { class: "row" },
            h("label", { class: "inline", title: DMZ_HELP }, chk(s, "dmzOn", renderCards), "dynamic mix zone"),
            s.dmzOn ? h("label", { title: "Vehicles that must be within the radius, this one included, for this vehicle to change. Empty: the value for all vehicles." },
              "min cars", h("input", { type: "number", min: 1, max: 50, step: 1, value: s.dmzMin, placeholder: m.dmz.min_vehicles,
                onchange: (e) => { s.dmzMin = e.target.value === "" ? "" : Math.max(1, Math.floor(Number(e.target.value) || 1)); draw(); } })) : null));
        }
        if (s.mobility === "route") {
          parts.push(h("div", { class: "row" }, setOnMap("route", s, `draw route of ${s.name}`),
            h("button", { type: "button", class: "secondary", onclick: () => { s.route = []; draw(); } }, "clear route"),
            h("span", { class: "muted" }, `${s.route.length} waypoint(s)`),
            h("label", {}, "km/h", num(s, "speed_kmh", { min: 1, max: 500 })), h("label", {}, "start s", num(s, "start_s", { min: 0, step: 0.5 })),
            h("label", { class: "inline" }, chk(s, "loop"), "loop")));
        }
        if (s.mobility === "crossing") {
          parts.push(h("div", { class: "row" }, setOnMap("crossing", s, `place crossing of ${s.name}`),
            h("label", {}, "arm m", num(s, "arm_m", { min: 10, max: 5000 })),
            h("label", {}, "start road", pick(s, "start_arm", ["north", "east", "south", "west"])),
            h("label", {}, "km/h", num(s, "speed_kmh", { min: 1, max: 500 })), h("label", {}, "start s", num(s, "start_s", { min: 0, step: 0.5 }))));
        }
      }
      const card = h("div", { class: "station colored", "data-name": s.name }, parts);
      card.style.borderLeftColor = s.color || stationColor(s.kind === "rsu" ? 15 : 5);   // as on the map
      cards.append(card);
    });
    const able = m.stations.filter((s) => canMix(m, s));
    allBox.checked = m.dmz.all;
    allBox.indeterminate = able.some((s) => s.dmzOn) && able.some((s) => !s.dmzOn);
    renderZones();
    draw();
  }

  function renderZones() {
    clear(zonesBox);
    m.zones.forEach((z, i) => zonesBox.append(h("div", { class: "row" },
      h("label", {}, "Mix zone", txt(z, "name")), h("label", {}, "radius m", num(z, "radius_m", { min: 5, max: 1000 })),
      h("label", { title: "A vehicle changes its pseudonym only once at least this many vehicles are inside the zone; one that leaves before keeps its pseudonym. 1: every vehicle changes on entry." },
        "min vehicles", num(z, "min_vehicles", { min: 1, max: 50, step: 1 })),
      setOnMap("zone", z, `place ${z.name}`),
      h("button", { type: "button", class: "danger", onclick: () => { m.zones.splice(i, 1); renderZones(); draw(); } }, "Remove"))));
  }

  // ------------------------------------------------------------ map
  const map = await makeMap(mapEl, CENTER, 16);
  layers.addTo(map);
  function draw() {
    layers.clearLayers();
    for (const s of m.stations) {
      const color = s.color || stationColor(s.kind === "rsu" ? 15 : 5);
      if (s.kind === "vehicle" && s.mobility === "route" && s.route.length) {
        L.polyline(s.route, { color, weight: 3 }).addTo(layers);
        L.circleMarker(s.route[0], { radius: 7, color, fillOpacity: 0.9 }).bindTooltip(s.name, { permanent: true, className: "station-label" }).addTo(layers);
      } else if (s.kind === "vehicle" && s.mobility === "crossing") {
        crossingLayer(s.crossing, s.arm_m, color).addTo(layers);
        const startEnd = { north: [1, 0], east: [0, 1], south: [-1, 0], west: [0, -1] }[s.start_arm];
        L.circleMarker(offset(s.crossing, startEnd[0] * s.arm_m, startEnd[1] * s.arm_m), { radius: 7, color, fillOpacity: 0.9 })
          .bindTooltip(s.name, { permanent: true, className: "station-label" }).addTo(layers);
      } else {
        L.circleMarker(s.position, { radius: s.kind === "rsu" ? 9 : 7, color, fillOpacity: 0.9 })
          .bindTooltip(s.name, { permanent: true, className: "station-label" }).addTo(layers);
      }
    }
    for (const z of m.zones) L.circle(z.center, { radius: z.radius_m, color: zoneColor(), fillOpacity: 0.12 }).addTo(layers);
  }
  map.on("click", (e) => {
    if (!mode) return;
    const p = [e.latlng.lat, e.latlng.lng];
    if (mode.what === "position") mode.target.position = p;
    else if (mode.what === "route") mode.target.route.push(p);
    else if (mode.what === "crossing") mode.target.crossing = p;
    else if (mode.what === "zone") mode.target.center = p;
    if (mode.what !== "route") { mode = null; modeLine.textContent = "Click a 'set on map' button, then the map."; }
    renderCards();
  });

  // ------------------------------------------------------------ errors from validation, by station
  function showErrors(errors) {
    for (const card of cards.children) card.classList.remove("field-error");
    clear(errorsBox);
    for (const e of errors) {
      const match = /^stations\[([^\]]+)\]/.exec(e.path || "");
      if (match) {
        const card = [...cards.children].find((c) => c.dataset.name === match[1]);
        if (card) card.classList.add("field-error");
      }
    }
  }

  const general = h("div", { class: "panel" },
    h("div", { class: "row" },
      h("label", {}, "Description", txt(m, "description")),
      h("label", { class: "inline" }, chk(m, "signed", () => { if (m.signed) m.hasPki = true; renderCards(); }), "signed messages (the run's own PKI)")),
    h("div", { class: "row" },
      h("label", {}, "certificates at start", num(m.pki, "initial", { min: 1, max: 256 })),
      h("label", {}, "refill at", num(m.pki, "refill_at", { min: 0, max: 255 })),
      h("label", {}, "batch", num(m.pki, "batch", { min: 1, max: 256 }))),
    h("div", { class: "row" },
      h("label", { title: SILENT_ALL }, "silent min ms, all vehicles", optNum(m.pseudo, "silent_min_ms", "0", renderCards)),
      h("label", { title: SILENT_ALL }, "silent max ms, all vehicles", optNum(m.pseudo, "silent_max_ms", "0", renderCards))),
    h("div", { class: "row" },
      h("label", {}, "Pseudonym change events", pick(m.client, "mode", ["manual", "periodic", "random", "once"])),
      h("label", {}, "every s (periodic)", num(m.client, "interval", { min: 1 }))),
    h("div", { class: "row" },
      h("label", { class: "inline", title: DMZ_HELP }, allBox, "dynamic mix zone: all vehicles"),
      h("label", { title: "Run scope: vehicles that must be within the radius, the vehicle itself included. A vehicle's own 'min cars' replaces it." },
        "min cars", num(m.dmz, "min_vehicles", { min: 1, max: 50, step: 1, onchange: (e) => { m.dmz.min_vehicles = Math.max(1, Math.floor(Number(e.target.value) || 1)); renderCards(); } })),
      h("label", {}, "radius m", num(m.dmz, "radius_m", { min: 1, max: 5000 })),
      h("label", { title: "Least time between two dynamic mix zone changes of a vehicle; also the earliest first change." },
        "cooldown s", num(m.dmz, "cooldown_s", { min: 1 }))));
  // the eavesdropper is a participant of its own, not a station and not a setting of the scenario
  const eavesdropperPanel = h("div", { class: "panel" }, h("h2", {}, "Eavesdropper"),
    h("p", { class: "muted" }, "A passive attacker that listens to every message and tries to follow each vehicle through its pseudonym changes."),
    h("div", { class: "observer" }, h("div", { class: "row" },
      h("label", { class: "inline" }, chk(m.eavesdropper, "enabled"), "listening"),
      h("label", { title: "How long after a pseudonym went silent the eavesdropper still continues its track with a new one." },
        "link window s", num(m.eavesdropper, "link_window", { min: 0, step: 0.5 })),
      h("label", { class: "inline", title: "Link a new pseudonym to an old one by where it appears." },
        h("input", { type: "checkbox", checked: m.eavesdropper.link_by.includes("position"), onchange: (e) => toggleLink("position", e.target.checked) }), "link by position"),
      h("label", { class: "inline", title: "Link a new pseudonym to an old one by the timing of its messages." },
        h("input", { type: "checkbox", checked: m.eavesdropper.link_by.includes("timing"), onchange: (e) => toggleLink("timing", e.target.checked) }), "link by timing"))));
  function toggleLink(kind, on) {
    m.eavesdropper.link_by = on ? [...new Set([...m.eavesdropper.link_by, kind])] : m.eavesdropper.link_by.filter((k) => k !== kind);
  }
  let rsus = m.stations.filter((s) => s.kind === "rsu").length, cars = m.stations.length - rsus;
  const unused = (name) => !m.stations.some((s) => s.name === name);
  function add(kind) {   // the next free rsuN / carN
    let station;
    do { station = newStation(kind, kind === "rsu" ? ++rsus : ++cars); } while (!unused(station.name));
    station.dmzOn = m.dmz.all;
    m.stations.push(station);
    renderCards();
  }
  pane.append(h("div", { class: "grid2" },
    h("div", {},
      general,
      h("div", { class: "panel" }, h("h2", {}, "Stations"), cards,
        h("div", { class: "row" },
          h("button", { type: "button", class: "secondary", onclick: () => add("rsu") }, "Add RSU"),
          h("button", { type: "button", class: "secondary", onclick: () => add("vehicle") }, "Add vehicle"))),
      h("div", { class: "panel" }, h("h2", {}, "Mix zones"), zonesBox,
        h("button", { type: "button", class: "secondary", onclick: () => { m.zones.push({ name: `zone${m.zones.length + 1}`, center: [...CENTER], radius_m: 40, min_vehicles: 1, extra: {} }); renderZones(); draw(); } }, "Add mix zone")),
      eavesdropperPanel),
    h("div", {},
      h("div", { class: "panel" }, modeLine, mapEl,
        h("p", { class: "legend muted" }, h("span", {}, h("i", { class: "dot rsu" }), "RSU"), h("span", {}, h("i", { class: "dot car" }), "vehicle"),
          h("span", {}, h("i", { class: "dot zone" }), "mix zone"))),
      errorsBox)));
  renderCards();
  setTimeout(() => map.invalidateSize(), 50);
  return { toml: () => toToml(m), changed: () => toToml(m) !== initial, showErrors };
}
