// Admin: users (create, role, disable, unlock, reset password, reset TOTP), backups and the audit log.
import { get, patch, post } from "../api.js";
import { badge, clear, h, notice, table, time } from "../dom.js";

const ROLES = ["viewer", "user", "admin"];

export async function adminView(app, session) {
  const users = h("div", {});
  const audit = h("div", {});
  const backups = h("div", {});
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
    h("div", { class: "panel" }, h("h2", {}, "Audit log"), auditFilter(), audit));

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
}
