# API

The service's HTTP API. The OpenAPI 3.1 description is authoritative for request and response
bodies; this page gives the overview.

- **In the repository:** [`openapi.json`](openapi.json), for reading API changes in diffs or
  generating clients without a running service.
- **From the running service:** `/api/openapi.json`, with an interactive view at `/api/docs`.
- **Keeping the file current:** after changing the API, run `make openapi`. `make test` fails
  when the file no longer matches the code.

## Authentication

- **Browser sessions:** `POST /api/auth/login` with `username`, `password` and, when the
  account has TOTP, `totp_code`. A missing code gives 401 with `"totp_required": true`.
  - Over plain HTTP with secure cookies, login is refused with an explanation (except on
    localhost).
  - Login sets an HttpOnly session cookie and returns a CSRF token. State-changing requests
    must send it as `X-CSRF-Token`.
- **API tokens:** `Authorization: Bearer vnap_…`, created under Account or with
  `POST /api/tokens`. They act with the user's role, need no CSRF token and no second factor,
  and are revocable. TOTP settings cannot be changed with a token.
- **Roles:**
  - `viewer`: sees runs shared with it;
  - `user`: starts templates and own scenarios within the limits;
  - `admin`: everything, including the scenario catalogue, users, backups and the audit log.

## Endpoints

| Method and path | Who | Purpose |
|---|---|---|
| `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/me` | all | session |
| `POST /api/auth/password` | all | change own password (ends all sessions) |
| `GET /api/auth/totp` | all | own TOTP state, recovery codes left |
| `POST /api/auth/totp/setup`, `/enable`, `/disable`, `/recovery-codes` | session | TOTP second factor ([service deployment](../operations/service-deployment.md#accounts-and-authentication)) |
| `GET/POST /api/tokens`, `DELETE /api/tokens/{id}` | all | own API tokens |
| `GET/POST /api/users`, `PATCH /api/users/{name}`, `POST /api/users/{name}/password` | admin | accounts: create, role, disable, unlock, reset TOTP, reset password |
| `GET /api/scenarios`, `GET /api/scenarios/{kind}/{name}` | all | templates (and, for admins, the catalogue) and their text |
| `GET /api/schema` | all | JSON Schema of the scenario format |
| `POST /api/scenarios/validate` | all | field-level validation against the user policy |
| `GET/POST /api/runs`, `GET/DELETE /api/runs/{id}` | all | list, start (template, catalogue or scenario text, overrides, duration), details with live status, delete |
| `POST /api/runs/{id}/stop`, `POST /api/runs/{id}/share` | owner | stop; share read-only with another user |
| `GET /api/runs/{id}/layout` | readers | description, stations, routes, crossings, mix zones (for the map) |
| `GET /api/runs/{id}/events`, `GET /api/runs/{id}/events/stream` | readers | events from the logs (optionally live MQTT for up to 30 s); server-sent events |
| `GET /api/runs/{id}/positions` | readers | current position of every moving station |
| `GET /api/runs/{id}/eavesdropper` | readers | the eavesdropper's report and the score against ground truth |
| `POST /api/runs/{id}/control` | owner | pseudonym change, trigger, lock, unlock; returns the station's answer |
| `POST /api/runs/{id}/stations/{station}/position` | owner | move a station |
| `POST /api/runs/{id}/check`, `GET /api/runs/{id}/checks` | owner / readers | run a check (verdict, metrics); earlier checks |
| `GET /api/runs/{id}/results`, `GET /api/runs/{id}/results/{name}` | readers | files collected at stop |
| `GET/POST /api/backups` | admin | list archives; back up now |
| `GET /api/audit` | admin | audit log (`limit`, `username`) |
| `GET /api/ui-config` | public | map tiles and origin for the web UI |
| `GET /api/healthz` | public | liveness |

"Readers" are the owner, the users a run is shared with, and admins. Others get 404 for
runs they cannot see.

## Errors

- **Validation errors:** 400 with `errors`, a list of `{path, message, text}`.
- **Refused by limits:** 429.
- **Control channel unreachable:** 502.
- **Other errors:** a JSON body with `detail` (or `error`).

## Example

```bash
T=vnap_…                                            # API token
curl -s -H "Authorization: Bearer $T" https://vnap.example.org/api/scenarios
RUN=$(curl -s -H "Authorization: Bearer $T" -H 'Content-Type: application/json' \
  -d '{"template": "pki-refill", "duration_minutes": 10}' https://vnap.example.org/api/runs | jq -r .id)
curl -s -H "Authorization: Bearer $T" -H 'Content-Type: application/json' -d '{"duration_s": 15}' \
  https://vnap.example.org/api/runs/$RUN/check | jq .verdict
curl -s -X POST -H "Authorization: Bearer $T" https://vnap.example.org/api/runs/$RUN/stop
```
