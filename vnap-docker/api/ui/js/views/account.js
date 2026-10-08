// Account: change password, TOTP second factor, API tokens.
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

  // ------------------------------------------------------------ TOTP
  const totpBox = h("div", {});
  const pw = () => h("input", { type: "password", autocomplete: "current-password" });
  const codeInput = () => h("input", { inputmode: "numeric", autocomplete: "one-time-code", maxlength: 16 });

  function showRecoveryCodes(codes) {
    return h("div", { class: "panel" },
      h("p", {}, h("b", {}, "Recovery codes: copy them now, they are not shown again.")),
      h("p", { class: "muted" }, "Each one logs in once in place of a code from the app, e.g. when the phone is lost."),
      h("pre", { class: "codes" }, codes.join("\n")));
  }

  async function renderTotp(extra) {
    const st = await get("/auth/totp");
    clear(totpBox);
    if (!st.enabled) {
      const p = pw();
      const form = h("form", { class: "row" }, h("label", {}, "Password", p), h("button", { type: "submit" }, "Set up"));
      form.addEventListener("submit", async (ev) => {
        ev.preventDefault();
        try { setupStep(await post("/auth/totp/setup", { password: p.value })); } catch (e) { notice(e.message, "error"); }
      });
      totpBox.append(h("p", {}, badge("off"), " Logins need only the password."),
        h("p", { class: "muted" }, "With a second factor, a login also needs a 6-digit code from an authenticator app " +
          "(e.g. Google Authenticator, Microsoft Authenticator, 1Password, Aegis). API tokens are not affected."), form);
    } else {
      const p = pw(), c = codeInput();
      const send = (path, done) => async () => {
        try { done(await post(path, { password: p.value, code: c.value.trim() })); } catch (e) { notice(e.message, "error"); }
      };
      totpBox.append(h("p", {}, badge("on", "ok"), ` Logins need a code from the app. ${st.recovery_codes_left} unused recovery code(s).`),
        h("div", { class: "row" }, h("label", {}, "Password", p), h("label", {}, "Code (or recovery code)", c),
          h("button", { type: "button", class: "secondary", onclick: send("/auth/totp/recovery-codes",
            (r) => renderTotp(showRecoveryCodes(r.recovery_codes))) }, "New recovery codes"),
          h("button", { type: "button", class: "danger", onclick: send("/auth/totp/disable",
            () => { notice("second factor turned off"); renderTotp(); }) }, "Turn off")));
    }
    if (extra) totpBox.append(extra);
  }

  function setupStep(setup) {
    clear(totpBox);
    const c = codeInput();
    const form = h("form", { class: "row" }, h("label", {}, "Code shown by the app", c), h("button", { type: "submit" }, "Turn on"));
    form.addEventListener("submit", async (ev) => {
      ev.preventDefault();
      try {
        const r = await post("/auth/totp/enable", { code: c.value.trim() });
        notice("second factor turned on");
        await renderTotp(showRecoveryCodes(r.recovery_codes));
      } catch (e) { notice(e.message, "error"); }
    });
    totpBox.append(
      h("p", {}, "1. Scan this code with the authenticator app, or enter the key by hand:"),
      setup.qr_svg ? h("img", { class: "qr", alt: "QR code of the TOTP setup", src: "data:image/svg+xml;base64," + btoa(setup.qr_svg) }) : null,
      h("p", {}, "Key: ", h("code", {}, setup.secret.replace(/(.{4})/g, "$1 ").trim())),
      h("p", {}, "2. Enter the 6-digit code the app shows to confirm:"), form,
      h("button", { type: "button", class: "secondary", onclick: () => renderTotp() }, "Cancel"));
    c.focus();
  }

  app.append(pwForm, h("div", { class: "panel" }, h("h2", {}, "Second factor (TOTP)"), totpBox),
    h("div", { class: "panel" }, h("h2", {}, "API tokens"),
    h("p", { class: "muted" }, "For scripts: send ", h("code", {}, "Authorization: Bearer <token>"), ". A token acts with your role."),
    tokForm, shown, list));
  await Promise.all([refresh(), renderTotp()]);
}
