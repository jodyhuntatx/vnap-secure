// Account: change password, API tokens.
import { del, get, post } from "../api.js";
import { badge, clear, h, notice, table, time } from "../dom.js";

export async function accountView(app, session) {
  app.append(h("h1", {}, `Account: ${session.user.username}`));

  const current = h("input", { type: "password", autocomplete: "current-password" });
  const next = h("input", { type: "password", autocomplete: "new-password" });
  const again = h("input", { type: "password", autocomplete: "new-password" });
  const pwForm = h("form", { class: "panel" }, h("h2", {}, "Change password"),
    h("div", { class: "row" }, h("label", {}, "Current", current), h("label", {}, "New", next), h("label", {}, "New, again", again),
      h("button", { type: "submit" }, "Change")),
    h("p", { class: "muted" }, "Changing the password ends all your sessions, including this one."));
  pwForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    if (next.value !== again.value) { notice("the new passwords differ", "error"); return; }
    try {
      await post("/auth/password", { current_password: current.value, new_password: next.value });
      notice("password changed; log in again");
      setTimeout(() => location.reload(), 1500);
    } catch (e) { notice(e.message, "error"); }
  });

  const list = h("div", {});
  const shown = h("div", {});
  const name = h("input", { placeholder: "e.g. ci-pipeline", maxlength: 64 });
  const tokForm = h("form", { class: "row" }, h("label", {}, "Name", name), h("button", { type: "submit" }, "Create token"));
  tokForm.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      const t = await post("/tokens", { name: name.value.trim() });
      name.value = "";
      clear(shown).append(h("div", { class: "panel" },
        h("p", {}, h("b", {}, "Copy this token now: it is not shown again.")),
        h("pre", {}, t.token), h("p", { class: "muted" }, t.note)));
      await refresh();
    } catch (e) { notice(e.message, "error"); }
  });

  async function refresh() {
    const tokens = await get("/tokens");
    clear(list).append(tokens.length ? table(["Name", "Created", "Last used", ""], tokens.map((t) => [
      t.name, time(t.created_at), time(t.last_used) || "never",
      t.revoked ? badge("revoked", "bad") : h("button", { type: "button", class: "secondary", onclick: async () => {
        if (!confirm(`Revoke token ${t.name}?`)) return;
        try { await del(`/tokens/${t.id}`); await refresh(); } catch (e) { notice(e.message, "error"); }
      } }, "Revoke")])) : h("p", { class: "muted" }, "No API tokens."));
  }

  app.append(pwForm, h("div", { class: "panel" }, h("h2", {}, "API tokens"),
    h("p", { class: "muted" }, "For scripts: send ", h("code", {}, "Authorization: Bearer <token>"), ". A token acts with your role."),
    tokForm, shown, list));
  await refresh();
}
