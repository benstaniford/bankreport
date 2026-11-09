# banklib

Drive UK online banking through a headless Firefox (Selenium) behind **one
provider-abstracted API**. Each bank is a `Provider`; there are no bank-specific
top-level functions, so adding a bank means writing one provider class.

Supported today:

| Provider   | Balances | Transaction download |
|------------|:--------:|:--------------------:|
| `natwest`  | ✅       | ✅ (CSV, last 12 months) |
| `barclays` | ✅       | —                    |

## Install

```sh
pip install -e .[test]      # from this repo
```

Firefox must be installed; Selenium Manager fetches the driver automatically.

## Library usage

```python
import banklib

name = "natwest"
provider = banklib.get_provider(name)

# Credentials are data: load from the keyring (or a 0600 JSON fallback),
# or pass your own dict. The field list drives prompts / web forms generically.
store = banklib.CredentialStore(name, provider.credential_fields)
creds = store.load()

with banklib.open_session(name, creds, headless=True) as session:
    for account in session.accounts():
        print(account.name, banklib.format_money(account.balance))

    # Capability-gated; raises UnsupportedCapability on providers without it.
    if provider.supports(banklib.Capability.DOWNLOAD):
        result = session.download(accounts=["BILLS"], out="~/statements/")
        print(result.path)
```

The core never prompts, prints, or exits — it raises `BankError` subclasses and
takes an optional `on_status` callback for login progress, so it drops into a web
backend unchanged.

## Command line

The library ships a shared CLI used by the thin `natwest` / `barclays` scripts in
`~/dot-files/scripts`:

```
natwest                 show balances
natwest --show          visible browser window (debugging / 2FA)
natwest --json          machine-readable output
natwest --forget        delete stored credentials
natwest --download [--account NAME ...] [--out PATH]
barclays                show balances            (no --download: Barclays has none)
```

Download flags appear only for providers that support downloads.

### Monthly spending report

`scripts/last-month` builds a PDF of the previous calendar month's spending
across both banks from the cached transaction API:

```
last-month                  report the previous calendar month
last-month --month 2026-10  a specific month (YYYY-MM)
last-month --out ~/reports/ write the PDF to a directory or file path
last-month --show           visible browser window (debugging / 2FA)
last-month --no-claude      skip Claude categorisation (label Uncategorised)
```

Barclays is a credit card paid off by the NatWest current account, so the monthly
card settlement leaving NatWest is excluded from the spend total and reported
separately (the real spending is the Barclays purchases it covers). Transactions
are categorised by shelling out to the `claude` CLI, with results cached in
`~/.cache/banklib/categories.json` so repeat runs don't re-ask. Rendering uses
reportlab (`pip install -e .[report]`); the wrapper installs it automatically.

## Adding a provider

Drop a module in `banklib/providers/` with a `@register`-decorated `Provider`
subclass (set `name`, `display_name`, `login_url`, `credential_fields`,
`capabilities`; implement `login` and `scrape_accounts`, and `download` if
supported), then import it from `banklib/providers/__init__.py`.

## Tests

```sh
pytest
```

The suite mocks the browser (live bank login can't run in CI): it covers money
parsing, credential storage (keyring + fallback), the provider registry,
capability gating, positional-secret entry, both providers' scrape parsing, and
the CLI wiring.
```
