# RUNBOOK — generate a contract when the AI agent (Sage) is unavailable

This is the single file to follow (human **or** AI assistant like Claude Code)
to create a Switch Case Studio agreement and signing link **without Sage**.

You do **not** need to build or run the SwitchSign app, Docker, or a local
server. The generator script calls the **live production API** over the
internet. The only things required are Python, two small packages, and the
admin API key.

---

## If you are an AI assistant (e.g. Claude Code)

1. Read [`../../integrations/openclaw/agent-system-prompt.md`](../../integrations/openclaw/agent-system-prompt.md).
   Those are your drafting rules: required inputs, the **clarifying questions**
   to ask Moshe, the **default terms**, and the contract structure. Act as the
   drafting assistant — interview Moshe for anything missing, then generate.
2. Look at [`intake.example.txt`](intake.example.txt) for the intake format.
3. Follow "Generate a contract" below.

---

## One-time setup

```bash
# from the repo root
python3 -m venv .venv
. .venv/bin/activate
pip install -r scripts/contracts/requirements.txt
```

Create a `.env` file at the repo root (it is gitignored — never commit it).
**The API key is NOT in the repo — ask Moshe for it:**

```
SWITCHSIGN_BASE_URL=https://contracts.example.com
SWITCHSIGN_API_KEY=<ask Moshe for the admin API key>
```

## Generate a contract

```bash
# 1) Copy the example and fill in the project details
cp scripts/contracts/intake.example.txt scripts/contracts/<client>.txt
#    (an AI assistant should fill this from Moshe's answers, applying the
#     defaults and asking the clarifying questions from agent-system-prompt.md)

# 2) Render + review — writes scripts/contracts/out/…md, sends nothing
python scripts/generate_contract.py scripts/contracts/<client>.txt

# 3) Create the signing link
python scripts/generate_contract.py scripts/contracts/<client>.txt --send
```

Step 3 prints the **signing URL**, expiry, and contract id. Send the URL to the
client. Done — the rest (signing page, PDF, emails, audit log) is handled by the
live app.

## Notes

- Required intake fields: `client_name`, `client_email`, `contract_title`,
  `project_overview`, `deliverables`, `project_fee`, `timeline`. The script
  lists anything missing (the same things Sage would ask about).
- Optional `client_company` / `client_title` pre-fill the signing form.
- To hand-edit wording first: render (step 2), edit the `out/…md`, then
  `--send --body scripts/contracts/out/<file>.md`.
- Canonical template (do not hand-write the parties intro or signatures —
  SwitchSign adds them): `integrations/openclaw/contract-templates/service-agreement.j2`.
