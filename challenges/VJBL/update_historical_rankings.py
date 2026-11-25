#!/usr/bin/env python3
"""Embed normalized season rankings from the historical ladders JSON."""

import argparse
import json
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DATA_OUTPUT = Path(__file__).resolve().parent / "historical_rankings.json"
REPORT = ROOT / "VJBL_U12_v1.2.html"
DATA_SCRIPT_PATTERN = re.compile(
    r'(<script id="historical-ranking-data" type="application/json">).*?(</script>)',
    re.DOTALL,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Refresh the integrated historical rankings in the VJBL report."
    )
    parser.add_argument(
        "source",
        type=Path,
        help="Path to vjbl_u12_boys_ladders.json from the VJBL Rankings project",
    )
    return parser.parse_args()


def division_sort_key(division):
    if division == "VC":
        return (0, 0, division)
    if division == "VC Reserve":
        return (1, 0, division)
    for prefix, order in (("VJL", 2), ("RDL", 3), ("RL", 4)):
        match = re.fullmatch(rf"{prefix}(\d+)", division, re.IGNORECASE)
        if match:
            return (order, int(match.group(1)), division)
    return (5, 0, division.casefold())


def normalize_seasons(source):
    seasons = source.get("seasons")
    if not seasons:
        raise ValueError("The source JSON does not contain any seasons.")

    normalized = []
    for season in sorted(seasons, key=lambda item: int(item["year"])):
        ranked_divisions = season.get("ranked_divisions")
        if not ranked_divisions:
            raise ValueError(f"No ranked divisions found for season {season.get('season')}.")

        teams = []
        overall_rank = 0
        for division in sorted(ranked_divisions, key=division_sort_key):
            for division_rank, row in enumerate(ranked_divisions[division], start=1):
                overall_rank += 1
                teams.append({
                    "team": row["team"],
                    "division": division,
                    "competition": row.get("competition", division),
                    "division_rank": division_rank,
                    "overall_rank": overall_rank,
                })
        normalized.append({
            "year": int(season["year"]),
            "season": season["season"],
            "teams": teams,
        })
    return {"seasons": normalized}


def main():
    args = parse_args()
    source = json.loads(args.source.read_text(encoding="utf-8"))
    normalized = normalize_seasons(source)
    compact_json = json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))

    report = REPORT.read_text(encoding="utf-8")
    updated_report, replacements = DATA_SCRIPT_PATTERN.subn(
        lambda match: f"{match.group(1)}{compact_json}{match.group(2)}",
        report,
    )
    if replacements != 1:
        raise RuntimeError(
            f"Expected one historical ranking data script in {REPORT}, found {replacements}."
        )

    DATA_OUTPUT.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    REPORT.write_text(updated_report, encoding="utf-8")
    counts = ", ".join(
        f"{season['year']}: {len(season['teams'])} teams"
        for season in normalized["seasons"]
    )
    print(f"Updated {REPORT} and {DATA_OUTPUT} ({counts}).")


if __name__ == "__main__":
    main()
