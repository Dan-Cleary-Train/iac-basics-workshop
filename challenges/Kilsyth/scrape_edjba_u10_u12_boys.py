#!/usr/bin/env python3
"""Save EDJBA Boys player statistics from PlayHQ as JSON."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from scrape_kilsyth_u9_u11_boys import find_grade_links, scrape_grade


DEFAULT_COMPETITION_URL = (
    "https://www.playhq.com/basketball-victoria/org/"
    "eastern-districts-junior-basketball-association-edjba/"
    "edjba-summer-202627/3f50cd49"
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Save EDJBA Boys player statistics from PlayHQ as JSON."
    )
    parser.add_argument(
        "--competition-url",
        default=DEFAULT_COMPETITION_URL,
        help="PlayHQ competition grades page (default: EDJBA Summer 2026/27)",
    )
    parser.add_argument(
        "--age-groups",
        nargs="+",
        choices=("U9", "U10", "U11", "U12"),
        default=("U10", "U12"),
        help="Boys age groups to scrape (default: U10 U12)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path.home() / "Downloads" / "edjba_u10_u12_boys_2026_27.json",
        help="JSON output path (default: %(default)s)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chromium without a window; PlayHQ may block headless requests.",
    )
    return parser.parse_args()


def sorted_players(players, grade_name):
    result = []
    for player in players:
        if "games_played" not in player or "total_points" not in player:
            raise RuntimeError(
                f"EDJBA statistics are missing GP or TP for {player['player_name']} "
                f"in {grade_name}."
            )
        games_played = player["games_played"]
        total_points = player["total_points"]
        result.append(
            {
                "name": player["player_name"],
                "team": player["team"],
                "games_played": games_played,
                "total_points": total_points,
                "points_per_game": (
                    round(total_points / games_played, 2)
                    if games_played
                    else None
                ),
            }
        )
    return sorted(
        result,
        key=lambda player: (player["team"].casefold(), player["name"].casefold()),
    )


def main():
    args = parse_args()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=args.headless)
        try:
            page = browser.new_page()
            page.set_default_navigation_timeout(60000)
            response = page.goto(
                args.competition_url,
                wait_until="domcontentloaded",
            )
            if response is not None and response.status >= 400:
                raise RuntimeError(
                    f"PlayHQ returned HTTP {response.status} for "
                    f"{args.competition_url}"
                )
            competition = page.title().split(" | PlayHQ", 1)[0].strip()
            grades = find_grade_links(
                page,
                args.competition_url,
                age_groups=args.age_groups,
            )
            for index, grade in enumerate(grades, start=1):
                print(
                    f"[{index}/{len(grades)}] {grade['grade']}",
                    file=sys.stderr,
                    flush=True,
                )
                grade["statistics_url"] = (
                    grade["url"].rstrip("/") + "/statistics"
                )
                grade["players"] = sorted_players(
                    scrape_grade(page, grade, allow_empty=True),
                    grade["grade"],
                )
                page.wait_for_timeout(500)
        finally:
            browser.close()

    data = {
        "format_version": 1,
        "competition": competition,
        "competition_url": args.competition_url,
        "scope": (
            f"{', '.join(args.age_groups)} Boys"
            + (" (excludes U11)" if set(args.age_groups) == {"U10", "U12"} else "")
        ),
        "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "grades": grades,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    player_count = sum(len(grade["players"]) for grade in grades)
    print(
        f"Wrote {player_count} EDJBA player-stat records across "
        f"{len(grades)} grades to {args.output}.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()