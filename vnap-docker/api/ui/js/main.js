// Entry point: session, navigation and routing (#/runs, #/new, #/run/<id>, #/account, #/admin).
import { ApiError, get, post, setCsrf } from "./api.js";
import { clear, h, leave, notice } from "./dom.js";
import { accountView } from "./views/account.js";
import { adminView } from "./views/admin.js";
import { newRunView } from "./views/newrun.js";
import { runView } from "./views/run.js";
import { runsView } from "./views/runs.js";

export const session = { user: null };
const app = document.getElementById("app");

function showNav() {
  const nav = document.getElementById("nav");
  nav.hidden = !session.user;
  document.getElementById("nav-admin").hidden = !(session.user && session.user.role === "admin");
  document.getElementById("who").textContent = session.user ? `${session.user.username} (${session.user.role})` : "";
}

function loginView() {
  const user = h("input", { name: "username", autocomplete: "username", required: true });
  const pass = h("input", { name: "password", type: "password", autocomplete: "current-password", required: true });
  const form = h("form", { class: "panel" },
    h("h1", {}, "Log in"),
    h("div", { class: "row" }, h("label", {}, "Username", user), h("label", {}, "Password", pass),
      h("button", { type: "submit" }, "Log in")));
  form.addEventListener("submit", async (ev) => {
    ev.preventDefault();
    try {
      const me = await post("/auth/login", { username: user.value, password: pass.value });
      setCsrf(me.csrf_token);
      session.user = me;
      showNav();
      notice("");
      location.hash = "#/runs";
      route();
    } catch (e) {
      notice(e.message, "error");
    }
  });
  return form;
}

async function route() {
  leave();
  clear(app);
  if (!session.user) {
    app.append(loginView());
    return;
  }
  const [, page, arg] = (location.hash || "#/runs").split("/");
  try {
    if (page === "new") await newRunView(app, session);
    else if (page === "run" && arg) await runView(app, session, arg);
    else if (page === "account") await accountView(app, session);
    else if (page === "admin" && session.user.role === "admin") await adminView(app, session);
    else await runsView(app, session);
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) {
      session.user = null;
      showNav();
      route();
      return;
    }
    notice(e.message, "error");
  }
}

async function start() {
  try {
    const me = await get("/auth/me");
    setCsrf(me.csrf_token);
    session.user = me;
  } catch (e) {
    session.user = null;
  }
  showNav();
  document.getElementById("logout").addEventListener("click", async () => {
    try { await post("/auth/logout"); } catch (e) { /* already logged out */ }
    session.user = null;
    setCsrf(null);
    showNav();
    location.hash = "#/runs";
    route();
  });
  window.addEventListener("hashchange", route);
  route();
}

start();
