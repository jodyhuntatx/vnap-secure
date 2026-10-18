// Admin: users (create, role, disable, unlock, reset password, reset TOTP), backups, the test certificate
// sets (validity, regeneration with chosen lifetimes) and the audit log.
import { get, patch, post } from "../api.js";
import { badge, clear, h, notice, table, time } from "../dom.js";

const ROLES = ["viewer", "user", "admin"];

export async function adminView(app, session) {
  const users = h("div", {});
  const audit = h("div", {});
  const backups = h("div", {});
  const certs = h("div", {}, h("p", { class: "muted" }, "reading the certificates ..."));
  app.append(h("h1", {}, "Administration"), h("div", { class: "panel" }, h("h2", {}, "Users"), newUserForm(), users),
    h("div", { class: "panel" }, h("h2", {}, "Backups"),
      h("p", { class: "muted" }, "Database, collected results and user scenarios, checked against a manifest; the newest are kept ",
        "([backup] in config.toml). Restore and copies off the VM are done on the server: ", h("code", {}, "api/backup.sh"), "."),
      h("div", { class: "row" }, h("button", { type: "button", onclick: async (e) => {
        e.target.disabled = true;
        try {
          const b = await post("/backups");
          notice(`backup ${b.name}: ${b.files} file(s), ${(b.bytes / 1e6).toFixed(1)} MB`);
          await Promise.all([refreshBackups(), refreshAudit()]);
        } catch (err) { notice(err.message, "error"); }
        e.target.disabled = false;
      } }, "Back up now")), backups),
    h("div", { class: "panel" }, h("h2", {}, "Test certificates"),
      h("p", { class: "muted" }, "The committed certificate sets in ", h("code", {}, "certs/"), " used by scenarios without a run PKI (",
        h("code", {}, "trust"), " and its variants). Regenerating replaces the set's files, which are tracked in git: commit them. ",
        "Running simulations keep the certificates they loaded; new runs use the new ones."), certs),
    h("div", { class: "panel" }, h("h2", {}, "Audit log"), auditFilter(), audit));

  async function refreshCerts() {
    let sets;
    try { sets = await get("/admin/certs"); } catch (e) { clear(certs).append(h("p", { class: "errors" }, e.message)); return; }
    clear(certs).append(...sets.map(certSet));
    if (sets.some((s) => s.job && s.job.state === "running")) setTimeout(() => refreshCerts().then(refreshAudit), 3000);
  }

  function certSet(s) {
    const job = s.job;
    const state = s.error ? badge("unreadable", "bad") : s.expired ? badge("expired", "bad")
      : s.expires ? badge(`valid until ${s.expires.slice(0, 10)}`, "ok") : badge("empty", "warn");
    // the butterfly tickets share one validity: one row for all of them
    const rows = [];
    const bke = (s.certs || []).filter((c) => /^bke_at_\d+\.cert$/.test(c.file));
    for (const c of (s.certs || []).filter((c) => !bke.includes(c))) rows.push(certRow(c.file, c));
    if (bke.length) rows.push(certRow(`bke_at_0 … ${bke.length - 1} (${bke.length})`, bke[0], bke.some((c) => c.expired)));
    const inputs = {};
    const fields = s.lifetimes.map((l) => {
      inputs[l.key] = h("input", { type: "number", min: l.min, max: l.max, step: 1, value: l.default, required: true });
      return h("label", { title: `${l.min} to ${l.max} ${l.unit}; default ${l.default}` }, `${l.entity} (${l.unit})`, inputs[l.key]);
    });
    if (s.generated) for (const [k, v] of Object.entries(s.generated.lifetimes || {})) if (inputs[k]) inputs[k].value = v;
    const go = h("button", { type: "submit", disabled: !!(job && job.state === "running") }, `Regenerate ${s.set}`);
    const form = h("form", { class: "row" }, fields,
      h("button", { type: "button", class: "secondary", onclick: () => s.lifetimes.forEach((l) => { inputs[l.key].value = l.default; }) }, "Defaults"), go);
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      const lifetimes = Object.fromEntries(s.lifetimes.map((l) => [l.key, Number(inputs[l.key].value)]));
      if (!confirm(`Replace every certificate and key in ${s.dir} with a new ${s.set} set?`)) return;
      go.disabled = true;
      try { await post(`/admin/certs/${s.set}/regenerate`, { lifetimes }); notice(`regenerating the ${s.set} set ...`); }
      catch (e) { notice(e.message, "error"); }
      await refreshCerts();
    });
    return h("div", { class: "panel" },
      h("h3", {}, `${s.set} · `, h("code", {}, s.dir || ""), " ", state),
      h("p", { class: "muted" }, `${s.security || ""}, ${s.format || ""}, made with ${s.tool || ""}.`,
        s.generated ? ` Generated ${s.generated.generated_at} by ${s.generated.by || "?"}.` : " Not generated here (no generated.json)."),
      s.error ? h("p", { class: "errors" }, s.error) : null,
      s.missing && s.missing.length ? h("p", { class: "errors" }, `missing: ${s.missing.join(", ")}`) : null,
      rows.length ? table(["Certificate", "Role", "Valid from", "Valid until", ""], rows) : null,
      job ? h("p", { class: job.state === "failed" ? "errors" : "muted" },
        `last regeneration: ${job.state} (by ${job.by}, ${time(job.started_at)})` + (job.error ? `: ${job.error}` : "")
        + (job.expires ? `; valid until ${job.expires}` : "")) : null,
      h("p", { class: "muted" }, `Lifetimes: ${s.basis}`), form);
  }

  function certRow(name, c, expired) {
    if (c.error) return [h("code", {}, name), "", "", "", badge("unreadable", "bad")];
    return [h("code", {}, name), c.role || "", c.valid_from, c.valid_until,
      (expired ?? c.expired) ? badge("expired", "bad") : badge("valid", "ok")];
  }

  async function refreshBackups() {
    const list = await get("/backups");
    clear(backups).append(list.length ? table(["Archive", "Size", "Created"], list.map((b) => [
      h("code", {}, b.name), `${(b.bytes / 1e6).toFixed(1)} MB`, time(b.created_at)]))
      : h("p", { class: "muted" }, "No backups yet."));
  }

  function newUserForm() {
    const name = h("input", { maxlength: 64, autocomplete: "off" });
    const pass = h("input", { type: "password", autocomplete: "new-password" });
    const role = h("select", {}, ROLES.map((r) => h("option", { value: r, selected: r === "user" }, r)));
    const form = h("form", { class: "row" }, h("label", {}, "Username", name), h("label", {}, "Initial password", pass),
      h("label", {}, "Role", role), h("button", { type: "submit" }, "Create user"));
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      try {
        await post("/users", { username: name.value.trim(), password: pass.value, role: role.value });
        notice(`user ${name.value} created`);
        name.value = pass.value = "";
        await refreshUsers();
      } catch (e) { notice(e.message, "error"); }
    });
    return form;
  }

  async function change(username, body) {
    try { await patch(`/users/${encodeURIComponent(username)}`, body); await refreshUsers(); } catch (e) { notice(e.message, "error"); }
  }

  async function refreshUsers() {
    const list = await get("/users");
    clear(users).append(table(["User", "Role", "State", "TOTP", "Created", ""], list.map((u) => {
      const self = u.username === session.user.username;
      const role = h("select", { disabled: self, onchange: (e) => change(u.username, { role: e.target.value }) },
        ROLES.map((r) => h("option", { value: r, selected: r === u.role }, r)));
      const actions = [
        self ? null : h("button", { type: "button", class: "secondary", onclick: () => change(u.username, { disabled: !u.disabled }) },
          u.disabled ? "Enable" : "Disable"),
        u.locked ? h("button", { type: "button", class: "secondary", onclick: () => change(u.username, { unlock: true }) }, "Unlock") : null,
        u.totp ? h("button", { type: "button", class: "secondary", onclick: () => {
          if (confirm(`Turn off the second factor of ${u.username} (e.g. a lost phone)? Their sessions end; they log in with the password and can set it up again.`)) {
            change(u.username, { reset_totp: true });
          }
        } }, "Reset TOTP") : null,
        h("button", { type: "button", class: "secondary", onclick: async () => {
          const pw = prompt(`New password for ${u.username}:`);
          if (!pw) return;
          try { await post(`/users/${encodeURIComponent(u.username)}/password`, { password: pw }); notice("password reset"); } catch (e) { notice(e.message, "error"); }
        } }, "Reset password"),
      ];
      return [u.username, role,
        u.disabled ? badge("disabled", "bad") : u.locked ? badge("locked", "warn") : badge("active", "ok"),
        u.totp ? badge("on", "ok") : badge("off"),
        `${time(u.created_at)}${u.created_by ? " by " + u.created_by : ""}`, h("div", { class: "row" }, actions)];
    })));
  }

  let who = "";
  function auditFilter() {
    const input = h("input", { placeholder: "username (empty: all)" });
    const form = h("form", { class: "row" }, h("label", {}, "Filter", input), h("button", { type: "submit", class: "secondary" }, "Show"));
    form.addEventListener("submit", (ev) => { ev.preventDefault(); who = input.value.trim(); refreshAudit().catch((e) => notice(e.message, "error")); });
    return form;
  }

  async function refreshAudit() {
    const rows = await get(`/audit?limit=300${who ? "&username=" + encodeURIComponent(who) : ""}`);
    clear(audit).append(table(["When", "User", "Action", "Target", "Detail", "From"], rows.map((r) => [
      time(r.ts), r.username || "", r.action, r.target || "",
      r.detail ? h("code", {}, JSON.stringify(r.detail)) : "", r.address || ""])));
  }

  await Promise.all([refreshUsers(), refreshBackups(), refreshAudit()]);
  refreshCerts();   // runs docker: a few seconds, so the rest of the page does not wait for it
}
