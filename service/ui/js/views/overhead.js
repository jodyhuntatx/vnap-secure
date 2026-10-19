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
  const outageS = (o.outages || []).reduce((t, x) => t + x.s, 0);
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
    s.followed_s_max !== null && s.followed_s_max !== undefined
      ? figure(`${s.followed_s_max} s`, `longest time followed (mean ${s.followed_s_mean} s)${s.followed_m_max ? `, ${Math.round(s.followed_m_max)} m` : ""}`,
        "a chain of pseudonyms the eavesdropper linked one to the next: how long (and how far) it tracked a vehicle without a break") : null,
    recv && o.positions ? figure(s.pos_error_m_p95 === null ? "–" : `${s.pos_error_m_p95} m`, `position error during gaps (extended: ${s.pos_error_m_p95_extended ?? "–"} m)`,
      "95th percentile of the distance between a receiver's last known position of the vehicle and its true position, during reception gaps; extended: the last position moved on by speed and heading") : null,
    recv ? figure(pct(s.full_cert_share), `CAMs with the full certificate (mean ${s.cam_bytes_mean ?? "–"} bytes)`,
      `the rest are signed with the certificate's 8-byte digest${s.cert_bytes ? ` instead of the ${s.cert_bytes}-byte certificate` : ""}`) : null,
    o.outages && o.outages.length ? figure(String(o.outages.length), `outage${o.outages.length === 1 ? "" : "s"}, ${Math.round(outageS)} s (${s.outage_changes} change${s.outage_changes === 1 ? "" : "s"} left out)`,
      "stretches in which the receiver heard nothing although a station should have sent, or the position recorder stalled: the host, not the stations. Gaps, stale time and delivery leave them out") : null));
  if (o.outages && o.outages.length) {
    // red when the outages cover a noticeable part of the recording, a plain note otherwise
    panel.append(h("p", { class: outageS > 0.05 * (o.duration_s || 0) ? "errors" : "muted" }, `The host stalled or the receiver heard nothing for ${Math.round(outageS)} s `,
      `(${o.outages.map((x) => `${x.from.toFixed(0)}–${x.to.toFixed(0)} s`).slice(0, 6).join(", ")}${o.outages.length > 6 ? ", …" : ""}). `,
      "Changes in those stretches are marked \"outage\" and left out of the figures; rerun if they cover much of the run."));
  }
  if (recv && o.duration_s) panel.append(timeline(o, short));
  panel.append(h("h3", {}, "Changes"), changesTable(o.changes, short, recv));
  panel.append(h("h3", {}, "Vehicles"), table(
    [hint("Vehicle", ""), hint("Changes", "pseudonym changes made (and refused, e.g. for lack of certificates)"),
      hint("Pseudonym lifetime", "time between two changes: mean (shortest–longest)"),
      hint("CAM interval", "the usual (median) time between two CAMs of this vehicle"),
      hint("Gap p95 / max", "time between consecutive CAMs at the receiver: 95th percentile and longest"),
      hint("Stale", `share of time its latest CAM was older than ${o.settings.stale_s} s`),
      hint("Delivery", "CAMs received against CAMs expected"), hint("Full cert.", "share of CAMs carrying the full certificate"),
      hint("CAM bytes", "mean size of a CAM frame"),
      ...(o.positions ? [hint("Position error p95", "95th percentile of the position error during this vehicle's reception gaps (last known / extended)")] : []),
      hint("Followed", "longest time the eavesdropper followed this vehicle through linked pseudonyms"),
      hint("Pool", `certificates held at most and their storage${s.cert_bytes ? ` (${s.cert_bytes}-byte certificate and ${s.key_bytes}-byte key file each)` : ""}`),
      hint("Refill", "certificate batches received and the mean time from request to installation")],
    o.vehicles.map((v) => [short(v.vehicle), `${v.changes}${v.rejected ? ` (${v.rejected} refused)` : ""}`,
      v.lifetime_s_mean === null ? "–" : `${v.lifetime_s_mean.toFixed(1)} s (${v.lifetime_s_min.toFixed(1)}–${v.lifetime_s_max.toFixed(1)})`,
      ms(v.cam_interval_ms_median), v.gap_ms_p95 === null ? "–" : `${v.gap_ms_p95} / ${v.gap_ms_max} ms`, pct(v.stale_share),
      pct(v.delivery_ratio), pct(v.full_cert_share), v.cam_bytes_mean ?? "–",
      ...(o.positions ? [v.pos_error_m_p95 === null ? "–" : `${v.pos_error_m_p95} / ${v.pos_error_m_p95_extended} m`] : []),
      v.followed_s_max === null || v.followed_s_max === undefined ? "–" : `${v.followed_s_max} s${v.followed_m_max ? ` (${Math.round(v.followed_m_max)} m)` : ""}`,
      v.pool_peak ? `${v.pool_peak}${v.pool_bytes ? ` (${(v.pool_bytes / 1024).toFixed(1)} KiB)` : ""}` : "–",
      v.refill && v.refill.batches ? `${v.refill.batches} batch(es), ${v.refill.refresh_ms_mean} ms` : "–"])));
  panel.append(h("p", { class: "muted" }, o.positions ? "" : "No recorded true positions in this run, so no position error. ",
    "Not measured yet: conflict exposure, verification failures, warnings and CPU load. ",
    "See Specs/VNAP-Secure-Pseudonym-Change-Overhead-Design.docx."));
}

// timeline class and badge class per outcome: kept (green), followed (red), confused (the zone colour, apart from silence)
const OUTCOME = { "not linked": ["kept", "ok"], "linked correctly": ["followed", "bad"], "linked wrongly": ["confused", "confused"] };

function changesTable(changes, short, recv) {
  const columns = [
    ["Time", "seconds from the start of the recording (the first frame or change)", (c) => c.t, (c) => (c.t === null ? "–" : `${c.t.toFixed(1)} s`)],
    ["Vehicle", "", (c) => c.vehicle, (c) => short(c.vehicle)],
    ["Silent", "the silent period the vehicle logged after this change", (c) => c.silent_ms, (c) => (c.silent_ms ? `${c.silent_ms} ms` : "–")],
    ...(recv ? [["Gap", "last CAM under the old pseudonym to the first under the new one", (c) => c.gap_ms ?? -1, (c) => (c.gap_ms === null ? "–" : `${c.gap_ms} ms`)],
      ["CAMs missing", "CAMs the vehicle would have sent in the gap at its usual rate", (c) => c.cams_missing ?? -1, (c) => c.cams_missing ?? "–"],
      ["Explained", "does the silent period plus two CAM intervals explain the gap? outage: the receiver or the host caused it",
        (c) => (c.outage ? 2 : c.explained === false ? 1 : 0),
        (c) => (c.outage ? badge("outage", "warn") : c.explained === false ? badge(`+${c.excess_ms} ms`, "bad") : c.explained ? "yes" : "–")],
      ...(changes.some((c) => c.pos_error_m_max !== null && c.pos_error_m_max !== undefined)
        ? [["Position error", "largest error of the last known position during the gap (extended by speed and heading)",
          (c) => c.pos_error_m_max ?? -1, (c) => (c.pos_error_m_max === null ? "–" : `${c.pos_error_m_max} m (${c.pos_error_m_max_extended} m)`)]] : []),
      ["History lost", "time and CAMs under the old pseudonym that a receiver can no longer attach to the vehicle",
        (c) => c.history_s ?? -1, (c) => (c.history_s === null || c.history_s === undefined ? "–" : `${c.history_s.toFixed(1)} s, ${c.history_cams} CAMs`)]] : []),
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
        svg("title", {}, `${kind === "silent" ? "silent period" : kind === "outage" ? "outage (host or receiver)" : "gap"}: ${(b - a).toFixed(1)} s from ${a.toFixed(1)} s`)));
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
      h("span", {}, h("i", { class: "swatch outage" }), "outage"),
      h("span", {}, h("i", { class: "dot kept" }), "change not linked"), h("span", {}, h("i", { class: "dot followed" }), "followed"),
      h("span", {}, h("i", { class: "dot confused" }), "linked to another vehicle")));
}
