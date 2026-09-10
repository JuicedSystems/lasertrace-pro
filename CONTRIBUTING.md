# Contributing to LaserTrace Pro

Thanks for looking. This project exists because generic vectorizers produce
files that misbehave on a galvo, so the most valuable contributions are the
ones grounded in what your machine actually did.

## The most useful bug report

**"This file broke my EZCAD/LightBurn import."** Include:

- the source image (or a stand-in that reproduces it),
- the exported file,
- the controller and version (EZCAD2, EZCAD3, LightBurn 1.x/2.x),
- what you expected on the part and what you got.

Every export embeds the job settings that made it, and a depth pack's
`_plan.json` carries the settings, the report and the warnings — attaching one
of those means the exact run can be reproduced.

## Getting set up

```bash
git clone https://github.com/JuicedSystems/lasertrace-pro.git
cd lasertrace-pro
python3.11 -m venv .venv
./.venv/bin/pip install -r requirements-ui.txt
./.venv/bin/pip install -e .
pytest          # 118 tests, ~2 minutes, green from a fresh clone
```

The fixture set is committed, so tests run immediately. To regenerate it:
`python tools/make_fixtures.py`.

## House rules

**Keep the core Qt-free.** Everything in `lasertrace/` must import and run
without PySide6. The desktop shell lives in `lasertrace_ui/` and depends on the
core, never the other way round.

**Every pipeline stage is a pure function with a test.** Same input, same
settings, same bytes out — the regression harness and the "reproduce last
year's export" promise both depend on it.

**Licence hygiene is not optional.**

- Potrace is GPL-3. It lives *only* in `sidecars/potrace_sidecar/` and is
  invoked as a subprocess. Never `import potracer` in the core.
- PyMuPDF is AGPL and must not be added. PDF input goes through pypdfium2.
- New dependencies need a permissive licence and a line in
  [`docs/TECH_STACK.md`](docs/TECH_STACK.md) and [`NOTICE`](NOTICE).

**Quality is measured, not asserted.** If you touch the tracer, run:

```bash
python tools/regress.py
```

and say in the PR what moved. It traces all 38 fixtures and fails on node-count
blowups, lost holes, open fills, fidelity below 85 %, or overlapping fills.
`tests/test_fixtures_quality.py` is the slow test; the rest are quick.

**Depth invariants** (these have each caused a real bug — see
[`docs/DEPTH_ENGRAVING.md`](docs/DEPTH_ENGRAVING.md)):

- Slices stay cumulative and nested; never run `union_overlapping_fills` on a
  depth graph.
- `Path.depth_index` must survive `transform_graph`, or every layer collapses
  to `ENGRAVE_FILL`.
- The depth graph is framed on the relief footprint (`height > 0`), the same
  region the height PNG is cropped to — otherwise the DXF and the PNG end up at
  different scales.
- `Layer.IGNORE` is never merged by "Flatten to one layer": it carries the
  per-slice alignment frame, which must not be marked.
- IoU and hole statistics are meaningless for stacked slices.

## Documentation and help

User-facing help is plain text in
[`lasertrace_ui/help_content.py`](lasertrace_ui/help_content.py), rendered by a
small Markdown subset (headings, lists, tables, `> ` notes, `**bold**`,
`*italic*`, `` `code` ``, and `[label](topic:id)` cross-links). `tests/test_help.py`
fails on unrendered markup and broken cross-links, so adding a topic is safe.
Corrections there are excellent first PRs.

## Pull requests

- One concern per PR.
- Say what you measured, not just what you changed.
- New behaviour comes with a test; a bug fix comes with the test that would
  have caught it.
