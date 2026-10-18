// Unit tests of the builder's model and TOML writer (no browser): node --test
import assert from "node:assert/strict";
import { test } from "node:test";
import { fromScenario, toToml } from "../js/views/builder.js";

const CENTER = [40.2, -8.4];
const scenario = (stations, defaults = {}) => ({ description: "d", defaults: { security: "certs-v3", ...defaults }, pki: {},
  stations, control: { client: { mode: "manual" } } });
const car = (name, pseudonyms = {}) => ({ name, pseudonyms, mobility: { crossing: CENTER } });

test("shared silent periods stay in [defaults] and are not copied into the vehicles", () => {
  const m = fromScenario(scenario([car("a"), car("b", { silent_max_ms: 9000 })],
    { pseudonyms: { min_interval_ms: 1000, silent_min_ms: 6000, silent_max_ms: 12000 } }), CENTER);
  assert.deepEqual([m.pseudo.silent_min_ms, m.pseudo.silent_max_ms], [6000, 12000]);
  assert.deepEqual([m.stations[0].silent_min_ms, m.stations[0].silent_max_ms], ["", ""]);
  const text = toToml(m);
  assert.match(text, /\[defaults\]\nsecurity = "certs-v3"\npseudonyms = \{ min_interval_ms = 1000, silent_min_ms = 6000, silent_max_ms = 12000 \}/);
  assert.match(text, /name = "a"\n(?:.*\n)*?pseudonyms = \{\}\n/);              // inherits: no keys of its own
  assert.match(text, /name = "b"\n(?:.*\n)*?pseudonyms = \{ silent_max_ms = 9000 \}\n/);   // its own value only
  assert.doesNotMatch(text, /silent_min_ms = 0/);
});

test("a scenario without silent periods gets none written", () => {
  const text = toToml(fromScenario(scenario([car("a")]), CENTER));
  assert.doesNotMatch(text, /silent_/);
  assert.doesNotMatch(text, /\[defaults\]\nsecurity = "certs-v3"\npseudonyms/);   // nothing shared to write
});

test("own values that were written survive a round trip", () => {
  const m = fromScenario(scenario([car("a", { silent_min_ms: 0, silent_max_ms: 0 })], { pseudonyms: { silent_min_ms: 3000 } }), CENTER);
  assert.deepEqual([m.stations[0].silent_min_ms, m.stations[0].silent_max_ms], [0, 0]);   // explicit 0: no silence for this one
  assert.match(toToml(m), /pseudonyms = \{ silent_min_ms = 0, silent_max_ms = 0 \}/);
});
