# Patches: build and container

## Debian snapshot mirror

- **Problem:** the image's build stages use Debian bullseye, which reached end of life on
  2026-08-31. `deb.debian.org` pool files then returned 404, so the image no longer built.
- **Change:** in both build stages, `Dockerfile` switches apt to the Debian snapshot sources
  that the base image ships commented out (`snapshot.debian.org`, without the validity check).
- **Upstream:** candidate (any build of release2-main hits it).

## `certify` and certificate mode

- **Problem:** the stock image does not ship Vanetza's `certify` tool, and its entrypoint
  cannot pass certificate options to socktap.
- **Change:**
  - `Dockerfile` builds `certify` (`-DBUILD_CERTIFY=ON`) and copies it into the image;
  - `entrypoint.sh` starts socktap with certificate, AA chain and trusted root from
    environment variables (`SECURITY=certs`, `AT_CERT`, `AT_KEY`, `AA_CERT`, `ROOT_CERT`,
    `VANETZA_SECURITY`).

  The later patch groups add their own variables (pseudonym pool, control channels, refill);
  all are listed in [container environment](../reference/container-environment.md).
- **Upstream:** candidate.

## Bridge interface selection

- **Problem:** a station on two networks (message and control) gets two interfaces, and Docker
  does not guarantee which one becomes `eth0`. The stock entrypoint bridged `eth0`, so V2X
  traffic could end up on the control network.
- **Change:** `entrypoint.sh` bridges the interface that carries `VANETZA_BRIDGE_IP` (the
  station's V2X address), falling back to `eth0`. `vnapctl` sets it.
- **Upstream:** candidate.

## Files

`Dockerfile`, `entrypoint.sh`.

## Testing

- `make image` builds.
- Every scenario starts its stations through this entrypoint.
- `entrypoint = "stock"` scenarios use an unpatched image with `sim/stock-entrypoint.sh`, for
  comparison.
