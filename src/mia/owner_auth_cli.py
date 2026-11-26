"""Local owner-authentication bootstrap CLI (MIA-COMP-04-OWNER)."""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path


def _service():
    from mia.security.owner_auth import OwnerAuthService

    data_dir = Path(os.getenv("MIA_DATA_DIR", "./data"))
    return OwnerAuthService(data_dir / "mia-owner-auth.sqlite3")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="mia-owner-auth",
        description="Bootstrap and inspect Mia's local WebAuthn owner passkey setup.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("bootstrap", help="Create the one-time first-passkey enrollment code")
    commands.add_parser("status", help="Show whether an owner passkey is registered")
    args = parser.parse_args()
    service = _service()
    try:
        if args.command == "bootstrap":
            result = service.create_bootstrap_code()
            print("Mia owner passkey bootstrap code (shown once):")
            print(result["code"])
            print(f"Expires at: {result['expires_at']}")
            print("Open http://localhost:PORT/owner-auth and enter this code.")
            print("Do not share it. It can be used once and expires in five minutes.")
            return 0
        if args.command == "status":
            print(json.dumps({
                "configured": service.is_configured,
                "credential_count": service.credential_count(),
                "database": str(service.path),
            }, indent=2))
            return 0
        return 2
    except Exception as exc:
        from mia.security.owner_auth import OwnerAuthError

        if isinstance(exc, (OwnerAuthError, OSError, RuntimeError)):
            print(f"mia-owner-auth: {exc}", file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    raise SystemExit(main())
