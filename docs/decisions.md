# Decisions log
# Tracks design choices made when the spec left something open.

## 2026-10-16: Python version
- Spec says 3.12 (3.11 also fine). User has 3.13.
- PyTorch wheels for 3.13 exist as of PyTorch 2.5+. Will try 3.13 first.
- If strands-decider doesn't support 3.13, we'll create a 3.12 venv.
- **Outcome**: strands-decider 0.1.0 + torch 2.14.1 installed cleanly on Python 3.13.3. ✓

## 2026-10-22: Decider API — spec vs reality (confirmed from installed schema.py)

### Model name
- **Spec §7.2** assumed: `hobson-latest` / `hobson-v19`
- **Real model**: `StrandsAgents/strands-decider-2B-qwen3.5-v1-2610`
- The `model` field in the request body is optional (defaults to `"strands-decider-latest"`
  which is resolved to the checkpoint the server was started with).

### Response field for selected choice
- **Spec §7.2** assumed: `"selected"` key in choice answer
- **Real schema** (`strands_decider.schema.ChoiceAnswer`): `"choice"` key
- All code uses `answer["choice"]`, not `answer["selected"]`.

### Response structure (confirmed from `schema.py` + `server.py`)
```json
{
  "model": "<checkpoint name>",
  "answers": {
    "<question_id>": {
      "type": "choice",
      "choice": "<winning option name>",
      "probabilities": { "<option>": 0.0, ... },
      "confidence": 0.0
    }
  },
  "usage": {
    "input_tokens": 0,
    "output_tokens": 0
  },
  "latency_ms": 0.0
}
```
Note: `latency_ms` is injected by the server after calling `SystemOneResponse.model_dump()`.
It is NOT in the Pydantic response model but IS present in the HTTP response JSON.

### Confidence formula
- For `choice`: `(N * p_max - 1) / (N - 1)` — normalised max-probability.
  Uniform → 0.0, one-hot → 1.0. Threshold-comparable across any N.

### `noul` answers
- Returns `{ "type": "noul", "noul": <float 0–1> }` — just the probability of `true`.
- No `confidence` field (with 2 outcomes, p itself is the uncertainty measure).

### InterventionHandler API (confirmed from installed strands-agents 1.61.1)
- Subclass `InterventionHandler`; must define `name` property.
- Override `before_tool_call(self, event: BeforeToolCallEvent, **kwargs)`.
- Access tool name via `event.tool_use["name"]` and args via `event.tool_use["input"]`.
- Cancel a tool call by setting `event.cancel_tool = "reason"` (or `True` for default msg).
- Return `Proceed()`, `Deny(reason=...)`, `Guide(feedback=...)`, `Confirm(...)`, or `Transform(...)`.
- Both sync and async implementations are supported.

## 2026-10-22: Simplest fallback strategy
- Spec says fallback must be labeled and excluded from eval/demo.
- Decision: `allow_fallback = false` by default. When Decider is unavailable and fallback
  is disabled, log the error and skip tool dispatch for that event (event stays `pending`
  in the DB, can be retried). This is safer than a hidden heuristic.
- If user opts into fallback (`allow_fallback = true`), use a simple rule:
  `test_failed` → `record_possible_defect`, `idle` → `record_interruption`, else → skip.
  All fallback decisions are tagged `"source": "fallback"` in the DB and excluded from
  eval and reports by a WHERE clause.
