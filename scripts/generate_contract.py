#!/usr/bin/env python3
"""
Generate a Switch Case Studio service agreement from a simple intake file —
no AI agent required. Renders the canonical template, writes the contract
markdown for review, and (with --send) creates the SwitchSign signing link.

Usage
-----
    # 1) Render only — writes the contract markdown so you can review/edit it
    python scripts/generate_contract.py scripts/contracts/my-project.txt

    # 2) Create the signing link (hits the live API; needs .env, see below)
    python scripts/generate_contract.py scripts/contracts/my-project.txt --send

    # 3) Send a markdown body you edited by hand
    python scripts/generate_contract.py scripts/contracts/my-project.txt --send \
        --body scripts/contracts/out/2026-05-29-my-project.md

Sending requires SWITCHSIGN_BASE_URL and SWITCHSIGN_API_KEY. Put them in a
local .env at the repo root (gitignored):

    SWITCHSIGN_BASE_URL=https://contracts.example.com
    SWITCHSIGN_API_KEY=your-admin-api-key
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is expected, but degrade gracefully
    load_dotenv = None

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / "integrations" / "openclaw" / "contract-templates"
TEMPLATE_NAME = "service-agreement.j2"
OUT_DIR = REPO_ROOT / "scripts" / "contracts" / "out"

REQUIRED = {
    "client_name": "Who is the client? (legal business name, or full name if an individual)",
    "client_email": "What email should the signing link and signed copy go to?",
    "contract_title": "What is the project/agreement title?",
    "project_overview": "Give a 2-3 sentence overview of the project.",
    "deliverables": "List what the client gets (at least one deliverable).",
    "project_fee": "What is the total project fee?",
    "timeline": "What is the delivery timeline? (e.g. '3-5 weeks')",
}

DEFAULTS = {
    # Example studio defaults (used when the intake doesn't override). Set your own.
    # deposit + milestones, 3 revision rounds, 7-day satisfaction guarantee + 50% refund.
    "payment_installments": ["50% deposit due on signing", "50% due on delivery"],
    "out_of_scope": [
        "Logo or full brand identity design",
        "Photography, videography, or stock asset licensing",
        "Paid advertising or advanced SEO campaigns",
        "Booking systems, e-commerce, memberships, or custom backend applications",
        "Legal, medical, financial, or industry-specific compliance review",
    ],
    "revision_rounds": "three (3)",
    "satisfaction_days": "7",
    "support_days": "14",
    "hourly_rate": "100",
    "governing_state": "[Your State]",
    "jurisdiction": "[Your County, State]",
    "hosting_price": "",
    "hosting_annual_fee": "",
    "payment_terms_short": "",
    "quick_summary": [],
}

BOOL_FIELDS = ("hosting_included", "hosting_first_year_included")
_TRUE = {"yes", "true", "y", "1", "on"}


def parse_intake(path: Path) -> dict:
    """Parse the forgiving intake format: `key: value`, and lists as a blank
    `key:` followed by `- item` lines. `#` lines are comments."""
    data: dict = {}
    current_key: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if current_key and stripped.startswith("- "):
            if not isinstance(data.get(current_key), list):
                data[current_key] = []
            data[current_key].append(stripped[2:].strip())
            continue
        match = re.match(r"^([A-Za-z_][A-Za-z0-9_]*):\s?(.*)$", line)
        if not match:
            continue
        key, value = match.group(1), match.group(2).strip()
        if value == "":
            data[key] = ""  # may become a list if `- ` items follow
            current_key = key
        else:
            data[key] = value
            current_key = None
    return data


def fee_display(value) -> str:
    digits = re.sub(r"[^\d.]", "", str(value))
    if not digits:
        return str(value)
    try:
        return f"{int(float(digits)):,}"
    except ValueError:
        return str(value)


def build_context(data: dict) -> dict:
    ctx = dict(DEFAULTS)
    ctx.update({k: v for k, v in data.items() if v not in ("", [])})

    for field in BOOL_FIELDS:
        ctx[field] = str(data.get(field, "")).strip().lower() in _TRUE

    ctx["project_fee_display"] = fee_display(data["project_fee"])

    installments = ctx["payment_installments"]
    if not ctx["payment_terms_short"]:
        if len(installments) == 1:
            ctx["payment_terms_short"] = re.sub(r"^100% ", "", installments[0])
        else:
            ctx["payment_terms_short"] = "paid in installments"

    if not ctx["quick_summary"]:
        bullets = list(ctx["deliverables"][:4])
        bullets.append(f"Timeline: {ctx['timeline']}")
        if ctx["hosting_included"] and ctx["hosting_price"]:
            bullets.append(f"Managed hosting at {ctx['hosting_price']}")
        ctx["quick_summary"] = bullets
    return ctx


def render_markdown(ctx: dict, template_name: str = TEMPLATE_NAME) -> str:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        trim_blocks=True,
        lstrip_blocks=True,
        undefined=StrictUndefined,
        keep_trailing_newline=True,
    )
    body = env.get_template(template_name).render(**ctx)
    # Collapse any runs of 3+ blank lines the conditionals may leave behind.
    return re.sub(r"\n{3,}", "\n\n", body).strip() + "\n"


def slugify(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    return value or "contract"


def create_contract(base_url: str, api_key: str, payload: dict) -> dict:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/api/contracts",
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a SwitchSign contract from an intake file.")
    parser.add_argument("intake", type=Path, help="Path to the intake file.")
    parser.add_argument("--send", action="store_true", help="Create the signing link via the live API.")
    parser.add_argument("--body", type=Path, help="Use this markdown file as the body instead of rendering.")
    parser.add_argument("--out", type=Path, help="Where to write the rendered markdown.")
    parser.add_argument(
        "--template",
        default=TEMPLATE_NAME,
        help=f"Template file in {TEMPLATE_DIR.name}/ to render (default: {TEMPLATE_NAME}).",
    )
    args = parser.parse_args()

    if not args.intake.exists():
        print(f"Intake file not found: {args.intake}", file=sys.stderr)
        return 1

    data = parse_intake(args.intake)

    missing = [field for field in REQUIRED if not data.get(field)]
    if missing:
        print("Cannot generate — the intake is missing required information:\n", file=sys.stderr)
        for field in missing:
            print(f"  • {field}: {REQUIRED[field]}", file=sys.stderr)
        print("\nFill those in and run again.", file=sys.stderr)
        return 1

    ctx = build_context(data)

    if args.body:
        markdown_body = args.body.read_text(encoding="utf-8")
        out_path = args.body
    else:
        markdown_body = render_markdown(ctx, args.template)
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out_path = args.out or OUT_DIR / f"{date.today().isoformat()}-{slugify(data['contract_title'])}.md"
        out_path.write_text(markdown_body, encoding="utf-8")

    print(f"\nContract markdown written to:\n  {out_path}\n")
    preview = "\n".join(markdown_body.splitlines()[:16])
    print("--- preview --------------------------------------------------")
    print(preview)
    print("--------------------------------------------------------------\n")

    if not args.send:
        print("Review (and edit the .md if needed), then re-run with --send to create the signing link.")
        print("  e.g.  python scripts/generate_contract.py "
              f"{args.intake} --send" + (f" --body {out_path}" if args.body else ""))
        return 0

    if load_dotenv is not None:
        load_dotenv(REPO_ROOT / ".env")
    import os

    base_url = os.environ.get("SWITCHSIGN_BASE_URL")
    api_key = os.environ.get("SWITCHSIGN_API_KEY")
    if not base_url or not api_key:
        print("--send needs SWITCHSIGN_BASE_URL and SWITCHSIGN_API_KEY (in a local .env or your shell).",
              file=sys.stderr)
        return 1

    payload = {
        "client_name": data["client_name"],
        "client_email": data["client_email"],
        "contract_title": data["contract_title"],
        "markdown_body": markdown_body,
    }
    if data.get("client_company"):
        payload["client_company_name"] = data["client_company"]
    if data.get("client_title"):
        payload["client_title"] = data["client_title"]
    try:
        result = create_contract(base_url, api_key, payload)
    except urllib.error.HTTPError as exc:
        print(f"API error {exc.code}: {exc.read().decode('utf-8', 'replace')}", file=sys.stderr)
        return 1
    except urllib.error.URLError as exc:
        print(f"Could not reach SwitchSign: {exc.reason}", file=sys.stderr)
        return 1

    print(f"Agreement created for {data['client_name']}.")
    print(f"Signing link: {result.get('signing_url')}")
    print(f"Expires:      {result.get('expires_at')}")
    print(f"Contract id:  {result.get('id')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
