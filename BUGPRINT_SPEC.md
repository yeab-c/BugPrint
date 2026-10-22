# BUGPRINT: Requirements and Design

Version 1.0 | Hackathon: Strands Decider Agent Hackathon | Pattern tag: #tool-selection
Deadline: Oct 23, 2026, 11:59 p.m. PT. Internal target: ship by end of Oct 22.

---

## 0. How to use this file

This file is the single source of truth for the project. A human or an AI coding agent should read all of it before writing code.

Words used in this file:
- MUST = required. SHOULD = do it unless there is a good reason not to. MAY = optional.
- VERIFY = something that could not be confirmed from public docs. Check it against the real installed package before relying on it. Never guess an API.
- Decider = Strands Decider 2B, a small local model that reads a state plus typed questions and returns a choice, a yes/no probability, or a score, each with a confidence. It does not write text.
- Bedrock = Amazon Bedrock, used only to write text.

---

## 1. One-sentence summary

Bugprint is a development flight recorder: it watches your test runs, uses Decider to decide what each failure or fix means, asks you when the evidence is weak, and uses Bedrock to write a short report that only states what the recorded data supports.

## 2. Goals and non-goals

### Goals
1. Record test failures and fixes automatically while a developer works.
2. Let Decider make a real tool-selection decision for each meaningful event, with confidence.
3. Ask the developer to confirm when confidence is low or evidence is weak, instead of recording a guess.
4. Show a timeline, defects, debugging time, every Decider decision, and a report in a dashboard.
5. Ship a reusable, standalone decision pattern called **Evidence-Aware Development Event Router**.
6. Measure Decider on a labeled event set (accuracy, confirmation rate, latency).

### Non-goals (do NOT build in v1)
- Camera monitoring, tracking every action on the PC, detecting mistakes while typing.
- Editor plugins, app-switch tracking, multi-user sync, cloud hosting, login system.
- Test frameworks other than pytest.

---

## 3. Hackathon rules that shape the design

| Rule | What it means for us |
|---|---|
| Decider makes at least one meaningful decision | Tool choice is made by Decider, never by Bedrock or hidden if/else |
| Paired with Bedrock | Bedrock writes the session report only |
| Public repo with OSI license | MIT or Apache-2.0, public from Day 1 |
| Reusable decision pattern in the repo | Folder `patterns/evidence_aware_event_router/`, clearly labeled, standalone |
| Article on Builder Center | Written by humans, uses real numbers from the eval |
| Video under 3 minutes, public | Follows the demo script in section 14 |
| Tags | `#strands-decider-2b` and `#tool-selection` |
| Original, unpublished agent | Do not publish the repo or article before the hackathon starts using this name elsewhere |
| One entry per person | Humans must check on Builder Center whether a team entry is allowed |

---

## 4. What the developer sees (user stories)

1. As a developer, I run `pytest` as usual. Bugprint records failures and fixes without slowing or breaking my tests.
2. As a developer, when a test fails, Bugprint records a possible defect and tells me what kind it looks like (real defect, flaky test, or environment problem).
3. As a developer, when a failing test passes later, Bugprint links the pass to the most likely open defect and computes how long I spent.
4. As a developer, when Bugprint is not sure, it shows me a confirmation request instead of writing a guess. I accept or reject it in the dashboard or in the terminal.
5. As a developer, I can see exactly what Decider chose, how confident it was, and what happened next.
6. As a developer, I get a short report with one suggestion, and every claim in it points to real recorded events.

---

## 5. System overview

```
 pytest run
    |
    v
[1 Tracker: pytest plugin]  ---> writes raw events to SQLite (status = pending)
    |
    v
[2 Pipeline: process_pending()]
    |   for each pending event:
    |     a. Evidence builder: collects facts about the event (error text, files changed, history, open defects)
    |     b. Router: asks Decider a typed choice question with allowed options
    |     c. Guard: if confidence is low, or the choice does not fit the event type, change action to ask_for_confirmation
    |     d. Dispatcher: runs the chosen tool (plain Python function)
    |     e. Saves the decision (options, probabilities, confidence, latency, final action) to SQLite
    v
[3 SQLite]  events, decisions, defects, confirmations, interruptions, reports
    |
    +--> [4 Report generator: Strands Agent on Bedrock] writes summary + one suggestion, then a validator checks every claim
    |
    +--> [5 Dashboard: Streamlit] timeline, defects, decisions, confirmations, report
```

Key principle: **the model classifies, the code acts.** Decider returns a choice and a number. Plain Python decides what to do with that number (the threshold and the guard live in our code, not in the model).

---

## 6. Tech stack

| Part | Choice | Notes |
|---|---|---|
| Language | Python 3.12 (3.11 also fine) | Decider's own README warns that PyTorch wheels can lag very new Python versions. Use a venv. |
| OS | Linux, macOS, or Windows via WSL2 | Decider docs list Linux or WSL2 for NVIDIA GPUs and macOS for Apple silicon. Plain Windows is not listed. VERIFY |
| Decider | `pip install strands-decider` | Runs as a local server, see 7.1 |
| Agent framework | `strands-agents` | Used for the report agent, `@tool` wrappers, and the InterventionHandler adapter |
| Bedrock | `boto3` plus Strands Bedrock model | Model id set by env var. Needs AWS credentials and model access turned on |
| Database | SQLite via built-in `sqlite3` | WAL mode on |
| CLI | `typer` | Command name: `bugprint` |
| Models/validation | `pydantic` v2 | Event, Decision, Evidence classes |
| HTTP | `httpx` or `requests` | Talks to the local Decider server |
| Dashboard | `streamlit` + `pandas` | Reads SQLite directly |
| Tests | `pytest` | Both for our own tests and as the thing we observe |
| Lint | `ruff` | |
| License | MIT or Apache-2.0 | |

Pin exact versions in `requirements.lock` after the first successful install.

### Hardware warning (important)
Decider is a 2B model. The base model download is about 4.5 GB. Docs say a GPU (NVIDIA, or Apple silicon via `mps`) is fast and CPU "works but is much slower". Published latency (about 115 to 153 ms) was measured on an RTX 3090 and an M3 Pro. If the team has no suitable machine, run the Decider server on a rented GPU machine (for example an AWS GPU instance, or a free notebook GPU) and point `DECIDER_URL` at it through an SSH tunnel. Do NOT expose the server to the internet: it has no authentication and binds to 127.0.0.1 by default.

---

## 7. External dependencies (what is known and what to VERIFY)

### 7.1 Decider server (known from public docs)
- Start: `strands-decider serve StrandsAgents/strands-decider-2B-hobson-v19 --port 8099`
  (the docs also show `serve <local checkpoint dir> --device mps|cpu --port N`).
- Health: `GET /health` returns model name, device, calibration info.
- Ask: `POST /v1/systemone` with JSON:

```json
{
  "state": "text the model reads",
  "model": "hobson-latest",
  "questions": {
    "next_tool": {
      "type": "choice",
      "instructions": "Which tool should Bugprint call next?",
      "criteria": {
        "record_possible_defect": "description...",
        "ask_for_confirmation": "description..."
      }
    }
  }
}
```

- Question types: `noul` (yes/no, returns probability of true), `choice` (returns top option, a probability per option, and a confidence), `score` (ordered levels, returns expected value, distribution, confidence).
- For `choice`, confidence is the top probability rescaled so an even split is 0 and a certain answer is 1.
- Docs describe a convention: about 0.9 confidence = act without a check, lower = confirm.
- CLI check: `strands-decider ask <model> --state "..." --choice "Question?=a,b,c"`.

### 7.2 VERIFY before coding (not confirmed in public docs)
1. The exact JSON shape of the `/v1/systemone` response for `choice` questions (key names for the chosen option, probabilities, confidence). Run one real request with `curl` and print it. Then write `DeciderClient` to match.
2. Whether the `model` field value `hobson-latest` is accepted by a server started with the Hugging Face id, or whether another name is needed. `GET /health` should tell you.
3. The Python class names (`Decider`, `Decider.noul`, `StrandsDeciderModel.load`) and import paths. The public docs mention them but do not show imports. The official examples are in `https://github.com/strands-labs/strands-decider/tree/main/examples/strands` (Apache-2.0). Read them in the installed package or repo.
4. The exact Strands API: `strands.interventions` with `InterventionHandler`, `Proceed`, `Deny`, `Confirm`, `Guide`, the `before_tool_call(event, **kwargs)` signature, and `Agent(interventions=[...])`. The public launch post and a community demo show these names. Confirm against the installed `strands-agents` version.
5. How to call a Strands `@tool` function directly (for example `agent.tool.<name>(...)`), or simply call the plain Python function. Plain function calls are acceptable.
6. The Bedrock model id and region that this AWS account can actually use.

Design rule: all of the unverified details are isolated behind two thin adapters, `decider_client.py` and `strands_handler.py`. If the real API differs, only those two files change.

---

## 8. Data model (SQLite)

All timestamps are UTC ISO-8601 strings. IDs are integers unless noted.

```sql
CREATE TABLE sessions (
  id TEXT PRIMARY KEY,              -- uuid4
  started_at TEXT NOT NULL,
  ended_at TEXT,
  project_path TEXT,
  git_commit TEXT
);

CREATE TABLE events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL REFERENCES sessions(id),
  ts TEXT NOT NULL,
  event_type TEXT NOT NULL CHECK (event_type IN ('test_failed','test_passed','idle_gap','focus_lost')),
  test_name TEXT,
  result TEXT,                      -- 'failed' | 'passed' | NULL
  error_text TEXT,                  -- trimmed to 2000 chars, secrets redacted
  files_changed TEXT,               -- JSON list from git (may be empty)
  duration_ms INTEGER,
  gap_seconds INTEGER,              -- for idle_gap
  status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending','processed','skipped','error'))
);

CREATE TABLE decisions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id INTEGER NOT NULL REFERENCES events(id),
  question_id TEXT NOT NULL,        -- 'next_tool' | 'failure_kind' | 'fix_match'
  options TEXT NOT NULL,            -- JSON list offered to Decider
  chosen TEXT NOT NULL,             -- what Decider picked
  probs TEXT NOT NULL,              -- JSON map option -> probability
  confidence REAL NOT NULL,
  threshold REAL,                   -- threshold applied
  final_action TEXT NOT NULL,       -- what Bugprint actually did
  overridden_reason TEXT,           -- NULL | 'low_confidence' | 'incompatible_with_event' | 'no_open_defects'
  latency_ms REAL,
  is_fallback INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE defects (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  test_name TEXT NOT NULL,
  summary TEXT NOT NULL,            -- first line of the error, trimmed
  failure_kind TEXT,                -- real_defect | flaky_test | environment_problem | unknown
  status TEXT NOT NULL CHECK (status IN ('open','fixed','dismissed')),
  confirmed_by_user INTEGER NOT NULL DEFAULT 0,
  opened_event_id INTEGER NOT NULL REFERENCES events(id),
  fixed_event_id INTEGER REFERENCES events(id),
  opened_at TEXT NOT NULL,
  fixed_at TEXT
);

CREATE TABLE confirmations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id INTEGER NOT NULL REFERENCES events(id),
  decision_id INTEGER NOT NULL REFERENCES decisions(id),
  proposed_action TEXT NOT NULL,    -- what Decider leaned toward
  proposed_args TEXT,               -- JSON, e.g. defect id candidate
  reason TEXT NOT NULL,             -- plain text shown to the developer
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','accepted','rejected')),
  created_at TEXT NOT NULL,
  resolved_at TEXT
);

CREATE TABLE interruptions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  event_id INTEGER NOT NULL REFERENCES events(id),
  started_at TEXT NOT NULL,
  seconds INTEGER NOT NULL,
  reason TEXT
);

CREATE TABLE reports (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  model_id TEXT NOT NULL,
  input_stats TEXT NOT NULL,        -- JSON numbers computed in Python
  summary TEXT NOT NULL,
  suggestion TEXT NOT NULL,
  claims TEXT NOT NULL,             -- JSON list of {text, event_ids}
  validated INTEGER NOT NULL        -- 1 if every claim passed the validator
);
```

---

## 9. Functional requirements

### 9.1 Tracker (pytest plugin)

- **FR-T1** MUST be a pytest plugin, activated by `pytest -p bugprint.pytest_plugin` or auto-loaded through a `pytest11` entry point after `pip install -e .`.
- **FR-T2** MUST create one `session` row per pytest run.
- **FR-T3** MUST emit a `test_failed` event for every failing test (call phase), with test name, error text (last 2000 chars of the traceback, secrets redacted), and duration.
- **FR-T4** MUST emit a `test_passed` event only for a test whose most recent stored result is a failure (a possible fix). Other passes only increase a counter in the session. This avoids thousands of useless events.
- **FR-T5** MUST record `files_changed` using `git diff --name-only HEAD` (empty list if not a git repo or git fails).
- **FR-T6** MUST emit an `idle_gap` event at session start if the last stored event is older than `IDLE_GAP_MINUTES` (default 15). The CLI command `bugprint interrupt "reason"` emits a `focus_lost` event manually.
- **FR-T7** At the end of the run, MUST call `process_pending()` (see 9.2). If the Decider server is unreachable, events stay `pending` and the plugin prints one warning line.
- **FR-T8** The plugin MUST NEVER change pytest's exit code or crash a test run. Wrap all plugin logic in try/except and log to `.bugprint/bugprint.log`.

### 9.2 Pipeline

- **FR-P1** `process_pending()` processes events in time order with `status = 'pending'`.
- **FR-P2** For each event it builds Evidence (9.3), calls the Router (9.4), applies the Guard, runs the Dispatcher (9.5), stores the Decision, and sets the event status.
- **FR-P3** An error on one event MUST NOT stop the others. Set that event to `error` and continue.
- **FR-P4** MUST be idempotent: running it twice never creates duplicate decisions.

### 9.3 Evidence builder

Evidence is a plain object with facts only (no conclusions):

```json
{
  "event_id": 42,
  "event_type": "test_failed",
  "test_name": "tests/test_stats.py::test_average_empty",
  "error_type": "ZeroDivisionError",
  "error_excerpt": "ZeroDivisionError: division by zero (stats.py line 8)",
  "files_changed": ["stats.py"],
  "seconds_since_previous_event": 95,
  "previous_results_for_test": ["failed", "failed"],
  "alternating_without_code_change": false,
  "environment_hint": false,
  "open_defects": [
    {"id": 3, "test_name": "tests/test_stats.py::test_average_empty",
     "summary": "ZeroDivisionError: division by zero", "minutes_open": 12,
     "files_overlap": ["stats.py"]}
  ]
}
```

- **FR-E1** `error_excerpt` MUST be at most 500 characters. The whole state text sent to Decider SHOULD stay under about 300 tokens, because published latency numbers are for short inputs.
- **FR-E2** `environment_hint` is true if the error text matches simple patterns (ModuleNotFoundError, ImportError, ConnectionError, Timeout, PermissionError, FileNotFoundError for non-test files). It is only a clue passed to Decider. It never makes the decision.
- **FR-E3** `alternating_without_code_change` is true if the same test flipped between passed and failed in its last 3 runs with an empty `files_changed`.
- **FR-E4** `open_defects` lists at most 5 open defects, most recent first.
- **FR-E5** `files_overlap` is the intersection of the event's `files_changed` and the defect's opening `files_changed`.

### 9.4 Router (the Decider decision)

Three typed questions. All are `choice` questions.

**Question 1: `next_tool` (asked for every routed event)**

Instructions: "Given this development event and its evidence, which tool should Bugprint call next?"

Options (criteria text is sent to Decider and matters a lot, keep it exactly clear):

| Option | Criteria text |
|---|---|
| `record_possible_defect` | A test failed and the error suggests a real problem worth recording. |
| `link_fix_to_defect` | A test that failed earlier now passes, and an open defect in the list matches it. |
| `record_interruption` | The developer was idle, away, or interrupted. This is not a test result. |
| `ask_for_confirmation` | The evidence is unclear, missing, or contradictory, so a person should decide. |

Allowed-option rule: `link_fix_to_defect` is REMOVED from the options when there are no open defects. The other three are always offered. We deliberately do not pre-filter by event type, so the accuracy number measures real tool selection.

**Question 2: `failure_kind` (asked after `record_possible_defect` is chosen)**

Options: `real_defect` (the code under test is wrong), `flaky_test` (result changes without code changes, often timing or randomness), `environment_problem` (missing package, network, permissions, config), `unclear`.
`unclear` is treated as `unknown` and creates a confirmation.

**Question 3: `fix_match` (asked after `link_fix_to_defect` is chosen)**

Options: one per open defect, keyed `defect_<id>` with its summary as the criteria text, plus `none_of_these`.
`none_of_these` creates a confirmation.

**Confidence handling (the Guard), in plain Python:**

```
ACT_THRESHOLD = 0.70   # default, config value, tuned with the eval (section 12)

final_action = decider_choice
if decider_choice == "ask_for_confirmation": final_action = "ask_for_confirmation"
elif confidence < ACT_THRESHOLD:             final_action = "ask_for_confirmation"; reason = "low_confidence"
elif not compatible(decider_choice, event):  final_action = "ask_for_confirmation"; reason = "incompatible_with_event"
```

Compatibility table (the guard): `record_possible_defect` fits only `test_failed`. `link_fix_to_defect` fits only `test_passed` with open defects. `record_interruption` fits only `idle_gap` and `focus_lost`. `ask_for_confirmation` fits everything.

- **FR-R1** The Router MUST call Decider through `DeciderClient` only. It MUST NOT call Bedrock.
- **FR-R2** The Router MUST save every decision: options offered, chosen option, all probabilities, confidence, threshold, final action, override reason, latency, fallback flag.
- **FR-R3** `ask_for_confirmation` MUST be a real, reachable outcome. It creates a `confirmations` row with a plain-English reason, and it does NOT create a defect or link anything.
- **FR-R4** Fallback mode: if `ALLOW_FALLBACK=true` and the server is down, a keyword heuristic MAY answer. Those decisions MUST be stored with `is_fallback = 1`, shown with a visible "FALLBACK, not Decider" label, and MUST NOT be used in the demo, the eval, or any published number.

### 9.5 Tools and dispatcher

The four tools are plain Python functions, also exposed as Strands `@tool` for the agent example. The Dispatcher maps the final action name to the function with a fixed dictionary. No LLM picks the tool.

| Tool | Arguments | Effect |
|---|---|---|
| `record_possible_defect` | event_id, failure_kind, summary | Insert a `defects` row (status `open`, `confirmed_by_user = 0`) |
| `link_fix_to_defect` | event_id, defect_id | Set defect `fixed`, set `fixed_event_id` and `fixed_at` |
| `record_interruption` | event_id, reason, seconds | Insert an `interruptions` row |
| `ask_for_confirmation` | event_id, decision_id, proposed_action, proposed_args, reason | Insert a `confirmations` row |

- **FR-D1** Accepting a confirmation MUST run the proposed tool with the stored arguments and mark `confirmed_by_user = 1` where a defect is involved. Rejecting MUST do nothing except mark the confirmation rejected (and mark a related defect `dismissed` if one exists).
- **FR-D2** Confirmation can be resolved in the dashboard (buttons) or with `bugprint confirm <id> --accept|--reject`. It MUST NOT block a pytest run.

### 9.6 Report generator (Bedrock)

- **FR-B1** `bugprint report [--session ID]` computes statistics in Python first: counts of failures, defects by kind, fixes, confirmations pending/accepted/rejected, per-defect debug time, total active debug time (opened to fixed minus overlapping interruptions), and the slowest defect.
- **FR-B2** It sends those numbers plus short event summaries (with ids) to a Strands `Agent` on Bedrock. The model MUST NOT calculate numbers. It only writes words around the numbers it is given.
- **FR-B3** Required output from the model (JSON): `summary` (max 120 words), `suggestion` (one sentence, one thing to try next time), `claims` (list of `{text, event_ids}`).
- **FR-B4** Validator: every claim MUST cite at least one existing event id, and any number in the summary MUST appear in `input_stats`. If validation fails, retry once with the errors. If it fails again, store the report with `validated = 0` and show it with a warning.
- **FR-B5** Minimum data rule: with fewer than 3 recorded failures, the report MUST say there is not enough data to claim a pattern, and give a general, clearly labeled tip instead of a "pattern".
- **FR-B6** The prompt to Bedrock MUST include: "Only state patterns supported by the provided events. If the data is thin, say so. Never invent numbers or events."
- **FR-B7** Redact secrets (AWS keys, tokens, passwords, anything matching common key patterns) from error text before storing it and before sending it to Bedrock.

### 9.7 Dashboard (Streamlit)

Single page, `streamlit run dashboard/app.py`. Sections, top to bottom:

1. **Header numbers**: failures, fixes, open defects, pending confirmations, active debug time.
2. **Timeline**: chronological list or chart of events (failed, passed, interruption) with defect markers.
3. **Pending confirmations**: each with reason, Decider's lean and confidence, Accept and Reject buttons.
4. **Defects table**: test, kind, status, confirmed by user, time to fix.
5. **Decisions table (the proof of Decider)**: event, options offered, chosen, confidence, threshold, final action, override reason, latency, and a visible badge for any fallback row.
6. **Report panel**: summary, suggestion, claims with their event ids, validation badge, a "Generate report" button.
7. **Session picker** in the sidebar.

- **FR-U1** The dashboard MUST work from the seeded demo data without Decider or Bedrock running.
- **FR-U2** It reads SQLite in read-only mode except for confirmation buttons.

### 9.8 CLI

| Command | Purpose |
|---|---|
| `bugprint init` | Create `.bugprint/` and the database |
| `bugprint status` | Check Decider server health, DB path, pending events |
| `bugprint process` | Run `process_pending()` manually |
| `bugprint interrupt "reason"` | Add a `focus_lost` event |
| `bugprint confirm <id> --accept/--reject` | Resolve a confirmation |
| `bugprint report` | Generate a report |
| `bugprint seed-demo` | Load the demo data into the database |
| `bugprint eval` | Run the eval (section 12) |

---

## 10. Decider state text (exact template)

Keep it short and consistent. Example for a failure:

```
EVENT: test_failed
TEST: tests/test_stats.py::test_average_empty
ERROR: ZeroDivisionError: division by zero (stats.py line 8)
FILES CHANGED SINCE LAST RUN: stats.py
SECONDS SINCE PREVIOUS EVENT: 95
PREVIOUS RESULTS FOR THIS TEST: failed, failed
RESULT FLIPS WITHOUT CODE CHANGE: no
ENVIRONMENT CLUE: no
OPEN DEFECTS: 1
- defect_3: tests/test_stats.py::test_average_empty | ZeroDivisionError: division by zero | open 12 min | shares file stats.py
```

For a pass after a failure, the same fields with `EVENT: test_passed` and the open defect list. For an idle gap: `EVENT: idle_gap`, `GAP SECONDS: 1860`, and the last event summary.

Rules: no markdown, no long tracebacks, at most 5 defects listed, fixed field order.

---

## 11. Configuration

All values come from environment variables with defaults, optionally overridden by `bugprint.toml`.

| Name | Default | Meaning |
|---|---|---|
| `BUGPRINT_DB` | `.bugprint/bugprint.db` | Database file |
| `DECIDER_URL` | `http://127.0.0.1:8099` | Local Decider server |
| `DECIDER_MODEL` | `hobson-latest` (VERIFY) | Model field in requests |
| `ACT_THRESHOLD` | `0.70` | Minimum confidence to act without asking |
| `IDLE_GAP_MINUTES` | `15` | Idle gap that creates an `idle_gap` event |
| `ALLOW_FALLBACK` | `false` | Allow the offline heuristic (dev only) |
| `BEDROCK_MODEL_ID` | set by the human after checking access | Model for reports |
| `AWS_REGION` | `us-east-1` | Bedrock region |

Secrets: AWS credentials come from the standard AWS credential chain (`aws configure` or environment). They MUST NEVER be written to the repo. `.gitignore` MUST include `.env`, `*.db`, `.bugprint/`, `.venv/`, `__pycache__/`.

---

## 12. Evaluation requirements

- **FR-V1** Create `eval/events.jsonl` with 60 to 100 labeled events. Each line: `{"id", "event" (Evidence), "expected_action", "expected_kind" (optional), "notes"}`.
- **FR-V2** Composition: about 35% failures that are clearly real defects, 15% flaky, 15% environment problems, 15% passes with one clear matching defect, 10% passes with ambiguous matches, 10% idle gaps. At least 20% of the set MUST be genuinely ambiguous cases where `ask_for_confirmation` is the right answer.
- **FR-V3** Labels MUST be reviewed by a human. An AI may draft events but a human must confirm every label. Labels written and checked only by the same AI would be circular and the article must not claim otherwise.
- **FR-V4** `bugprint eval` prints and saves `eval/results.json` and `eval/results.md` with:
  - Accuracy of `final_action` against the label.
  - Accuracy on decisions where Bugprint acted (confidence at or above threshold), and the share it chose to ask about (coverage).
  - A threshold sweep (0.50 to 0.95 in 0.05 steps): accuracy on acted decisions versus share asked. Use it to pick `ACT_THRESHOLD`.
  - Latency p50 and p95 of the Decider call.
  - Confusion matrix of expected versus chosen action.
  - Optional bonus: the same events through a Bedrock model, with latency and approximate cost per decision, clearly labeled as an approximation.
- **FR-V5** Report the real numbers, even if they are not impressive. Targets (goals, not promises): at least 85% accuracy on acted decisions, and a confirmation rate that is not zero and not close to 100%.

---

## 13. The reusable pattern (hackathon asset)

Folder: `patterns/evidence_aware_event_router/`. It MUST be standalone: it must not import anything from the `bugprint` package, so another builder can copy the folder.

```
patterns/evidence_aware_event_router/
  README.md            what it is, when to use it, how to reuse it, a small example, limits
  questions.py         the typed question(s) and the option set, with criteria text
  router.py            call Decider, apply threshold and compatibility guard, return a RoutingResult
  decider_client.py    thin HTTP client (the only place that knows the response shape)
  strands_handler.py   InterventionHandler adapter that places the router before tool execution
  example.py           runnable example with 3 sample events and printed results
```

README MUST contain: the typed question and option set; the Strands wiring that puts Decider before tool execution; confidence and uncertainty handling; a runnable example; step-by-step reuse instructions; the Decider and Strands versions it was tested with; known limits.

Strands wiring (names to VERIFY against the installed version):

```python
class EvidenceAwareRouterHandler(InterventionHandler):
    name = "evidence-aware-event-router"

    def before_tool_call(self, event, **kwargs):
        # 1. Build evidence from the proposed tool call and the conversation.
        # 2. Ask Decider the typed choice question.
        # 3. If confidence is low or the proposed tool is incompatible: return Confirm(...)
        # 4. If Decider prefers a different allowed tool: return Guide(feedback="Use <tool> instead because ...")
        # 5. Otherwise: return Proceed()
```

Bugprint's own main path routes directly (no LLM in the loop, fastest and cheapest). The handler adapter is the drop-in version for agents where an LLM proposes the tool call. `example.py` MUST show it working at least once with a Bedrock-backed Strands agent, and that run is shown in the article.

---

## 14. Demo project and demo script

### 14.1 `demo_project/` (a small project with planted problems)

| File | Problem planted | Expected Bugprint behavior |
|---|---|---|
| `stats.py` `average([])` raises ZeroDivisionError | Real defect | `record_possible_defect`, kind `real_defect` |
| `tests/test_discount.py` uses unseeded randomness | Flaky | kind `flaky_test` or a confirmation |
| `tests/test_config.py` imports a missing package | Environment problem | kind `environment_problem` |
| Two similar open defects, then one test passes | Ambiguous fix | `ask_for_confirmation` |
| Fix `stats.py`, rerun | Clear fix | `link_fix_to_defect` to the right defect |
| `bugprint interrupt "meeting"` | Interruption | `record_interruption` |

### 14.2 Video script (under 3:00, no music, no third-party logos)

| Time | Show |
|---|---|
| 0:00 to 0:20 | The problem in two sentences. Name Bugprint. |
| 0:20 to 1:30 | Run pytest, failure appears, dashboard shows the Decider choice and confidence. Fix the code, rerun, the fix is linked. Show one confirmation request and accept it. |
| 1:30 to 2:10 | Timeline, defects, and the Bedrock report with cited claims. |
| 2:10 to 2:50 | The pattern folder and the eval numbers (accuracy, confirmation rate, latency). |

---

## 15. Repository layout

```
bugprint/
  README.md  LICENSE  pyproject.toml  requirements.lock  .gitignore  bugprint.toml.example
  src/bugprint/
    __init__.py  config.py  models.py  db.py  schema.sql
    evidence.py  decider_client.py  router.py  tools.py  pipeline.py
    pytest_plugin.py  report.py  redact.py  cli.py
  dashboard/app.py
  patterns/evidence_aware_event_router/   (see section 13)
  eval/  events.jsonl  run_eval.py  results.md
  demo_project/  stats.py  tests/...
  tests/  (our own unit tests)
  docs/  decision-design.md  architecture.md  article-draft.md  screenshots/
```

---

## 16. Non-functional requirements

- **NFR-1 Never break the developer's tests.** Plugin errors are swallowed and logged.
- **NFR-2 Speed.** Plugin overhead per run MUST be under 1 second excluding Decider calls. A Decider call SHOULD be under 500 ms on supported hardware.
- **NFR-3 Honesty.** No fabricated data. Fallback decisions are labeled. Reports cite real events.
- **NFR-4 Privacy.** Everything stays local except the report request to Bedrock, which contains statistics and redacted, trimmed error text.
- **NFR-5 Clean install.** A new user MUST be able to go from `git clone` to a working demo by following the README, in under 20 minutes plus the model download.
- **NFR-6 Code quality.** Type hints, small functions, `ruff` clean, docstrings on public functions.
- **NFR-7 Tests.** At least: DB functions, evidence builder, guard logic (threshold and compatibility), dispatcher, redaction, report validator, and a pipeline test with a fake Decider client.

---

## 17. Acceptance tests (the project is done when these pass)

| ID | Test | Pass condition |
|---|---|---|
| AT-1 | Run `pytest` in `demo_project` | Events appear in SQLite, pytest exit code unchanged by Bugprint |
| AT-2 | Process the failing `average([])` event with the real Decider | A `decisions` row exists with options, probabilities, confidence, latency, `is_fallback = 0`, and a defect row exists |
| AT-3 | Fix the code and rerun | The defect becomes `fixed`, linked to the right event, with a computed time to fix |
| AT-4 | Send a deliberately unclear event | `ask_for_confirmation` happens, a confirmation row exists, and no defect was written |
| AT-5 | Accept that confirmation in the dashboard | The proposed tool runs and `confirmed_by_user = 1` |
| AT-6 | Stop the Decider server and run pytest | Tests still pass, events stay `pending`, one warning line is printed |
| AT-7 | Generate a report with 5 or more failures | `validated = 1`, every claim cites real event ids, numbers match `input_stats` |
| AT-8 | Generate a report with 1 failure | Report says there is not enough data for a pattern |
| AT-9 | `bugprint eval` | Writes accuracy, coverage, threshold sweep, latency, confusion matrix |
| AT-10 | Fresh clone, follow README | Demo runs end to end |
| AT-11 | Open `patterns/evidence_aware_event_router/example.py` | Runs without importing `bugprint` |
| AT-12 | Search the repo history for secrets | None found |

---

## 18. Risks and mitigations

| Risk | Mitigation |
|---|---|
| Decider API differs from the docs | Isolate in `decider_client.py` and `strands_handler.py`. Do the real-request check first (section 7.2). |
| No suitable hardware for Decider | Rent a GPU machine and use an SSH tunnel. Decide on Day 1. |
| Model download is large (about 4.5 GB) | Start the download on Day 1 and use a stable connection. |
| Bedrock access not approved | Request model access on Day 1. Try another region or model. |
| Decisions look trivial | Eval set with messy and ambiguous cases, `failure_kind` and `fix_match` questions, confidence shown in the dashboard. |
| Poor accuracy on some labels | Improve option descriptions (criteria text) and state text. Never hide bad numbers. |
| Time runs out | Cut in this order: Bedrock baseline comparison, dashboard polish, `fix_match` question. Never cut the pattern folder, article, or video. |

---

## 19. Human prerequisites (things an AI agent cannot do for you)

1. Create a builder.aws.com profile (each person who enters).
2. Create or access an AWS account, enable Bedrock model access, run `aws configure`, and find a model id that works in your region.
3. Create the public GitHub repo with an MIT or Apache-2.0 license and invite the teammate.
4. Confirm whether a team entry is allowed. If not, enter under one name and credit the other.
5. Decide where Decider will run (local GPU, Apple silicon, or rented GPU).
6. Review and correct every label in `eval/events.jsonl`.
7. Record the video, publish the article, add both tags, submit.

---

## 20. Definition of done

- All acceptance tests AT-1 to AT-12 pass.
- Repo is public with license, and the pattern folder is clearly labeled.
- Article published with real eval numbers, both tags added.
- Video public, under 3 minutes, no copyrighted music.
- No secrets in the repo history.
- Submitted before the end of Oct 22 (Addis time), with Oct 23 as buffer only.
