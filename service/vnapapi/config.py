"""Service configuration (config.toml next to the package, or VNAP_API_CONFIG)."""

import os
import tomllib

API_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
# the simulation package (vnapsim) lives in the repository's sim/ (VNAP_SIM_DIR to override)
SIM_DIR = os.environ.get("VNAP_SIM_DIR") or os.path.join(os.path.dirname(API_DIR), "sim")

DEFAULTS = {
    "server": {"data_dir": "data", "cookie_secure": True, "session_hours": 12},
    "auth": {"max_failed_logins": 5, "lockout_minutes": 15, "login_attempts_per_minute": 10,
             "totp_issuer": "vnap-secure", "totp_key_file": "secrets/totp.key"},
    "backup": {"dir": "backups", "keep": 14},
    "limits": {"user": {"concurrent_runs": 2, "max_duration_minutes": 60, "results_mb": 500},
               "admin": {"concurrent_runs": 10, "max_duration_minutes": 480, "results_mb": 5000}},
    "containers": {"station": {"cpus": 1.0, "memory": "512m"}, "pki": {"cpus": 1.0, "memory": "512m"},
                   "default": {"cpus": 0.5, "memory": "256m"}},
    "worker": {"threads": 2},
    "ui": {"tile_url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
           "tile_attribution": "&copy; OpenStreetMap contributors"},
}


def load_config(path=None):
    path = path or os.environ.get("VNAP_API_CONFIG") or os.path.join(API_DIR, "config.toml")
    cfg = {k: dict(v) for k, v in DEFAULTS.items()}
    if os.path.isfile(path):
        with open(path, "rb") as f:
            for section, values in tomllib.load(f).items():
                cfg.setdefault(section, {}).update(values)
    for section, key in (("server", "data_dir"), ("auth", "totp_key_file"), ("backup", "dir")):
        value = cfg[section][key]
        cfg[section][key] = value if os.path.isabs(value) else os.path.join(API_DIR, value)
    return cfg
