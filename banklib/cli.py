"""Shared command-line front end.

``main("natwest")`` / ``main("barclays")`` give identical CLIs, differing only in
which flags a provider's capabilities expose. Every ``input``/``getpass``/
``print``/``sys.exit`` in banklib lives here; the library core stays silent and
raises exceptions.
"""

from __future__ import annotations

import argparse
import getpass
import json
import sys

from .base import Capability
from .credentials import CredentialStore
from .errors import BankError
from .money import format_money
from .registry import get_provider
from .session import default_cache_dir, open_session


def build_parser(provider) -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog=provider.name,
        description=f"Show {provider.display_name} account balances"
        + (", or download transactions as CSV."
           if provider.supports(Capability.DOWNLOAD) else "."))
    ap.add_argument("--show", action="store_true", help="show the browser window")
    ap.add_argument("--json", action="store_true", help="print JSON")
    ap.add_argument("--forget", action="store_true",
                    help="delete stored credentials and exit")
    if provider.supports(Capability.DOWNLOAD):
        ap.add_argument("--download", action="store_true",
                        help="download transactions as CSV (last 12 months) "
                             "instead of showing balances")
        ap.add_argument("--account", action="append", metavar="NAME",
                        help="limit --download to accounts whose name contains NAME "
                             "(repeatable); default is all accounts")
        ap.add_argument("--out", metavar="PATH",
                        help="where to save the --download CSV: a directory or a "
                             "file path (default: current directory)")
    return ap


def prompt_credentials(provider, store: CredentialStore) -> dict:
    print(f"{provider.display_name} credentials not found; they will be stored "
          "for future runs.", file=sys.stderr)
    creds: dict = {}
    for field in provider.credential_fields:
        reader = getpass.getpass if field.secret else input
        value = reader(f"{field.label}: ")
        if not field.secret:
            value = value.strip()
        elif field.pattern:  # secret but validated (e.g. numeric PIN/passcode)
            value = value.strip()
        if not field.validate(value):
            sys.exit(f"{field.label} is invalid.")
        creds[field.name] = value
    where = store.save(creds)
    print(f"Credentials saved to {where}.", file=sys.stderr)
    return creds


def print_table(accounts) -> None:
    name_w = max((len(a.name) for a in accounts), default=0)
    for a in accounts:
        extra = []
        if a.overdraft_limit is not None:
            extra.append(f"overdraft {format_money(a.overdraft_limit)}")
        if a.available is not None and a.available != a.balance:
            extra.append(f"available {format_money(a.available)}")
        acct = f"{a.sort_code or ''} {a.account_number or ''}".strip()
        line = f"{a.name:<{name_w}}  {format_money(a.balance):>12}"
        if acct:
            line += f"  {acct}"
        if extra:
            line += "  (" + ", ".join(extra) + ")"
        print(line)


def main(provider_name: str, argv: list[str] | None = None) -> int:
    provider = get_provider(provider_name)
    args = build_parser(provider).parse_args(argv)

    store = CredentialStore(provider.name, provider.credential_fields)
    if args.forget:
        store.forget()
        print(f"Stored {provider.display_name} credentials removed.")
        return 0

    creds = store.load() or prompt_credentials(provider, store)
    want_download = getattr(args, "download", False)

    accounts = None
    result = None
    on_status = lambda msg: print(msg, file=sys.stderr)  # noqa: E731
    session = open_session(provider.name, creds, headless=not args.show,
                           on_status=on_status)
    try:
        session.start()
        if want_download:
            result = session.download(accounts=getattr(args, "account", None),
                                      out=getattr(args, "out", None))
        else:
            accounts = session.accounts()
    except BankError as e:
        # Capture a screenshot before the browser is torn down.
        if not args.show and session.browser is not None:
            shot = default_cache_dir(provider.name) / "last-error.png"
            if session.browser.screenshot(shot):
                print(f"(screenshot: {shot})", file=sys.stderr)
        session.close(logout=False)
        print(f"{provider.name}: {e}", file=sys.stderr)
        return 1
    session.close(logout=True)

    if want_download:
        if args.json:
            print(json.dumps(result.to_dict(), indent=2))
        else:
            print(f"Saved {', '.join(result.accounts)} to {result.path}")
    elif args.json:
        print(json.dumps([a.to_dict() for a in accounts], indent=2))
    else:
        print_table(accounts)
    return 0


def _natwest() -> int:
    return main("natwest")


def _barclays() -> int:
    return main("barclays")
