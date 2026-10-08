// Admin: users (create, role, disable, unlock, reset password) and the audit log.
import { get, patch, post } from "../api.js";
import { badge, clear, h, notice, table, time } from "../dom.js";

const ROLES = ["viewer", "user", "admin"];

export async function adminView(app, session) {
  const users = h("div", {});
  const audit = h("div", {});
  app.append(h("h1", {}, "Administration"), h("div", { class: "panel" }, h("h2", {}, "Users"), newUserForm(), users),
    h("div", { class: "panel" }, h("h2", {}, "Audit log"), auditFilter(), audit));

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
    clear(users).append(table(["User", "Role", "State", "Created", ""], list.map((u) => {
      const self = u.username === session.user.username;
      const role = h("select", { disabled: self, onchange: (e) => change(u.username, { role: e.target.value }) },
        ROLES.map((r) => h("option", { value: r, selected: r === u.role }, r)));
      const actions = [
        self ? null : h("button", { type: "button", class: "secondary", onclick: () => change(u.username, { disabled: !u.disabled }) },
          u.disabled ? "Enable" : "Disable"),
        u.locked ? h("button", { type: "button", class: "secondary", onclick: () => change(u.username, { unlock: true }) }, "Unlock") : null,
        h("button", { type: "button", class: "secondary", onclick: async () => {
          const pw = prompt(`New password for ${u.username}:`);
          if (!pw) return;
          try { await post(`/users/${encodeURIComponent(u.username)}/password`, { password: pw }); notice("password reset"); } catch (e) { notice(e.message, "error"); }
        } }, "Reset password"),
      ];
      return [u.username, role,
        u.disabled ? badge("disabled", "bad") : u.locked ? badge("locked", "warn") : badge("active", "ok"),
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

  await Promise.all([refreshUsers(), refreshAudit()]);
}
