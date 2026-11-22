"""Create, verify, preview and restore encrypted Mia backups (MIA-LIFE-11).

The passphrase is read from MIA_BACKUP_PASSPHRASE or a hidden prompt, never from the command
line, so it stays out of shell history and process listings. It is never printed or stored.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

from mia.life.backup import (
    POLICIES,
    BackupError,
    create_backup,
    preview_restore,
    restore_backup,
    verify_backup,
)


def _passphrase(confirm: bool) -> str:
    value = os.environ.get("MIA_BACKUP_PASSPHRASE")
    if value:
        return value
    if not sys.stdin.isatty():
        raise BackupError("Set MIA_BACKUP_PASSPHRASE or run this command in a terminal.")
    value = getpass.getpass("Backup passphrase: ")
    if confirm and getpass.getpass("Repeat passphrase: ") != value:
        raise BackupError("The passphrases did not match; no backup was written.")
    return value


def _show_items(items: list[dict]) -> None:
    for item in items:
        note = " (spend ledger: never overwritten)" if item["rollback_sensitive"] else ""
        print(f"  {item['name']}: {item['state']} -> {item['action']}{note}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Encrypted backup and restore for Mia's local data.")
    commands = parser.add_subparsers(dest="command", required=True)

    create = commands.add_parser("create", help="Write an encrypted backup of the data folder")
    create.add_argument("--data-dir", default=os.getenv("MIA_DATA_DIR", "./data"))
    create.add_argument("--out", required=True, help="New backup file (must not exist)")

    for name, text in (("verify", "Check a backup without writing anything"),
                       ("preview", "Show what a restore would do, writing nothing"),
                       ("restore", "Restore a backup into a folder")):
        sub = commands.add_parser(name, help=text)
        sub.add_argument("archive")
        if name != "verify":
            sub.add_argument("--to", required=True, help="Folder to restore into (stop Mia first if in use)")
            sub.add_argument("--policy", choices=POLICIES, default="abort")

    args = parser.parse_args(argv)
    try:
        if args.command == "create":
            result = create_backup(args.data_dir, args.out, _passphrase(confirm=True))
            print(f"Backup written: {result.path} ({result.archive_bytes} bytes)")
            print(f"Archive SHA-256: {result.archive_sha256}")
            print("Included: " + ", ".join(entry["name"] for entry in result.files))
            if result.excluded_by_policy:
                print("Deliberately excluded (permission grants): " + ", ".join(result.excluded_by_policy))
            print("Provider API keys are in your OS credential store and are not in this backup.")
            print("Keep the passphrase safe: without it the backup cannot be opened.")
        elif args.command == "verify":
            summary = verify_backup(args.archive, _passphrase(confirm=False))
            print(f"Backup verified. Created {summary.created_at}; {len(summary.files)} file(s):")
            for entry in summary.files:
                print(f"  {entry['name']}: {entry['size']} bytes, integrity {summary.integrity[entry['name']]}")
        elif args.command == "preview":
            preview = preview_restore(args.archive, _passphrase(confirm=False), args.to, policy=args.policy)
            print(f"Backup verified (created {preview.created_at}). Policy: {preview.policy}")
            _show_items(preview.items)
            for warning in preview.warnings:
                print("Warning: " + warning)
            print("A restore would succeed." if preview.would_succeed else "A restore would stop on a conflict.")
            return 0 if preview.would_succeed else 2
        else:
            result = restore_backup(args.archive, _passphrase(confirm=False), args.to, policy=args.policy)
            print(f"Restored {len(result.restored)} file(s); {len(result.skipped_identical)} already identical.")
            for original, copy in result.side_by_side.items():
                print(f"  {original} restored as {copy}")
            if result.undo_dir:
                print(f"Replaced files were kept in {Path(result.undo_dir).name} inside the target folder.")
            for warning in result.warnings:
                print("Warning: " + warning)
    except BackupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
