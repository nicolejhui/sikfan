"""
Import Dexcom Clarity CSV export into the CGM store.

Usage:
    python scripts/import_dexcom_csv.py --file path/to/dexcom_export.csv
"""
import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from pipeline.glucose_store import save_cgm_reading, _load, _CGM_FILE, _ensure_files


def derive_trend(current_glucose: int, prev_glucose: int, minutes_elapsed: float) -> str:
    rate = (current_glucose - prev_glucose) / minutes_elapsed
    if rate > 3:
        return "rising_rapidly"
    if rate > 1:
        return "rising"
    if rate < -3:
        return "falling_rapidly"
    if rate < -1:
        return "falling"
    return "flat"


def _parse_timestamp(raw: str) -> str:
    """Normalize Dexcom timestamp to ISO 8601 (no trailing timezone suffix)."""
    raw = raw.strip()
    for fmt in (
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%Y %H:%M:%S",
        "%m/%d/%Y %I:%M %p",
    ):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            continue
    raise ValueError(f"Unrecognised timestamp format: '{raw}'")


def parse_dexcom_csv(filepath: str) -> list[dict]:
    """Parse Dexcom Clarity CSV and return list of CGM reading dicts."""
    readings = []
    prev_glucose = None
    prev_dt = None

    with open(filepath, newline="", encoding="utf-8-sig") as f:
        # Dexcom exports may have metadata rows at the top; skip until header row found
        lines = f.readlines()

    header_idx = None
    for i, line in enumerate(lines):
        lower = line.lower()
        if "timestamp" in lower and ("glucose" in lower or "index" in lower):
            header_idx = i
            break

    if header_idx is None:
        raise ValueError("Could not find header row in CSV (expected 'Timestamp' and 'Glucose')")

    reader = csv.DictReader(lines[header_idx:])

    # Normalise column names: strip whitespace, lowercase for matching
    def _find_col(fieldnames, *candidates):
        for name in fieldnames:
            normalised = name.strip().lower()
            for c in candidates:
                if c in normalised:
                    return name
        return None

    fieldnames = reader.fieldnames or []
    ts_col = _find_col(fieldnames, "timestamp")
    glucose_col = _find_col(fieldnames, "glucose value", "glucose (mg/dl)", "glucose")

    if not ts_col or not glucose_col:
        raise ValueError(
            f"Required columns not found. Headers: {fieldnames}"
        )

    for row in reader:
        raw_ts = row.get(ts_col, "").strip()
        raw_glucose = row.get(glucose_col, "").strip()

        if not raw_ts or not raw_glucose:
            continue

        # Skip non-numeric glucose values (sensor warm-up shows "Low", "High", etc.)
        try:
            glucose_val = int(float(raw_glucose))
        except ValueError:
            continue

        try:
            ts_str = _parse_timestamp(raw_ts)
        except ValueError:
            continue

        curr_dt = datetime.fromisoformat(ts_str)

        if prev_glucose is not None and prev_dt is not None:
            mins_elapsed = (curr_dt - prev_dt).total_seconds() / 60
            if mins_elapsed > 0:
                trend = derive_trend(glucose_val, prev_glucose, mins_elapsed)
            else:
                trend = "flat"
        else:
            trend = "flat"

        readings.append({
            "timestamp": ts_str,
            "glucose_mgdl": glucose_val,
            "trend": trend,
            "source": "dexcom_csv",
        })

        prev_glucose = glucose_val
        prev_dt = curr_dt

    return readings


def import_to_store(readings: list[dict]) -> dict:
    """Import parsed readings into glucose_store; return summary dict."""
    _ensure_files()
    existing = _load(_CGM_FILE)
    existing_timestamps = {r["timestamp"] for r in existing}

    total = len(readings)
    imported = 0
    skipped = 0
    duplicates = 0

    for reading in readings:
        if reading["timestamp"] in existing_timestamps:
            duplicates += 1
            continue

        # Basic range guard before calling save (which does full validation)
        if reading["glucose_mgdl"] < 20 or reading["glucose_mgdl"] > 600:
            skipped += 1
            continue

        try:
            save_cgm_reading(reading)
            existing_timestamps.add(reading["timestamp"])
            imported += 1
        except ValueError:
            skipped += 1

    return {"total": total, "imported": imported, "skipped": skipped, "duplicates": duplicates}


def main():
    parser = argparse.ArgumentParser(description="Import Dexcom Clarity CSV into CGM store")
    parser.add_argument("--file", required=True, help="Path to Dexcom Clarity CSV export")
    args = parser.parse_args()

    filepath = args.file
    if not Path(filepath).exists():
        print(f"ERROR: File not found: {filepath}")
        sys.exit(1)

    print(f"Parsing {filepath} ...")
    readings = parse_dexcom_csv(filepath)
    print(f"Parsed {len(readings)} readings from CSV")

    summary = import_to_store(readings)
    print(
        f"\nImport Summary\n"
        f"  Total rows parsed : {summary['total']}\n"
        f"  Imported          : {summary['imported']}\n"
        f"  Skipped (invalid) : {summary['skipped']}\n"
        f"  Duplicates        : {summary['duplicates']}"
    )


if __name__ == "__main__":
    main()
