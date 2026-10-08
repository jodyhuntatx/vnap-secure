// One run: overview, live map, event stream, pseudonym control and results.
import { get, post } from "../api.js";
import { badge, clear, every, h, notice, onLeave, remaining, table, time } from "../dom.js";
import { crossingLayer, makeMap, stationColor, zoneColor } from "./mapview.js";

const TABS = ["Overview", "Map", "Events", "Control", "Results"];

export async function runView(app, session, runId) {
  let run = await get(`/runs/${runId}`);
  const canWrite = session.user.role !== "viewer" && (run.owner === session.user.username || session.user.role === "admin");
  const title = h("h1", {});
  const tabs = h("div", { class: "tabs" });
  const pane = h("div", {});
  app.append(title, tabs, pane);
  let stopTab = () => {};
  let description = null;   // from the scenario, fetched once by the overview
  const running = () => run.state === "running";
  const needsRunning = () => h("p", { class: "panel muted" }, `This needs a running run (the run is ${run.state}).`);

  function setTitle() {
    clear(title);
    title.append(`${run.scenario} `, badge(run.state), h("span", { class: "muted" }, `  run ${run.id}`));
  }
  function select(name) {
    stopTab();
    stopTab = () => {};
    for (const b of tabs.children) b.classList.toggle("active", b.textContent === name);
    clear(pane);
    const views = { Overview: overview, Map: mapTab, Events: eventsTab, Control: controlTab, Results: resultsTab };
    Promise.resolve(views[name]()).catch((e) => notice(e.message, "error"));
  }
  for (const name of TABS) tabs.append(h("button", { type: "button", onclick: () => select(name) }, name));
  setTitle();
  select("Overview");
  every(5000, async () => {
    try { run = await get(`/runs/${runId}`); setTitle(); } catch (e) { /* keep the last state */ }
  });


  // ------------------------------------------------------------ overview
  async function overview() {
    const box = h("div", {});
    pane.append(box);
    if (description === null) {
      try { description = (await get(`/runs/${runId}/layout`)).description || ""; } catch (e) { description = ""; }
    }
    async function render() {
      clear(box);
      const actions = [];
      if (canWrite && ["queued", "starting", "running"].includes(run.state)) {
        actions.push(h("button", { class: "danger", type: "button", onclick: async () => {
          try { await post(`/runs/${runId}/stop`); notice("stopping"); } catch (e) { notice(e.message, "error"); }
        } }, "Stop run"));
      }
      box.append(h("div", { class: "panel" },
        description ? h("p", { class: "description" }, description) : null,
        table(["Owner", "Instance", "Started", "Time left", "Image", "Seed", "Shared with"], [[
          run.owner, run.instance ?? "", time(run.started_at), running() ? remaining(run.deadline) : (run.stop_reason || ""),
          run.image || "", run.seed ?? "", (run.shared_with || []).join(", ")]]),
        run.overrides.length ? h("p", {}, "Overrides: ", run.overrides.map((o) => h("code", {}, o))) : null,
        run.error ? h("p", { class: "errors" }, run.error) : null,
        h("div", { class: "row" }, actions, canWrite ? shareForm() : null)));
      const s = run.status;
      if (!s) return;
      box.append(h("div", { class: "panel" }, h("h2", {}, "Stations ", badge(s.health)),
        table(["Station", "ID", "State", "Security", "Pseudonym", "Refill", "Cert checks"], s.stations.map((st) => {
          const p = st.pseudonym;
          const rf = p && p.refill;
          const bad = st.chain.filter((c) => !c.ok).length;
          return [st.name, p && p.id_change ? p.id_change.station_id : st.station_id, badge(st.state, st.state === "running" ? "ok" : "bad"),
            st.security.entity,
            p ? `index ${p.index}/${p.pool_size}, ${p.changes} change(s)` : "",
            rf ? `${rf.unused} unused, ${rf.batches} batch(es)${rf.batches ? `, refresh ≤ ${rf.refresh_ms_max} ms` : ""}${rf.starved ? `, ${rf.starved} starved` : ""}` : "",
            bad ? badge(`${bad} failed`, "bad") : badge(`${st.chain.length} OK`, "ok")];
        }))));
      const k = s.control && s.control.pki;
      const lines = [];
      if (k) lines.push(`Run PKI: ${k.state}, provisioned in ${k.provisioned_ms} ms, ${k.batches} refill batch(es)` +
        (k.batches ? `, issue mean ${k.issue_ms_mean} ms, queue max ${k.queue_ms_max} ms` : ""));
      for (const o of s.observers || []) {
        if ("tracks" in o) lines.push(`Eavesdropper: ${o.tracks} track(s), ${o.pseudonyms} pseudonym(s), ${o.linked_changes} linked change(s)`);
      }
      if (lines.length) box.append(h("div", { class: "panel" }, lines.map((l) => h("p", {}, l))));
      if (s.warnings.length) box.append(h("ul", { class: "errors" }, s.warnings.map((w) => h("li", {}, w))));
    }
    await render();
    const id = setInterval(render, 5000);
    stopTab = () => clearInterval(id);
  }

  function shareForm() {
    const who = h("input", { placeholder: "username" });
    return h("div", { class: "row" }, h("label", {}, "Share with", who),
      h("button", { type: "button", class: "secondary", onclick: async () => {
        try { run = await post(`/runs/${runId}/share`, { username: who.value.trim() }); notice(`shared with ${who.value}`); } catch (e) { notice(e.message, "error"); }
      } }, "Share"));
  }

  // ------------------------------------------------------------ map
  async function mapTab() {
    const layout = await get(`/runs/${runId}/layout`);
    const el = h("div", { class: "map" });
    const info = h("p", { class: "muted" });
    const movable = layout.stations.filter((s) => Object.keys(s.mobility).length);
    let target = null;
    const chooser = canWrite && movable.length ? h("label", {}, "Click the map to move",
      h("select", { onchange: (e) => { target = e.target.value || null; } },
        h("option", { value: "" }, "(nobody)"), movable.map((s) => h("option", { value: s.name }, s.name)))) : null;
    pane.append(h("div", { class: "panel" }, h("div", { class: "row" }, chooser, info), el,
      h("p", { class: "legend muted" }, h("span", {}, h("i", { class: "dot rsu" }), "RSU"), h("span", {}, h("i", { class: "dot car" }), "vehicle"),
        h("span", {}, h("i", { class: "dot zone" }), "mix zone"))));
    const first = layout.stations[0] ? layout.stations[0].start : [40, -8];
    const map = await makeMap(el, first, 16);
    setTimeout(() => map.invalidateSize(), 50);
    for (const z of layout.mix_zones) L.circle(z.center, { radius: z.radius_m, color: zoneColor(), fillOpacity: 0.12 }).addTo(map);
    const markers = {};
    for (const s of layout.stations) {
      const color = stationColor(s.station_type);
      if (s.mobility.route) L.polyline(s.mobility.route, { color, weight: 2, opacity: 0.6 }).addTo(map);
      if (s.mobility.crossing) crossingLayer(s.mobility.crossing, s.mobility.arm_m || 150, color).addTo(map);
      markers[s.name] = L.circleMarker(s.start, { radius: s.station_type === 15 ? 9 : 7, color, fillOpacity: 0.9 })
        .bindTooltip(s.name, { permanent: true, className: "station-label" }).addTo(map);
    }
    map.on("click", async (e) => {
      if (!target) return;
      try {
        await post(`/runs/${runId}/stations/${target}/position`, { lat: e.latlng.lat, lon: e.latlng.lng, speed: 0 });
        notice(`${target} moved`);
      } catch (err) { notice(err.message, "error"); }
    });
    if (!running()) { info.textContent = `Layout only: the run is ${run.state}.`; return; }
    let busy = false;
    async function refresh() {
      if (busy) return;   // the previous poll is still out
      busy = true;
      try {
        const pos = await get(`/runs/${runId}/positions`);
        for (const [name, p] of Object.entries(pos)) if (markers[name]) markers[name].setLatLng([p.lat, p.lon]);
        info.textContent = `${Object.keys(pos).length} moving station(s), updated ${new Date().toLocaleTimeString()}`;
      } catch (e) { info.textContent = e.message; }
      busy = false;
    }
    await refresh();
    const id = setInterval(refresh, 2000);
    stopTab = () => clearInterval(id);
  }

  // ------------------------------------------------------------ events
  function eventsTab() {
    if (!running()) { pane.append(needsRunning()); return; }
    const kinds = ["pseudonym", "idchange", "pki", "chain", "error"];
    const LABELS = { pseudonym: "pseudonym", idchange: "ID change", pki: "refill", chain: "cert check", error: "error" };
    const HINTS = {
      pseudonym: "pseudonym certificate changes and control answers",
      idchange: "identifier changes: MAC/GN address, stationId, ID-LOCK, silent periods",
      pki: "certificate refill: batch requests to the run's PKI and their installation",
      chain: "certificate checks: the station verifies a certificate's signature chain up to the root CA before using it",
      error: "errors in the station logs",
    };
    const chosen = new Set(kinds);
    const list = h("div", { class: "events" });
    let paused = false;
    let source = null;
    function connect() {
      if (source) source.close();
      source = new EventSource(`/api/runs/${runId}/events/stream?kinds=${[...chosen].join(",")}`);
      source.onmessage = (msg) => {
        if (paused) return;
        const ev = JSON.parse(msg.data);
        list.prepend(h("div", {}, h("span", { class: "t" }, new Date(ev.t * 1000).toLocaleTimeString()),
          h("span", { class: "k", title: HINTS[ev.kind] || "" }, LABELS[ev.kind] || ev.kind), h("b", {}, ev.station), " ", ev.text));
        while (list.children.length > 500) list.lastChild.remove();
      };
      source.addEventListener("end", () => { source.close(); list.prepend(h("div", { class: "muted" }, "run ended")); });
    }
    pane.append(h("div", { class: "panel" },
      h("div", { class: "row" },
        kinds.map((k) => h("label", { class: "inline", title: HINTS[k] }, h("input", { type: "checkbox", checked: true, onchange: (e) => {
          if (e.target.checked) chosen.add(k); else chosen.delete(k);
          connect();
        } }), LABELS[k])),
        h("button", { type: "button", class: "secondary", onclick: (e) => { paused = !paused; e.target.textContent = paused ? "Resume" : "Pause"; } }, "Pause"),
        h("button", { type: "button", class: "secondary", onclick: () => clear(list) }, "Clear")),
      list));
    connect();
    stopTab = () => source && source.close();
    onLeave(() => source && source.close());
  }

  // ------------------------------------------------------------ control
  async function controlTab() {
    if (!running()) { pane.append(needsRunning()); return; }
    if (!canWrite) { pane.append(h("p", { class: "panel muted" }, "Only the run's owner can send control events.")); return; }
    const layout = await get(`/runs/${runId}/layout`);
    const stations = layout.stations.filter((s) => s.pseudonyms);
    if (!stations.length) { pane.append(h("p", { class: "panel muted" }, "No station of this run has pseudonyms.")); return; }
    const out = h("div", {});
    const handles = {};
    async function send(station, body) {
      const line = h("p", {}, badge("sent", "warn"), ` ${station}: ${body.action} …`);
      out.prepend(line);
      try {
        const r = await post(`/runs/${runId}/control`, { station, ...body });
        const a = r.answer;
        if (a && a.lock_handle) handles[station] = a.lock_handle;
        if (a && a.result === "unlocked") delete handles[station];
        clear(line).append(badge(r.ok ? "ok" : "refused", r.ok ? "ok" : "bad"), ` ${station}: ${body.action} → `,
          a ? `${a.result}${a.error ? " (" + a.error + ")" : ""}${a.lock_handle ? ` (handle ${a.lock_handle})` : ""}` +
              `${a.previous !== undefined && a.result === "changed" ? `, pool ${a.previous} → ${a.index}` : ""}` : "no answer within 3 s");
      } catch (e) { clear(line).append(badge("error", "bad"), ` ${station}: ${body.action}: ${e.message}`); }
    }
    pane.append(h("div", { class: "panel" },
      table(["Station", ""], stations.map((s) => [s.name, h("div", { class: "row" },
        h("button", { type: "button", onclick: () => send(s.name, { action: "change" }) }, "Change pseudonym"),
        h("button", { type: "button", class: "secondary", onclick: () => send(s.name, { action: "trigger" }) }, "ID change trigger"),
        h("button", { type: "button", class: "secondary", onclick: () => send(s.name, { action: "lock", duration: 30 }) }, "Lock 30 s"),
        h("button", { type: "button", class: "secondary", onclick: () => {
          if (!handles[s.name]) { notice("no lock taken from this page", "error"); return; }
          send(s.name, { action: "unlock", lock_handle: handles[s.name] });
        } }, "Unlock"))])),
      h("h2", {}, "Answers"), out));
  }

  // ------------------------------------------------------------ results
  async function resultsTab() {
    const box = h("div", {});
    pane.append(box);
    if (running() && canWrite) {
      const duration = h("input", { type: "number", min: 1, max: 60, value: 15 });
      const expect = h("textarea", { rows: 2, placeholder: "extra expectations, one per line, e.g. pki.running==1" });
      const result = h("div", {});
      box.append(h("div", { class: "panel" }, h("h2", {}, "Check"),
        h("div", { class: "row" }, h("label", {}, "Seconds", duration),
          h("button", { type: "button", onclick: async (e) => {
            e.target.disabled = true;
            clear(result).append(h("p", { class: "muted" }, "collecting..."));
            try {
              const r = await post(`/runs/${runId}/check`, { duration_s: Number(duration.value),
                expect: expect.value.split("\n").map((l) => l.trim()).filter(Boolean) });
              clear(result).append(h("p", {}, badge(r.verdict)),
                table(["Expectation", "Value", ""], r.expectations.map((x) => [h("code", {}, x.expect), x.detail || x.value || "", badge(x.ok ? "ok" : "fail", x.ok ? "ok" : "bad")])));
            } catch (err) { clear(result).append(h("p", { class: "errors" }, err.message)); }
            e.target.disabled = false;
          } }, "Run check")), expect, result));
    }
    if (running()) {
      try {
        const ev = await get(`/runs/${runId}/eavesdropper`);
        if (ev.score) box.append(scorePanel(ev.score));
      } catch (e) { /* no eavesdropper */ }
    }
    const checks = await get(`/runs/${runId}/checks`);
    if (checks.length) {
      box.append(h("div", { class: "panel" }, h("h2", {}, "Earlier checks"),
        table(["When", "Verdict", "Failed expectations"], checks.map((c) => [time(c.created_at), badge(c.result.verdict),
          c.result.expectations.filter((x) => !x.ok).map((x) => x.expect).join(", ")]))));
    }
    const files = await get(`/runs/${runId}/results`);
    if (files.length) {
      box.append(h("div", { class: "panel" }, h("h2", {}, "Collected results"),
        table(["File", "Size"], files.map((f) => [h("a", { href: `/api/runs/${runId}/results/${encodeURIComponent(f.name)}` }, f.name),
          `${(f.bytes / 1024).toFixed(1)} KB`]))));
      if (files.some((f) => f.name === "score.json")) {
        try {
          const r = await fetch(`/api/runs/${runId}/results/score.json`, { credentials: "same-origin" });
          if (r.ok) box.append(scorePanel(await r.json()));
        } catch (e) { /* optional */ }
      }
    } else if (!running()) {
      box.append(h("p", { class: "panel muted" }, "Results are collected when the run stops."));
    }
  }

  function scorePanel(score) {
    const hint = (text, title) => h("span", { class: "hint", title }, text);
    const techniques = Object.entries(score.by_technique || {});
    const lf = score.longest_followed;
    return h("div", { class: "panel" }, h("h2", {}, "Eavesdropper against ground truth"),
      h("p", {}, hint(`${score.links} link(s)`, "pseudonym changes the eavesdropper linked: it decided that an old and a new pseudonym are the same vehicle"),
        `: ${score.correct} correct, ${score.wrong} wrong`),
      lf ? h("p", {}, hint("Longest chain followed", "the most pseudonym changes through which the eavesdropper followed one vehicle without a mistake (consecutive identities of the same vehicle in one track); 0 means it never followed any vehicle through a change"),
        ": ", lf.changes ? `${lf.changes} pseudonym change(s), ${lf.station} (track ${lf.track})` : "none, no vehicle was followed through a pseudonym change") : null,
      techniques.length ? table([
        hint("Technique", "the evidence the eavesdropper used for a link: identifier (an unchanged identifier), position (the new pseudonym appears where the old one went silent) or timing (it appears right after the old one stopped)"),
        hint("Correct", "links where both pseudonyms really belong to the same vehicle (from the stations' own logs)"),
        hint("Wrong", "links that joined two different vehicles")],
        techniques.map(([k, v]) => [k, v.correct, v.wrong])) : null,
      score.tracks && score.tracks.length ? table([
        hint("Track", "one vehicle as the eavesdropper sees it: the pseudonyms it believes belong together"),
        hint("Identities", "pseudonyms in the track; 1 means nothing was linked to it"),
        hint("Purity", "share of the track's pseudonyms that belong to its most common vehicle. Read it with Identities: many identities at 100 % = a vehicle tracked through its changes (bad for privacy); low purity = different vehicles mixed up (the mix zone worked); 1 identity is always 100 % and means nothing"),
        hint("Followed", "pseudonym changes through which this track followed one vehicle without a mistake"),
        hint("Stations, in order", "the real vehicle behind each pseudonym of the track, in the order the eavesdropper added them")],
        score.tracks.map((t) => [t.track, t.identities, `${Math.round(t.purity * 100)} %`, t.followed_changes ?? "", t.stations.join(" → ")])) : null);
  }
}
