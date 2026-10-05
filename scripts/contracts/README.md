# Generate a contract without Sage (token-free fallback)

When the AI agent (Sage) is unavailable, you can generate and send a signing
link yourself from a short intake file. Same template, same output.

## One-time setup

Create a local `.env` at the repo root (it's gitignored — never commit it):

```
SWITCHSIGN_BASE_URL=https://contracts.example.com
SWITCHSIGN_API_KEY=your-admin-api-key
```

Use a Python env that has `jinja2` and `python-dotenv` (the `.venv-render`
env already does).

## Make a contract

1. Copy the example and fill it in:

   ```
   cp scripts/contracts/intake.example.txt scripts/contracts/northwind-bakery.txt
   # edit the values
   ```

2. Render and review (does **not** send anything):

   ```
   .venv-render/bin/python3 scripts/generate_contract.py scripts/contracts/northwind-bakery.txt
   ```

   It writes the contract markdown to `scripts/contracts/out/…md`. Open it,
   tweak any wording if the project is unusual.

3. Create the signing link:

   ```
   .venv-render/bin/python3 scripts/generate_contract.py scripts/contracts/northwind-bakery.txt --send
   ```

   Prints the signing URL, expiry, and contract id. Send the URL to the client.

   To send a markdown file you edited by hand, add `--body path/to/file.md`.

## Notes

- Required fields: `client_name`, `client_email`, `contract_title`,
  `project_overview`, `deliverables`, `project_fee`, `timeline`. If any are
  missing the script tells you exactly what to add — the same questions Sage
  asks.
- The renderer adds the cover, the formal preamble, and the signature block
  automatically. Your body should start at the Quick Summary / sections —
  don't write a parties intro or a signatures section.
- The canonical template lives at
  `integrations/openclaw/contract-templates/service-agreement.j2`.
