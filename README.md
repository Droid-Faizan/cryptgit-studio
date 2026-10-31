# CryptGit Studio

CryptGit Studio is a local-first developer tool for inspecting Python
repositories, organizing evidence from Git history, and reviewing conservative
potentially-unreferenced findings. Deterministic AST analysis and evidence
verification remain the source of truth; optional local Ollama explanations
help a developer interpret uncertainty without authorizing automatic deletion.

## Development scope

Current features include:

- scope-aware Python AST analysis and conservative classifications;
- bounded Git archaeology and deterministic evidence verification;
- optional local Ollama interpretation with strict JSON/evidence validation;
- a CLI JSON workflow and a Streamlit developer review interface;
- local review statuses, notes, and JSON/Markdown exports; and
- regression tests using Python's `unittest` framework.

Ollama, Appwrite, and Sentry are optional integrations. Streamlit is used for
the local developer review interface; source code and full model prompts never
leave the local machine.

## Requirements

- Python 3.11 or newer
- Git, when using the history reader against a repository
- Streamlit, installed with the project dependencies

## Setup

Create and activate a virtual environment, then install the project in
editable mode:

```powershell
py -3.11 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e .
```

## Usage

Run the CLI against a repository path:

```powershell
cryptgit-studio .
```

The current CLI reports the number of files found and commits available. Phase
1A also provides deterministic Python AST analysis:

```powershell
cryptgit-studio scan . --json
```

Use `--history` to add bounded, read-only Git history metadata:

```powershell
cryptgit-studio scan . --json --history --history-limit 20
```

Add `--verify-evidence` to independently validate the generated historical
commit, file, symbol, source location, and normalized AST digest:

```powershell
cryptgit-studio scan . --json --history --verify-evidence
```

Verification statuses are explicit (`verified`, `mismatch`,
`missing_commit`, `missing_file`, `missing_symbol`, `ambiguous`, `unavailable`,
or `unsupported`). Verification applies only to historical snapshots; it does
not turn uncommitted working-tree changes into committed evidence.

## Local AI interpretation

Phase 2 optionally uses a local Ollama service to explain deterministic
findings; it does not replace static analysis or evidence verification.

Install Ollama separately, start its local service, and pull a model:

```powershell
ollama pull llama3.2
```

Then run:

```powershell
cryptgit-studio scan . --json --history --verify-evidence --ai
```

Configuration can be supplied with `OLLAMA_HOST` and `OLLAMA_MODEL`, or with
CLI options:

```powershell
cryptgit-studio scan . --json --ai `
  --ollama-endpoint http://localhost:11434 `
  --ollama-model llama3.2 `
  --ollama-timeout 120
```

The AI report is separate from deterministic findings. It contains validated
interpretations, evidence-ID-linked claims, and explicit errors when Ollama is
unavailable, the model is missing, inference times out, or the response is
invalid. No cloud API, API key, or hosted fallback is used. Packets contain
bounded structured metadata rather than the repository source, and filenames,
commit subjects, and analysis text are treated as untrusted data.

AI output is advisory and may be incomplete or incorrect. It cannot establish
that code is safe to delete, and it cannot override deterministic verification
statuses.

History is reported as unavailable when the target is not a Git repository or
Git cannot be used. Historical function tracking is limited to module-level
Python functions and available file snapshots; it does not prove a definitive
introduction commit, follow arbitrary renames, or establish that code is safe
to delete.

Ordinary non-Git directories still receive AST analysis. Their reports include
an informational `Git history unavailable` warning and never mark historical
evidence as verified. Standard-library imports such as `json`, `dataclasses`,
`typing`, and their submodules are not reported as unresolved project imports;
unknown third-party or project imports remain warnings.

The report identifies module-level functions and conservatively classifies
potentially unreferenced private functions. It is not proof of dead code:
attribute resolution, dynamic dispatch, reflection, runtime registration, and
external callers are intentionally limited or unresolved.

The JSON `heuristic_score` is an ordinal analysis score, not a calibrated
probability. Reports also include `coverage_incomplete`, parsing errors, and
analysis warnings when discovery or import resolution is incomplete. Symlinks
are skipped by default to keep analysis inside the requested repository.

Python discovery excludes `.git`, `.venv`, `venv`, `__pycache__`,
`node_modules`, `build`, `dist`, and `.tox`. Syntax and read errors are
reported in the JSON output while other files continue to be analyzed.

## Reproducible final demo

Create a synthetic Git repository outside the CryptGit Studio package:

```powershell
python demo\create_demo_repository.py $env:TEMP\cryptgit-studio-demo
```

The demo contains:

```text
demo_pkg/
├── __init__.py
└── helpers.py
```

Its history introduces a referenced public function, changes a helper across
commits, adds a potentially unreferenced private function, and adds a
decorated runtime hook classified as uncertain.

Run the deterministic pipeline:

```powershell
python -m cryptgit_studio.cli scan $env:TEMP\cryptgit-studio-demo `
  --json --history --verify-evidence
```

Run with local Ollama explanations:

```powershell
python -m cryptgit_studio.cli scan $env:TEMP\cryptgit-studio-demo `
  --json --history --verify-evidence --ai `
  --ollama-model qwen2.5-coder:3b
```

Then launch the review interface:

```powershell
streamlit run streamlit_app.py --server.address 127.0.0.1
```

Enter the demo path, run the scan, select a finding, review its deterministic
classification and Git evidence, optionally save a human status and note, and
download the JSON or Markdown report. The demo repository can be removed after
validation; no CryptGit Studio source files are modified by these commands.

## Optional Appwrite persistence

CryptGit Studio can persist scan metadata and review decisions with Appwrite
TablesDB. It uploads only scan summaries, relative finding metadata, evidence
statuses, and review notes—not source code, prompts, diffs, or secrets.

Configure these environment variables before launching the UI:

```powershell
$env:APPWRITE_ENDPOINT="https://cloud.appwrite.io/v1"
$env:APPWRITE_PROJECT_ID="..."
$env:APPWRITE_API_KEY="..."
$env:APPWRITE_DATABASE_ID="..."
$env:APPWRITE_SCANS_TABLE_ID="scans"
$env:APPWRITE_FINDINGS_TABLE_ID="findings"
```

Create the two TablesDB tables with columns matching the fields sent by
`appwrite_store.py`. A minimal schema is:

- `scans`: `scan_id` (string), `timestamp` (datetime or string),
  `repository` (string), `python_files` (integer), `functions` (integer),
  and `classification_counts` (stringified JSON or JSON-compatible field).
- `findings`: `scan_id` (string), `symbol` (string), `file` (string),
  `classification` (string), `evidence_status` (stringified JSON or
  JSON-compatible field), `review_status` (string), and `review_note` (string).

The API key needs database/table row read, create, and update scopes. The
adapter stores only the privacy-safe repository display identifier
`<repository>` and repository-relative finding paths. It never uploads source
code, complete diffs, prompts, model responses, or credentials. When variables
are absent, the UI remains fully usable offline and shows Appwrite as not
configured.

## Optional Sentry tracing

Tracing is explicitly opt-in and sends only bounded operational metadata:

```powershell
$env:SENTRY_DSN="https://..."
$env:CRYPTGIT_SENTRY_ENABLED="1"
$env:SENTRY_TRACES_SAMPLE_RATE="1.0"
```

Instrumented stages include repository scanning, Git history, evidence
verification, Ollama inference, AI response validation, and report generation.
Source code, absolute paths, prompts, model responses, diffs, and credentials
are never attached as telemetry. Without both the opt-in flag and DSN, no
Sentry SDK initialization or network activity occurs.

## Tests

Run the initial test suite with:

```powershell
python -m unittest discover -s tests -v
```

## Developer review interface

Launch the local Streamlit interface with:

```powershell
streamlit run streamlit_app.py --server.address 127.0.0.1
```

The interface supports local repository selection, deterministic AST scanning,
bounded Git history, evidence verification, optional local Ollama explanations,
finding filters, review statuses and notes, and JSON/Markdown exports.

Review data is stored outside scanned repositories in
`%USERPROFILE%\.cryptgit-studio\reviews.json` on Windows. It is keyed by a
repository-specific identifier and finding location. If a function moves or
changes its source range, the prior decision is not silently applied to the
new finding.

The interface never executes discovered repository code and never writes to the
scanned repository. AI output remains advisory; deterministic findings and
verification statuses are shown separately.

## Public Render demo

The repository includes a minimal `render.yaml` deployment for a safe public
demo. Render creates the existing synthetic Git demo repository under `/tmp`,
sets `CRYPTGIT_PUBLIC_DEMO_MODE=1`, and starts Streamlit on Render's port.
Public demo mode accepts no arbitrary repository path and does not require
Ollama, Appwrite, or Sentry. Local mode and the full CLI remain unchanged.

To deploy:

1. Create a Render Web Service from this repository.
2. Select the `render.yaml` Blueprint configuration.
3. Deploy without adding secrets. Optional Appwrite and Sentry variables may be
   added later, but are not required for the synthetic demo.

The hosted command is:

```text
python demo/create_demo_repository.py /tmp/cryptgit-studio-demo && streamlit run streamlit_app.py --server.address 0.0.0.0 --server.port $PORT
```
