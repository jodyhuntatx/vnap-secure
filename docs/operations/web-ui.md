# Web UI

The service serves a browser UI at `/ui/` (`/` redirects there). With it, a user can author,
run, observe and evaluate a scenario without the command line. It is plain JavaScript modules
(`service/ui/`), with no build step and no third-party code except Leaflet (vendored in
`service/ui/vendor/leaflet/`), and it uses only the [API](../reference/api.md).

## Pages

- **Runs:** every visible run with its state and time left; stop and delete.
- **New run:**
  1. *Start from the template*: pick a template (or the blank scenario). Its text becomes the
     new scenario. The list has the service's templates (the foundational scenarios `linkability`,
     `crossing`, `convoy` and `refill`), the saved templates and, for admins, the catalogue
     (also `trust` and the controls, as `controls/<name>`). Picking another one replaces the scenario, after a question if it was
     edited.
  2. Edit the scenario in either tab; both edit the same scenario:
     - *Scenario text*: TOML ([scenario format](../reference/scenario-format.md)).
     - *Builder*: RSUs and vehicles, pseudonyms and silent periods (for all vehicles, in
       `[defaults] pseudonyms`, and a vehicle's own value; an empty field shows in grey what
       applies), routes or random-turn
       crossings, mix zones, the run's PKI (certificates at start, refill) and the
       eavesdropper. Positions, routes, crossings and zones are set by clicking the map.
       Each vehicle has a colour, used for the vehicle and its route here and on the run's map.
       *Dynamic mix zone*: "all vehicles" with "min cars", radius and cooldown for the run,
       and a toggle with its own "min cars" per vehicle
       ([vehicle movement](../functional/vehicle-movement.md#dynamic-mix-zones)).
  3. Pick a variant of the scenario if it has any (its overrides and description are shown),
     add overrides, validate, start. The variant's overrides apply first, then the ones typed
     here ([variants](../reference/scenario-format.md#variants)). The scenario is checked
     against the user policy. Field errors are listed, and in the builder the station
     concerned is highlighted.

  - **Text and builder:** the builder reads the text when its tab opens, and keeps every key
    its forms do not show (e.g. `[check]`, `validity_hours`). It writes the text again only
    when something was changed in it; the text's comments are then lost, and defaults the
    forms show are written out (e.g. a station's position).
  - **Save as a template:** saves the scenario as it is, under a name (letters, digits, `-`,
    `_`); it must be valid. A saved template is its owner's; "shared with all users" lets
    every user start from it. Only the owner or an admin replaces or deletes it.
  - **Generic names:** when saving, renames the stations `rsu1`, `rsu2`, … (station type 15)
    and `vehicle1`, `vehicle2`, …, and the mix zones `zone1`, `zone2`, …, also where `[check]`
    expectations start with a station's name. Comments keep the old names.
- **Run:**
  - *Overview*: the run's name and description, scenario, owner, instance, time left, image
    and seed. Per station: state, pseudonym, refill and certificate checks. Also the run PKI's
    issue times, the eavesdropper's tracks and warnings. Stop and share are here.
    "Edit name and description" changes both, in any state of the run (owner or admin). They
    start as the scenario's; an empty name gives the scenario's name again.
  - *Map*: start positions, routes, crossings and mix zones, with live positions every 2 s.
    Pick a station, then click the map to move it.
  - *Events*: the last 10 minutes' events and then the live event stream, filtered by kind (pseudonym, ID change, refill,
    certificate check, error), with pause. Hover a kind for its meaning.
  - *Control*: ID change trigger, lock and unlock, with each station's answer.
  - *Results*: run a check (extra expectations allowed) and see earlier checks. After the stop,
    download the summary report (`report.docx`) and the other collected files, see the
    eavesdropper's score against ground truth, and the privacy and cost of every pseudonym
    change ([below](#privacy-and-cost-of-the-pseudonym-changes)); the headings explain each
    column on hover.
- **Account:**
  - change password;
  - turn the TOTP second factor on (QR code) or off, and make new recovery codes;
  - create and revoke API tokens (a new token is shown once).
- **Admin:**
  - users: create, change roles, disable, unlock, reset passwords, reset a user's TOTP;
  - backups: list them, and back up now;
  - test certificates: every certificate of the committed v2 and v3 sets with its validity
    (expired ones marked), and *Regenerate* with a lifetime per certificate kind. The fields
    start at the lifetimes of the last regeneration, *Defaults* restores the tools' defaults,
    and the page follows the job until it is done
    ([certificates](../certificates.md#validity-and-regeneration));
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
  - *Most Frequent*: the share that belongs to its most frequent vehicle, with that vehicle
    in parentheses, e.g. `75 % (obu1)`. Several names mean a tie.
  - *Followed*: changes followed without a mistake.
  - *Station predictions in actual order across identity changes*: the real vehicle behind each pseudonym
    the eavesdropper put into the track, in the order it added them, e.g.
    `obu1 → obu1 → obu2`. One name throughout means it followed that vehicle; a change of
    name is a wrong link.

  Read the share together with identities. 1 identity is always 100 % and means nothing was
  linked. Many identities at a high share means that vehicle was tracked. A low share means
  different vehicles were mixed up, so the mix zone worked.

Details are in [eavesdropper and scoring](../functional/eavesdropper-and-scoring.md#scoring-against-ground-truth).

## Privacy and cost of the pseudonym changes

After a run stops, the *Results* tab puts every pseudonym change next to what it cost: what
the eavesdropper made of the change, and what the other stations missed meanwhile. This is the
first iteration of the measures in `Specs/VNAP-Secure-Pseudonym-Change-Overhead-Design.docx`.
It uses only data every run keeps, so runs stopped before it existed show it too.

- **Reference receiver:** reception is measured at the eavesdropper. On the lossless link it
  hears every frame, like any other receiver. Runs without an eavesdropper show only the
  figures from the stations' logs.
- **Headline figures:**
  - *tracking success*: the share of changes the eavesdropper linked correctly (lower is
    more private);
  - changes linked to the wrong vehicle;
  - *changes synchronized*: the share with another vehicle changing within 2 s;
  - *longest reception gap* and its mean;
  - *time information was stale*: the share of time the latest CAM of a vehicle was older
    than 1 s;
  - *delivery ratio*: CAMs received against CAMs expected at the vehicle's usual rate;
  - the share of CAMs carrying the full certificate, and the mean CAM size.
- **Timeline:** one lane per vehicle, showing reception, silent periods (amber) and other gaps
  (red). Each change is a dot: green when not linked, red when followed, purple when linked to
  another vehicle. Hover for details.
- **Changes:** one row per change: time, vehicle, logged silent period, reception gap (last
  CAM under the old pseudonym to the first under the new one), CAMs missing in it, the
  eavesdropper's outcome with its technique, and the vehicles that changed at the same time.
  Click a heading to sort.
- **Vehicles:** per vehicle:
  - changes, and changes refused;
  - pseudonym lifetime;
  - usual CAM interval;
  - gaps between CAMs (95th percentile, longest);
  - stale share and delivery ratio;
  - full-certificate share and CAM size;
  - certificate refill.

Without a silent period, no gap should be longer than two CAM intervals, because the CAM timer
restarts at a random phase. With one, a gap is the silent period plus up to that much.
Position error, conflict exposure, verification failures, warnings and CPU load need data
that runs do not record yet.

The summary report has the same figures in its section 4, *Cost of the pseudonym changes*
([summary report](../functional/eavesdropper-and-scoring.md#summary-report)).

The figures come from `sim/vnapsim/overhead.py`, which writes `overhead.json` to the run's
results when it stops (`GET /api/runs/{id}/overhead`; for older runs on first request), and
from `./vnapctl overhead <results dir>`.

## Browser notes

- **Session cookie:** HTTPS-only with `cookie_secure = true`, as production must have it.
  Safari and other WebKit browsers (including DuckDuckGo) then drop it even on
  `http://localhost`; use HTTPS, Chrome or Firefox. The repository's `config.toml` sets
  `cookie_secure = false`, for testing only, so that these browsers work over plain HTTP. The
  UI says so when the cookie was dropped.
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
