"""Generate transparent fixtures and import the two Chinese guided scenarios."""
import csv
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.scenarios import SCENARIOS, scenario_rows

for key, spec in SCENARIOS.items():
    header, rows = scenario_rows(key)
    with (ROOT / "examples" / spec["filename"]).open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)

if __name__ == "__main__":
    from app.main import import_scenario
    for key in SCENARIOS:
        meta = import_scenario(key)
        print(f"{key}: {meta['quality']['input_rows']} raw / {meta['quality']['valid_rows']} valid")
