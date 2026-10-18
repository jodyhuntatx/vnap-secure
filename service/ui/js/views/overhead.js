// Results: privacy and cost of a stopped run's pseudonym changes (GET /api/runs/{id}/overhead, from
// sim/vnapsim/overhead.py): headline figures, a timeline per vehicle, and the changes and vehicles.
import { badge, clear, h, table } from "../dom.js";

export function overheadPanel(panel, o) {
  const s = o.summary;
  const short = (name) => String(name).replace(/-i\d+$/, "");
  const pct = (x) => (x === null || x === undefined ? "–" : `${Math.round(x * 100)} %`);
  const ms = (x) => (x === null || x === undefined ? "–" : `${x} ms`);
  const hint = (text, title) => h("span", { class: "hint", title }, text);
  const figure = (value, label, title) => h("div", { class: "figure", title }, h("div", { class: "value" }, value), h("div", { class: "label" }, label));
  const recv = !!o.reference_receiver;
  clear(panel).append(h("h2", {}, "Privacy and cost of the pseudonym changes"),
    h("p", { class: "muted" }, "Every pseudonym change of the run, what the eavesdropper made of it and what the other stations missed meanwhile. ",
      recv ? "Reception is measured at the eavesdropper, which hears every frame on the lossless link like any receiver."
        : "No CAMs were recorded by an eavesdropper, so only the figures from the stations' logs are shown."));
  panel.append(h("div", { class: "figures" },
    figure(s.tracking_success === null ? "–" : pct(s.tracking_success), `tracking success (${s.linked_correctly ?? 0} of ${s.changes} changes followed)`,
      "share of the pseudonym changes the eavesdropper linked correctly: the lower, the better the privacy"),
    figure(String(s.linked_wrongly ?? "–"), "changes linked to the wrong vehicle", "the eavesdropper's mistakes: it joined two different vehicles"),
    figure(pct(s.sync_ratio), "changes synchronized", `share of changes with another vehicle changing within ${o.settings.sync_window_s} s: what a mix zone aims at`),
    recv ? figure(ms(s.gap_ms_max), `longest reception gap (mean ${ms(s.gap_ms_mean)})`, "per change: from the last CAM under the old pseudonym to the first under the new one; the vehicle is missing from every receiver's view meanwhile") : null,
    recv ? figure(pct(s.stale_share), "time information was stale", `share of time a receiver's latest CAM of a vehicle was older than ${o.settings.stale_s} s (mean over vehicles)`) : null,
    recv ? figure(pct(s.delivery_ratio), "delivery ratio", "CAMs received against CAMs expected at each vehicle's usual rate; on the lossless link every shortfall comes from changes and silent periods") : null,
    recv ? figure(pct(s.full_cert_share), `CAMs with the full certificate (mean ${s.cam_bytes_mean ?? "–"} bytes)`, "the rest are signed with the certificate's 8-byte digest") : null));
  if (recv && o.duration_s) panel.append(timeline(o, short));
  panel.append(h("h3", {}, "Changes"), changesTable(o.changes, short, recv));
  panel.append(h("h3", {}, "Vehicles"), table(
    [hint("Vehicle", ""), hint("Changes", "pseudonym changes made (and refused, e.g. for lack of certificates)"),
      hint("Pseudonym lifetime", "time between two changes: mean (shortest–longest)"),
      hint("CAM interval", "the usual (median) time between two CAMs of this vehicle"),
      hint("Gap p95 / max", "time between consecutive CAMs at the receiver: 95th percentile and longest"),
      hint("Stale", `share of time its latest CAM was older than ${o.settings.stale_s} s`),
      hint("Delivery", "CAMs received against CAMs expected"), hint("Full cert.", "share of CAMs carrying the full certificate"),
      hint("CAM bytes", "mean size of a CAM frame"), hint("Refill", "certificate batches received and the mean time from request to installation")],
    o.vehicles.map((v) => [short(v.vehicle), `${v.changes}${v.rejected ? ` (${v.rejected} refused)` : ""}`,
      v.lifetime_s_mean === null ? "–" : `${v.lifetime_s_mean.toFixed(1)} s (${v.lifetime_s_min.toFixed(1)}–${v.lifetime_s_max.toFixed(1)})`,
      ms(v.cam_interval_ms_median), v.gap_ms_p95 === null ? "–" : `${v.gap_ms_p95} / ${v.gap_ms_max} ms`, pct(v.stale_share),
      pct(v.delivery_ratio), pct(v.full_cert_share), v.cam_bytes_mean ?? "–",
      v.refill && v.refill.batches ? `${v.refill.batches} batch(es), ${v.refill.refresh_ms_mean} ms` : "–"])));
  panel.append(h("p", { class: "muted" }, "Not measured yet: position error and conflict exposure (they need the vehicles' true positions), ",
    "verification failures, warnings and CPU load. See Specs/VNAP-Secure-Pseudonym-Change-Overhead-Design.docx."));
}

// timeline class and badge class per outcome: kept (green), followed (red), confused (the zone colour, apart from silence)
const OUTCOME = { "not linked": ["kept", "ok"], "linked correctly": ["followed", "bad"], "linked wrongly": ["confused", "confused"] };

function changesTable(changes, short, recv) {
  const columns = [
    ["Time", "seconds from the start of the recording (the first frame or change)", (c) => c.t, (c) => (c.t === null ? "–" : `${c.t.toFixed(1)} s`)],
    ["Vehicle", "", (c) => c.vehicle, (c) => short(c.vehicle)],
    ["Silent", "the silent period the vehicle logged after this change", (c) => c.silent_ms, (c) => (c.silent_ms ? `${c.silent_ms} ms` : "–")],
    ...(recv ? [["Gap", "last CAM under the old pseudonym to the first under the new one", (c) => c.gap_ms ?? -1, (c) => (c.gap_ms === null ? "–" : `${c.gap_ms} ms`)],
      ["CAMs missing", "CAMs the vehicle would have sent in the gap at its usual rate", (c) => c.cams_missing ?? -1, (c) => c.cams_missing ?? "–"]] : []),
    ["Eavesdropper", "followed (linked correctly), confused (linked to another vehicle) or not linked", (c) => c.outcome,
      (c) => h("span", {}, badge(c.outcome, OUTCOME[c.outcome][1]), c.technique ? ` by ${c.technique}` : "", c.linked_to ? ` to ${short(c.linked_to)}` : "")],
    ["Synchronized with", "vehicles that changed within the synchronization window", (c) => c.synchronized_with.length,
      (c) => c.synchronized_with.map(short).join(", ") || "–"],
  ];
  let by = 0, dir = 1;
  const body = h("tbody", {});
  const render = () => {
    const rows = [...changes].sort((a, b) => { const x = columns[by][2](a), y = columns[by][2](b); return (x > y ? 1 : x < y ? -1 : 0) * dir; });
    clear(body).append(...rows.map((c) => h("tr", {}, columns.map((col) => h("td", {}, col[3](c))))));
  };
  const head = h("tr", {}, columns.map((col, i) => h("th", { class: "sortable", title: `${col[1] ? col[1] + ". " : ""}Click to sort.`,
    onclick: () => { dir = by === i ? -dir : 1; by = i; render(); } }, col[0])));
  render();
  return h("div", { class: "scroll" }, h("table", {}, h("thead", {}, head), body));
}

// one lane per vehicle: reception, gaps (silent or not) and the changes coloured by the eavesdropper's outcome
function timeline(o, short) {
  const NS = "http://www.w3.org/2000/svg";
  const svg = (tag, attrs, ...kids) => {
    const el = document.createElementNS(NS, tag);
    for (const [k, v] of Object.entries(attrs || {})) el.setAttribute(k, v);
    for (const kid of kids) el.append(typeof kid === "string" ? document.createTextNode(kid) : kid);
    return el;
  };
  const W = 1000, left = 90, lane = 26, top = 8, d = o.duration_s;
  const x = (t) => left + Math.max(0, Math.min(1, t / d)) * (W - left - 10);
  const height = top + o.timeline.length * lane + 22;
  const root = svg("svg", { class: "timeline", viewBox: `0 0 ${W} ${height}`, role: "img", "aria-label": "timeline of reception and pseudonym changes" });
  o.timeline.forEach((v, i) => {
    const y = top + i * lane;
    root.append(svg("text", { x: 4, y: y + 15 }, short(v.vehicle)));
    if (v.first !== null) root.append(svg("rect", { class: "recv", x: x(v.first), y: y + 6, width: Math.max(1, x(v.last) - x(v.first)), height: 12 }));
    for (const [a, b, kind] of v.gaps) {
      root.append(svg("rect", { class: kind, x: x(a), y: y + 6, width: Math.max(1.5, x(b) - x(a)), height: 12 },
        svg("title", {}, `${kind === "silent" ? "silent period" : "gap"}: ${(b - a).toFixed(1)} s from ${a.toFixed(1)} s`)));
    }
    for (const [t, outcome] of v.changes) {
      if (t === null) continue;
      root.append(svg("line", { class: "change-line", x1: x(t), x2: x(t), y1: y + 3, y2: y + 21 }),
        svg("circle", { class: OUTCOME[outcome][0], cx: x(t), cy: y + 12, r: 4 }, svg("title", {}, `${t.toFixed(1)} s: ${outcome}`)));
    }
  });
  const axisY = top + o.timeline.length * lane + 4;
  root.append(svg("line", { class: "axis", x1: left, x2: W - 10, y1: axisY, y2: axisY }));
  const step = [10, 30, 60, 120, 300, 600, 1800].find((s) => d / s <= 10) || 3600;
  for (let t = 0; t <= d; t += step) {
    root.append(svg("text", { x: x(t), y: axisY + 14, "text-anchor": "middle" }, t >= 60 ? `${Math.floor(t / 60)}:${String(Math.round(t % 60)).padStart(2, "0")}` : `${t} s`));
  }
  return h("div", {}, h("div", { class: "scroll" }, root),
    h("p", { class: "legend muted" }, h("span", {}, h("i", { class: "swatch recv" }), "receiving"),
      h("span", {}, h("i", { class: "swatch silent" }), "silent period"), h("span", {}, h("i", { class: "swatch gap" }), "other gap"),
      h("span", {}, h("i", { class: "dot kept" }), "change not linked"), h("span", {}, h("i", { class: "dot followed" }), "followed"),
      h("span", {}, h("i", { class: "dot confused" }), "linked to another vehicle")));
}
