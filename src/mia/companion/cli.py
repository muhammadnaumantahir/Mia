"""Command-line entry point for the optional Windows companion agent."""

from __future__ import annotations

import argparse
import getpass
import json
import sys
from typing import Any


def _print(value: dict[str, Any]) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2))


def _identity():
    from mia.companion.identity import CompanionIdentity

    return CompanionIdentity()


def _lifecycle(identity):
    from mia.companion.lifecycle import WindowsCompanionLifecycle

    return WindowsCompanionLifecycle(identity)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mia-companion",
        description="Manage Mia's outbound-only, least-privilege Windows companion.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    pair = sub.add_parser("pair", help="Pair this Windows device using an owner-created one-time code")
    pair.add_argument("--server", required=True, help="Mia loopback URL or verified HTTPS URL")
    pair.add_argument("--name", default="Mia Windows Companion", help="Visible device name")
    pair.add_argument("--code", help="One-time pairing code; otherwise it is requested securely")

    sub.add_parser("run", help="Run the outbound heartbeat/reconnect loop in the foreground")
    sub.add_parser("status", help="Show local device and process health without secrets")
    sub.add_parser("start", help="Start the agent in the current-user session")
    sub.add_parser("stop", help="Request graceful stop; never force-kills the process")

    forget = sub.add_parser(
        "forget", help="Remove this user's protected local identity after the device has been revoked in Mia"
    )
    forget.add_argument("--confirm", action="store_true",
                        help="Confirm deletion of the local protected device identity")

    for name, help_text in (
        ("install", "Install an explicit current-user logon task at Limited privilege"),
        ("uninstall", "Remove the current-user logon task"),
        ("update", "Gracefully restart the installed/current-user companion after an application update"),
    ):
        item = sub.add_parser(name, help=help_text)
        item.add_argument(
            "--confirm", action="store_true",
            help="Confirm this Windows logon-task change or restart",
        )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        identity = _identity()
        if args.command == "pair":
            from mia.companion.agent import CompanionAgent

            code = args.code or getpass.getpass("One-time pairing code (not echoed): ").strip()
            agent = CompanionAgent(identity)
            try:
                result = agent.pair(args.server, code, args.name)
            finally:
                agent.close()
            _print({"paired": True, **result})
            return 0

        if args.command == "run":
            from mia.companion.agent import CompanionAgent

            agent = CompanionAgent(identity)
            try:
                return agent.run_forever()
            finally:
                agent.close()

        manager = _lifecycle(identity)
        if args.command == "forget":
            if not args.confirm:
                raise RuntimeError(
                    "forget deletes the protected local device identity; pass --confirm after revoking it in Mia"
                )
            if not manager.stop(timeout_seconds=20):
                raise RuntimeError(
                    "The companion is still running; local identity was not removed"
                )
            deleted = identity.clear()
            _print({
                "forgotten_locally": deleted,
                "server_device_revoked": False,
                "note": "Revoke the device in Mia's Companion devices page before forgetting it here.",
            })
            return 0
        if args.command == "status":
            _print(manager.status())
            return 0
        if args.command == "start":
            _print(manager.start())
            return 0
        if args.command == "stop":
            stopped = manager.stop()
            _print({"stopped": stopped, "graceful": True})
            return 0 if stopped else 2
        if args.command == "install":
            _print(manager.install(confirmed=args.confirm))
            return 0
        if args.command == "uninstall":
            _print(manager.uninstall(confirmed=args.confirm))
            return 0
        if args.command == "update":
            _print(manager.restart_update(confirmed=args.confirm))
            return 0
        raise AssertionError("unsupported companion command")
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        from mia.companion.agent import CompanionAgentError
        from mia.companion.lifecycle import CompanionLifecycleError
        from mia.companion.dpapi import CompanionSecretProtectionError

        if isinstance(exc, (
            CompanionAgentError, CompanionLifecycleError, CompanionSecretProtectionError,
            FileNotFoundError, RuntimeError, ValueError, OSError,
        )):
            print(f"mia-companion: {exc}", file=sys.stderr)
            return 2
        raise


if __name__ == "__main__":
    raise SystemExit(main())
