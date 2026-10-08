// Runs list: state, time left, stop and delete; refreshes every 5 s.
import { del, get, post } from "../api.js";
import { badge, clear, every, h, notice, remaining, table, time } from "../dom.js";

export async function runsView(app, session) {
  const body = h("div", {});
  app.append(h("div", { class: "row" }, h("h1", {}, "Runs"), h("a", { href: "#/new" }, h("button", { type: "button" }, "New run"))), body);

  async function refresh() {
    let runs;
    try {
      runs = await get("/runs");
    } catch (e) {
      notice(e.message, "error");
      return;
    }
    clear(body);
    if (!runs.length) {
      body.append(h("p", { class: "panel muted" }, "No runs yet. Start one from a template or your own scenario."));
      return;
    }
    const rows = runs.map((r) => {
      const actions = [];
      const mine = r.owner === session.user.username || session.user.role === "admin";
      if (mine && ["queued", "starting", "running"].includes(r.state)) {
        actions.push(h("button", { class: "danger", type: "button", onclick: async () => {
          try { await post(`/runs/${r.id}/stop`); notice(`stopping ${r.scenario}`); refresh(); } catch (e) { notice(e.message, "error"); }
        } }, "Stop"));
      }
      if (mine && ["stopped", "failed"].includes(r.state)) {
        actions.push(h("button", { class: "secondary", type: "button", onclick: async () => {
          if (!confirm(`Delete run ${r.id} and its results?`)) return;
          try { await del(`/runs/${r.id}`); refresh(); } catch (e) { notice(e.message, "error"); }
        } }, "Delete"));
      }
      return [
        h("a", { href: `#/run/${r.id}` }, r.scenario), badge(r.state), r.owner,
        r.instance ?? "", time(r.started_at || r.created_at),
        r.state === "running" ? remaining(r.deadline) : (r.stop_reason || r.error || ""),
        h("div", { class: "row" }, actions),
      ];
    });
    body.append(h("div", { class: "panel" }, table(["Scenario", "State", "Owner", "Instance", "Started", "Time left / reason", ""], rows)));
  }

  await refresh();
  every(5000, refresh);
}
