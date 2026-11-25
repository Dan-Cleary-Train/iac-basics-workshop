#!/usr/bin/env python3
"""Embed the South Pen game and ladder snapshot in the standalone HTML report."""

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DATA = Path(__file__).resolve().with_name("south_pen_u12_boys_2026.json")
DEFAULT_REPORT = ROOT / "VJBL_U12_v1.2.html"
DATA_SCRIPT_PATTERN = re.compile(
    r'<script id="south-pen-data" type="application/json">.*?</script>',
    re.DOTALL,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Embed the South Pen JSON snapshot in the VJBL U12 v1.2 report."
    )
    parser.add_argument(
        "data",
        nargs="?",
        type=Path,
        default=DEFAULT_DATA,
        help=f"South Pen data JSON (default: {DEFAULT_DATA})",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
        help=f"HTML report path (default: {DEFAULT_REPORT})",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    data = json.loads(args.data.read_text(encoding="utf-8"))
    grades = data.get("grades")
    if data.get("complete") is not True:
        raise ValueError(f"South Pen scraping is incomplete in {args.data}.")
    if not isinstance(grades, list) or len(grades) != 14:
        raise ValueError(f"Expected 14 U12 Boys grades in {args.data}.")
    if any("ladder" not in grade for grade in grades):
        raise ValueError("Every South Pen grade must include an official ladder.")

    report = args.report.read_text(encoding="utf-8")
    if len(DATA_SCRIPT_PATTERN.findall(report)) != 1:
        raise ValueError(
            f"Expected exactly one south-pen-data script in {args.report}."
        )

    serialized = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    serialized = serialized.replace("<", "\\u003c")
    replacement = (
        '<script id="south-pen-data" type="application/json">'
        f"{serialized}</script>"
    )
    args.report.write_text(
        DATA_SCRIPT_PATTERN.sub(lambda _: replacement, report),
        encoding="utf-8",
    )
    print(f"Embedded South Pen data for {len(grades)} grades in {args.report}.")


if __name__ == "__main__":
    main()
