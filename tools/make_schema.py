"""Write JSON schemas for the preset and job files (for editors / validation)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from lasertrace.models import Job, Preset  # noqa: E402


def main() -> None:
    out = ROOT / "schemas"
    out.mkdir(exist_ok=True)
    (out / "preset.schema.json").write_text(json.dumps(Preset.model_json_schema(), indent=2), encoding="utf-8")
    (out / "job.schema.json").write_text(json.dumps(Job.model_json_schema(), indent=2), encoding="utf-8")
    print(f"wrote {out / 'preset.schema.json'} and {out / 'job.schema.json'}")


if __name__ == "__main__":
    main()
