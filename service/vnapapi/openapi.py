"""Export the API's OpenAPI description (the same document the service serves at
/api/openapi.json) to a file, or check that the file is current.

  python3 -m vnapapi.openapi [--output PATH]     (make openapi)
  python3 -m vnapapi.openapi --check [PATH]      (make openapi-check, part of make test)

The app is built against a temporary data directory, so the service's database, TOTP key and
backups are never touched."""

import argparse
import difflib
import json
import os
import sys
import tempfile

os.environ.setdefault("VNAP_API_NO_APP", "1")
from .app import create_app  # noqa: E402
from .config import API_DIR, load_config  # noqa: E402

DEFAULT = os.path.join(os.path.dirname(API_DIR), "docs", "reference", "openapi.json")


def spec_text():
    with tempfile.TemporaryDirectory() as tmp:
        cfg = load_config()
        cfg["server"]["data_dir"] = os.path.join(tmp, "data")
        cfg["auth"]["totp_key_file"] = os.path.join(tmp, "totp.key")
        cfg["backup"]["dir"] = os.path.join(tmp, "backups")
        app = create_app(cfg, start_workers=False)
        try:
            return json.dumps(app.openapi(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        finally:
            app.state.db.conn.close()


def main():
    ap = argparse.ArgumentParser(prog="vnapapi.openapi", description=__doc__.split("\n\n")[0])
    ap.add_argument("--output", default=DEFAULT, help=f"where to write it (default {os.path.relpath(DEFAULT)})")
    ap.add_argument("--check", nargs="?", const=DEFAULT, metavar="PATH", help="only check that PATH is current")
    args = ap.parse_args()
    text = spec_text()
    if args.check:
        try:
            with open(args.check, encoding="utf-8") as f:
                current = f.read()
        except FileNotFoundError:
            current = ""
        if current == text:
            print(f"{os.path.relpath(args.check)} is current ({len(json.loads(text)['paths'])} paths)")
            return 0
        sys.stderr.writelines(list(difflib.unified_diff(current.splitlines(True), text.splitlines(True),
                                                        os.path.relpath(args.check), "generated", n=1))[:40])
        print(f"{os.path.relpath(args.check)} is out of date: run make openapi", file=sys.stderr)
        return 1
    with open(args.output, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"wrote {os.path.relpath(args.output)} ({len(json.loads(text)['paths'])} paths)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
