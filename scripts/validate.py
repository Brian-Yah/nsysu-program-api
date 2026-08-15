from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

root = Path(__file__).resolve().parents[1]
schema = json.loads((root / "schemas/program.schema.json").read_text(encoding="utf-8"))
validator = Draft202012Validator(schema, format_checker=FormatChecker())
errors = []
ids = set()
for path in (root / "data/published").glob("*/*.json"):
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("program_id") in ids:
        errors.append(f"{path}: duplicate program_id {data['program_id']}")
    ids.add(data.get("program_id"))
    errors.extend(f"{path}: {error.message}" for error in validator.iter_errors(data))
if errors:
    print("\n".join(errors))
    sys.exit(1)
print(f"Validated {len(ids)} published programs")
