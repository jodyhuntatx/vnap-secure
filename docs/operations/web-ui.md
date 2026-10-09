# Web UI

The service serves a browser UI at `/ui/` (`/` redirects there). With it, a user can author,
run, observe and evaluate a scenario without the command line. It is plain JavaScript modules
(`service/ui/`), with no build step and no third-party code except Leaflet (vendored in
`service/ui/vendor/leaflet/`), and it uses only the [API](../reference/api.md).

## Pages

- **Runs:** every visible run with its state and time left; stop and delete.
- **New run:**
  - *From a template*: pick a template, see its text, add overrides.
  - *Scenario text*: edit TOML directly ([scenario format](../reference/scenario-format.md)).
  - *Builder*: RSUs and vehicles, pseudonyms and silent periods, routes or random-turn
    crossings, mix zones, the run's PKI (certificates at start, refill) and the eavesdropper.
    Positions, routes, crossings and zones are set by clicking the map. *Edit as text* turns
    the result into TOML.

  All three validate against the user policy before starting. Field errors are listed, and
  in the builder the station concerned is highlighted.
- **Run:**
  - *Overview*: the scenario's description, owner, instance, time left, image and seed. Per
    station: state, pseudonym, refill and certificate checks. Also the run PKI's issue times,
    the eavesdropper's tracks and warnings. Stop and share are here.
  - *Map*: start positions, routes, crossings and mix zones, with live positions every 2 s.
    Pick a station, then click the map to move it.
  - *Events*: the live event stream, filtered by kind (pseudonym, ID change, refill,
    certificate check, error), with pause. Hover a kind for its meaning.
  - *Control*: change pseudonym, ID change trigger, lock and unlock, with each station's answer.
  - *Results*: run a check (extra expectations allowed) and see earlier checks. After the stop,
    download the collected files and see the eavesdropper's score against ground truth; the
    headings explain each column on hover.
- **Account:**
  - change password;
  - turn the TOTP second factor on (QR code) or off, and make new recovery codes;
  - create and revoke API tokens (a new token is shown once).
- **Admin:**
  - users: create, change roles, disable, unlock, reset passwords, reset a user's TOTP;
  - backups: list them, and back up now;
  - the audit log, filtered by user.

## Reading the eavesdropper score

The *Results* tab compares what the eavesdropper concluded with what really happened (the
stations' own logs):

- **Links:** pseudonym changes the eavesdropper linked; correct when both pseudonyms belong to
  the same vehicle.
- **Longest chain followed:** the most pseudonym changes through which it followed one vehicle
  without a mistake. This is the privacy result in one number; 0 means it never followed a
  vehicle through a change.
- **Per track:**
  - *Identities*: pseudonyms in the track.
  - *Purity*: the share that belongs to its most common vehicle.
  - *Followed*: changes followed without a mistake.

  Read purity together with identities. 1 identity is always 100 % and means nothing was
  linked. Many identities at high purity means a vehicle was tracked. Low purity means
  different vehicles were mixed up, so the mix zone worked.

Details are in [eavesdropper and scoring](../functional/eavesdropper-and-scoring.md#scoring-against-ground-truth).

## Browser notes

- **Session cookie:** HTTPS-only by default. Safari and other WebKit browsers (including
  DuckDuckGo) drop it even on `http://localhost`; use HTTPS, Chrome or Firefox, or set
  `cookie_secure = false` for a test. The UI says so when the cookie was dropped.
- **Map tiles** come from `[ui] tile_url` (OpenStreetMap by default) and are requested with an
  origin-only Referer, as the OpenStreetMap tile policy requires. Without internet access, set
  an internal tile server, or `""` for no background map.

## Security

- **Content-Security-Policy:** strict. Scripts and styles come only from the service, with no
  inline code; images may also come from the tile server.
- **Framing and referrers:** `frame-ancestors 'none'`; no referrer, except the origin for map
  tiles.
- **Text** is only ever inserted as text, never as HTML.
- **Sessions:** the session cookie and CSRF token are the API's.
