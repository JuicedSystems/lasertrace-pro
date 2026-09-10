## What this changes

<!-- One concern per PR. What problem on the machine or in the code does this solve? -->

## What you measured

<!--
Quality here is measured, not eyeballed. If you touched the tracer or the depth
pipeline, run `python tools/regress.py` and paste what moved (node counts,
holes, fidelity). If nothing moved, say that.
-->

- [ ] `pytest` is green
- [ ] `python tools/regress.py` run (tracer/depth changes only) — what moved:

## Checklist

- [ ] The core (`lasertrace/`) still imports and runs without PySide6
- [ ] New behaviour has a test; a bug fix has the test that would have caught it
- [ ] No new GPL/AGPL dependency in the core (Potrace stays in the sidecar)
- [ ] Any new dependency is listed in `docs/TECH_STACK.md` and `NOTICE`
- [ ] User-visible changes are reflected in the in-app help (`lasertrace_ui/help_content.py`)
