# Public evidence and reproduction

This distribution contains the verified R14 application source, tests, pinned Windows wheels, and local demonstration media. Historical raw observer frames and machine-specific paths were removed before public release. Fresh runs generate their own evidence under `runtime/evidence/` and `runtime/validation/`.

After `setup_windows.ps1`, run `scripts/judge_smoke_current.py` and `scripts/fresh_entry_r14.py` as shown in the top-level README. The first command generates a JUnit report; the second emits seven installed-entry checks. The frozen R14 validation had 113 passing tests. A judge's own run is the current result.

Historical demonstrations of Stop, Recovery, and independent verification are described in `docs/JUDGE_GUIDE.md` and the submitted video; live observations remain bounded.
