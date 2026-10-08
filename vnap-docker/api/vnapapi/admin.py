"""Account administration on the server (the API has no self-registration):

  python3 -m vnapapi.admin create-user <name> [--role admin|user|viewer]   (first admin, or any user)
  python3 -m vnapapi.admin reset-password <name>
  python3 -m vnapapi.admin unlock <name>
  python3 -m vnapapi.admin list-users

The password is read from the terminal, or from VNAP_NEW_PASSWORD (scripts), never from argv.
"""

import argparse
import getpass
import os
import sys

os.environ.setdefault("VNAP_API_NO_APP", "1")
from .config import load_config  # noqa: E402
from .db import Database  # noqa: E402
from .security import ROLES, Accounts  # noqa: E402


def read_password():
    password = os.environ.get("VNAP_NEW_PASSWORD")
    if password:
        return password
    first = getpass.getpass("password: ")
    if first != getpass.getpass("again: "):
        sys.exit("passwords differ")
    return first


def main():
    ap = argparse.ArgumentParser(prog="vnapapi.admin", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("create-user")
    p.add_argument("username")
    p.add_argument("--role", choices=ROLES, default="user")
    sub.add_parser("reset-password").add_argument("username")
    sub.add_parser("unlock").add_argument("username")
    sub.add_parser("list-users")
    args = ap.parse_args()

    cfg = load_config()
    db = Database(os.path.join(cfg["server"]["data_dir"], "vnapapi.db"))
    accounts = Accounts(db, cfg)
    try:
        if args.cmd == "create-user":
            accounts.create_user(args.username, read_password(), args.role, created_by="vnapapi.admin")
            db.audit("vnapapi.admin", "user.created", args.username, {"role": args.role})
        elif args.cmd == "reset-password":
            if not db.one("SELECT id FROM users WHERE username = ?", (args.username,)):
                sys.exit("no such user")
            accounts.set_password(args.username, read_password())
            db.audit("vnapapi.admin", "user.password_reset", args.username)
        elif args.cmd == "unlock":
            db.execute("UPDATE users SET failed_logins = 0, locked_until = 0 WHERE username = ?", (args.username,))
            db.audit("vnapapi.admin", "user.unlocked", args.username)
        else:
            for u in db.query("SELECT username, role, disabled FROM users ORDER BY username"):
                print(f"{u['username']:24} {u['role']:7} {'disabled' if u['disabled'] else ''}")
    except ValueError as e:
        sys.exit(str(e))


if __name__ == "__main__":
    main()
