# Decisions log
# Tracks design choices made when the spec left something open.

## 2026-10-16: Python version
- Spec says 3.12 (3.11 also fine). User has 3.13.
- PyTorch wheels for 3.13 exist as of PyTorch 2.5+. Will try 3.13 first.
- If strands-decider doesn't support 3.13, we'll create a 3.12 venv.
