// Scenario builder: stations, routes, crossings and mix zones in forms and on a map; it
// writes the scenario as TOML for the same validation and start as scenario text.
import { clear, h } from "../dom.js";
import { crossingLayer, getUiConfig, makeMap, offset, stationColor, zoneColor } from "./mapview.js";
import { startControls } from "./newrun.js";

let CENTER = [40.208106, -8.4197756];   // replaced by the service's origin (ui-config)

function newStation(kind, n) {
  const vehicle = kind === "vehicle";
  return {
    kind, name: vehicle ? `car${n}` : (n > 1 ? `rsu${n}` : "rsu"),
    position: vehicle ? offset(CENTER, 0, 60 * n) : [...CENTER],
    pseudonyms: vehicle, silent_min_ms: 0, silent_max_ms: 0,
    mobility: vehicle ? "crossing" : "none", route: [], speed_kmh: 36, loop: true, start_s: 1.5 * (n - 1),
    crossing: [...CENTER], arm_m: 150, start_arm: ["east", "north", "west", "south"][(n - 1) % 4],
  };
}

export function toToml(m) {
  const q = (s) => JSON.stringify(String(s));
  const f = (x) => Number(x).toFixed(6);
  const pt = (p) => `[${f(p[0])}, ${f(p[1])}]`;
  const lines = [`description = ${q(m.description)}`, "", "[defaults]", `security = ${q(m.signed ? "certs-v3" : "none")}`, ""];
  const anyPseudo = m.signed && m.stations.some((s) => s.kind === "vehicle" && s.pseudonyms);
  if (m.signed) {
    lines.push("[pki]", `initial = ${m.pki.initial}`, `refill_at = ${m.pki.refill_at}`, `batch = ${m.pki.batch}`, "");
  }
  for (const s of m.stations) {
    lines.push("[[stations]]", `name = ${q(s.name)}`);
    if (s.kind === "rsu") lines.push("station_type = 15");
    if (s.kind === "rsu" || s.mobility === "none") {
      lines.push(`env = { VANETZA_LATITUDE = ${q(f(s.position[0]))}, VANETZA_LONGITUDE = ${q(f(s.position[1]))} }`);
    }
    if (s.kind === "vehicle" && s.pseudonyms && m.signed) {
      lines.push(`pseudonyms = { silent_min_ms = ${Number(s.silent_min_ms) || 0}, silent_max_ms = ${Number(s.silent_max_ms) || 0} }`);
    }
    if (s.kind === "vehicle" && s.mobility === "route" && s.route.length >= 2) {
      lines.push(`mobility = { route = [${s.route.map(pt).join(", ")}], speed_kmh = ${Number(s.speed_kmh)}, start_s = ${Number(s.start_s)}, loop = ${s.loop} }`);
    }
    if (s.kind === "vehicle" && s.mobility === "crossing") {
      lines.push(`mobility = { crossing = ${pt(s.crossing)}, arm_m = ${Number(s.arm_m)}, start_arm = ${q(s.start_arm)}, speed_kmh = ${Number(s.speed_kmh)}, start_s = ${Number(s.start_s)} }`);
    }
    lines.push("");
  }
  const moving = m.stations.some((s) => s.kind === "vehicle" && s.mobility !== "none");
  if (anyPseudo || moving || m.zones.length) {
    lines.push("[control]", "", "[control.client]", `mode = ${q(m.client.mode)}`);
    if (m.client.mode === "periodic") lines.push(`interval = ${Number(m.client.interval)}`);
    lines.push("");
    for (const z of m.zones) {
      lines.push("[[control.mix_zones]]", `name = ${q(z.name)}`, `center = ${pt(z.center)}`, `radius_m = ${Number(z.radius_m)}`, "");
    }
  }
  if (m.eavesdropper.enabled) {
    lines.push("[eavesdropper]", `link_window = ${Number(m.eavesdropper.link_window)}`,
      `link_by = [${m.eavesdropper.link_by.map(q).join(", ")}]`, "");
  }
  return lines.join("\n");
}

export async function builder(pane, session, editAsText) {
  CENTER = (await getUiConfig()).origin || CENTER;
  const m = {
    description: "built scenario", signed: true, pki: { initial: 8, refill_at: 2, batch: 8 },
    client: { mode: "manual", interval: 20 }, zones: [{ name: "crossing", center: [...CENTER], radius_m: 40 }],
    eavesdropper: { enabled: true, link_window: 15, link_by: ["position", "timing"] },
    stations: [newStation("rsu", 1), newStation("vehicle", 1), newStation("vehicle", 2)],
  };
  let mode = null;   // what a map click sets: {what: "position"|"route"|"crossing"|"zone", target}
  const cards = h("div", {});
  const zonesBox = h("div", {});
  const modeLine = h("p", { class: "muted" }, "Click a 'set on map' button, then the map.");
  const mapEl = h("div", { class: "map" });
  const errorsBox = h("div", {});
  const layers = L.layerGroup();

  // ------------------------------------------------------------ inputs bound to the model
  const num = (obj, key, attrs) => h("input", { type: "number", value: obj[key], ...attrs, onchange: (e) => { obj[key] = Number(e.target.value); draw(); } });
  const txt = (obj, key) => h("input", { value: obj[key], onchange: (e) => { obj[key] = e.target.value.trim(); renderCards(); } });
  const chk = (obj, key, after) => h("input", { type: "checkbox", checked: obj[key], onchange: (e) => { obj[key] = e.target.checked; (after || draw)(); } });
  const pick = (obj, key, options, after) => h("select", { onchange: (e) => { obj[key] = e.target.value; (after || draw)(); } },
    options.map((o) => h("option", { value: o, selected: obj[key] === o }, o)));
  const setOnMap = (what, target, label) => h("button", { type: "button", class: "secondary", onclick: () => {
    mode = { what, target };
    modeLine.textContent = `Click the map: ${label}` + (what === "route" ? " (each click adds a waypoint; 'clear route' starts over)" : "");
  } }, label);

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
          s.pseudonyms ? [h("label", {}, "silent min ms", num(s, "silent_min_ms", { min: 0 })),
            h("label", {}, "silent max ms", num(s, "silent_max_ms", { min: 0 }))] : null,
          h("label", {}, "Movement", pick(s, "mobility", ["none", "route", "crossing"], renderCards))));
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
      cards.append(h("div", { class: "station", "data-name": s.name }, parts));
    });
    renderZones();
    draw();
  }

  function renderZones() {
    clear(zonesBox);
    m.zones.forEach((z, i) => zonesBox.append(h("div", { class: "row" },
      h("label", {}, "Mix zone", txt(z, "name")), h("label", {}, "radius m", num(z, "radius_m", { min: 5, max: 1000 })),
      setOnMap("zone", z, `place ${z.name}`),
      h("button", { type: "button", class: "danger", onclick: () => { m.zones.splice(i, 1); renderZones(); draw(); } }, "Remove"))));
  }

  // ------------------------------------------------------------ map
  const map = await makeMap(mapEl, CENTER, 16);
  layers.addTo(map);
  function draw() {
    layers.clearLayers();
    for (const s of m.stations) {
      const color = stationColor(s.kind === "rsu" ? 15 : 5);
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
      h("label", { class: "inline" }, chk(m, "signed", renderCards), "signed messages (the run's own PKI)")),
    h("div", { class: "row" },
      h("label", {}, "certificates at start", num(m.pki, "initial", { min: 1, max: 256 })),
      h("label", {}, "refill at", num(m.pki, "refill_at", { min: 0, max: 255 })),
      h("label", {}, "batch", num(m.pki, "batch", { min: 1, max: 256 }))),
    h("div", { class: "row" },
      h("label", {}, "Pseudonym change events", pick(m.client, "mode", ["manual", "periodic", "random"])),
      h("label", {}, "every s (periodic)", num(m.client, "interval", { min: 1 }))),
    h("div", { class: "row" },
      h("label", { class: "inline" }, chk(m.eavesdropper, "enabled"), "eavesdropper"),
      h("label", {}, "link window s", num(m.eavesdropper, "link_window", { min: 0, step: 0.5 })),
      h("label", { class: "inline" }, h("input", { type: "checkbox", checked: true, onchange: (e) => toggleLink("position", e.target.checked) }), "position"),
      h("label", { class: "inline" }, h("input", { type: "checkbox", checked: true, onchange: (e) => toggleLink("timing", e.target.checked) }), "timing")));
  function toggleLink(kind, on) {
    m.eavesdropper.link_by = on ? [...new Set([...m.eavesdropper.link_by, kind])] : m.eavesdropper.link_by.filter((k) => k !== kind);
  }
  let rsus = 1, cars = 2;
  pane.append(h("div", { class: "grid2" },
    h("div", {},
      general,
      h("div", { class: "panel" }, h("h2", {}, "Stations"), cards,
        h("div", { class: "row" },
          h("button", { type: "button", class: "secondary", onclick: () => { m.stations.push(newStation("rsu", ++rsus)); renderCards(); } }, "Add RSU"),
          h("button", { type: "button", class: "secondary", onclick: () => { m.stations.push(newStation("vehicle", ++cars)); renderCards(); } }, "Add vehicle"))),
      h("div", { class: "panel" }, h("h2", {}, "Mix zones"), zonesBox,
        h("button", { type: "button", class: "secondary", onclick: () => { m.zones.push({ name: `zone${m.zones.length + 1}`, center: [...CENTER], radius_m: 40 }); renderZones(); draw(); } }, "Add mix zone"))),
    h("div", {},
      h("div", { class: "panel" }, modeLine, mapEl,
        h("p", { class: "legend muted" }, h("span", {}, h("i", { class: "dot rsu" }), "RSU"), h("span", {}, h("i", { class: "dot car" }), "vehicle"),
          h("span", {}, h("i", { class: "dot zone" }), "mix zone"))),
      h("div", { class: "panel" },
        h("div", { class: "row" }, h("button", { type: "button", class: "secondary", onclick: () => editAsText(toToml(m)) }, "Edit as text")),
        errorsBox,
        startControls(session, () => ({ scenario_text: toToml(m), overrides: [] }), showErrors)))));
  renderCards();
  setTimeout(() => map.invalidateSize(), 50);
}
