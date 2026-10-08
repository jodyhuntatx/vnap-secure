"""Service configuration (config.toml next to the package, or VNAP_API_CONFIG)."""

import os
import tomllib

API_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
VNAP_DOCKER = os.path.dirname(API_DIR)   # vnapsim lives here

DEFAULTS = {
    "server": {"data_dir": "data", "cookie_secure": True, "session_hours": 12},
    "auth": {"max_failed_logins": 5, "lockout_minutes": 15, "login_attempts_per_minute": 10},
    "limits": {"user": {"concurrent_runs": 2, "max_duration_minutes": 60, "results_mb": 500},
               "admin": {"concurrent_runs": 10, "max_duration_minutes": 480, "results_mb": 5000}},
    "containers": {"station": {"cpus": 1.0, "memory": "512m"}, "pki": {"cpus": 1.0, "memory": "512m"},
                   "default": {"cpus": 0.5, "memory": "256m"}},
    "worker": {"threads": 2},
}


def load_config(path=None):
    path = path or os.environ.get("VNAP_API_CONFIG") or os.path.join(API_DIR, "config.toml")
    cfg = {k: dict(v) for k, v in DEFAULTS.items()}
    if os.path.isfile(path):
        with open(path, "rb") as f:
            for section, values in tomllib.load(f).items():
                cfg.setdefault(section, {}).update(values)
    data_dir = cfg["server"]["data_dir"]
    cfg["server"]["data_dir"] = data_dir if os.path.isabs(data_dir) else os.path.join(API_DIR, data_dir)
    return cfg
