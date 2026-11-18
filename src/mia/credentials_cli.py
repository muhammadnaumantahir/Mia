"""Manage local provider credentials without placing secrets in files or shell history."""

from __future__ import annotations

import argparse
import getpass

from mia.gateway.credentials import CredentialStore, CredentialStoreUnavailable


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Save or remove Mia provider keys in the operating-system credential store."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    set_parser = subparsers.add_parser("set", help="Save a provider key securely")
    set_parser.add_argument("provider", help="Provider ID, e.g. freellmapi")

    delete_parser = subparsers.add_parser("delete", help="Remove a saved provider key")
    delete_parser.add_argument("provider", help="Provider ID")

    status_parser = subparsers.add_parser("status", help="Check whether a provider key exists")
    status_parser.add_argument("provider", help="Provider ID")

    args = parser.parse_args()

    try:
        store = CredentialStore()
        if args.command == "set":
            secret = getpass.getpass(f"API key for {args.provider}: ").strip()
            if not secret:
                parser.error("API key cannot be empty")
            store.set(args.provider, secret)
            print(f"Saved credential for {args.provider} in the OS credential store.")
        elif args.command == "delete":
            store.delete(args.provider)
            print(f"Removed credential for {args.provider}.")
        elif args.command == "status":
            present = store.get(args.provider) is not None
            print(f"{args.provider}: {'credential saved' if present else 'no credential saved'}")
    except CredentialStoreUnavailable as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
