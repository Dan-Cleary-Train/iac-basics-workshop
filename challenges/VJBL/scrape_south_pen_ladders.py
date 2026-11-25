#!/usr/bin/env python3
"""Add official PlayHQ ladders to scraped South Pen game data."""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


def parse_args():
    parser = argparse.ArgumentParser(
        description="Scrape official PlayHQ ladders for each grade in a game-data JSON file."
    )
    parser.add_argument("input", type=Path, help="Game data JSON produced by the U12 Boys scraper")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("south_pen_u12_boys_2026.json"),
        help="Output JSON path (default: %(default)s)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chromium without a window (PlayHQ may block headless requests)",
    )
    return parser.parse_args()


def scrape_ladder(page, grade):
    ladder_url = grade["url"].split("?", 1)[0].rstrip("/") + "/ladder"
    response = page.goto(ladder_url, wait_until="domcontentloaded")
    if response is not None and response.status >= 400:
        raise RuntimeError(
            f"PlayHQ returned HTTP {response.status} for {grade['grade']} ladder: {ladder_url}"
        )

    try:
        page.wait_for_function(
            """() => {
                const tables = [...document.querySelectorAll('table')];
                const hasTeams = tables.some(table => {
                    const headers = [...table.querySelectorAll('th')]
                        .map(cell => cell.innerText.trim().toUpperCase());
                    return headers.includes('TEAM') && headers.includes('#');
                });
                const hasStats = tables.some(table => {
                    const headers = [...table.querySelectorAll('th')]
                        .map(cell => cell.innerText.trim().toUpperCase());
                    return ['P', 'PTS', '%', 'W', 'L', 'D'].every(header =>
                        headers.includes(header)
                    );
                });
                return hasTeams && hasStats;
            }""",
            timeout=30000,
        )
    except PlaywrightTimeoutError as exc:
        raise RuntimeError(
            f"Official ladder tables did not load for {grade['grade']}: {ladder_url}"
        ) from exc

    data = page.evaluate(
        """() => ({
            poolNames: [...document.body.innerText.matchAll(/\\bPool\\s+([A-Z0-9]+)\\b/gi)]
                .map(match => `Pool ${match[1]}`),
            tables: [...document.querySelectorAll('table')].map(table =>
                [...table.querySelectorAll('tr')].map(row =>
                    [...row.children].map(cell => cell.innerText.trim())
                )
            )
        })"""
    )

    team_tables = []
    stats_tables = []
    for rows in data["tables"]:
        if not rows:
            continue
        headers = [cell.strip().upper() for cell in rows[0]]
        if "#" in headers and "TEAM" in headers:
            team_tables.append(rows)
        elif all(header in headers for header in ("P", "PTS", "%", "W", "L", "D")):
            stats_tables.append(rows)

    if not team_tables or len(team_tables) != len(stats_tables):
        raise RuntimeError(
            f"Unexpected ladder table layout for {grade['grade']} at {ladder_url}: "
            f"{len(team_tables)} team tables and {len(stats_tables)} statistics tables."
        )
    if data["poolNames"] and len(data["poolNames"]) != len(team_tables):
        raise RuntimeError(
            f"Could not match pool labels to ladder tables for {grade['grade']}: "
            f"{len(data['poolNames'])} labels and {len(team_tables)} pools."
        )

    groups = []
    for index, (team_rows, stats_rows) in enumerate(zip(team_tables, stats_tables)):
        teams = [
            [value for value in row if value]
            for row in team_rows[1:]
            if len([value for value in row if value]) >= 2
        ]
        stats = [
            [value for value in row if value]
            for row in stats_rows[1:]
            if len([value for value in row if value]) >= 6
        ]
        if len(teams) != len(stats):
            raise RuntimeError(
                f"Ladder row count mismatch for {grade['grade']} group {index + 1}: "
                f"{len(teams)} teams and {len(stats)} statistics rows."
            )

        ladder_teams = []
        for team_row, stat_row in zip(teams, stats):
            try:
                ladder_teams.append(
                    {
                        "position": int(team_row[0]),
                        "team": team_row[1],
                        "games_played": int(stat_row[0]),
                        "premiership_points": int(stat_row[1]),
                        "percentage": float(stat_row[2].replace(",", "")),
                        "wins": int(stat_row[3]),
                        "losses": int(stat_row[4]),
                        "draws": int(stat_row[5]),
                        "byes": int(stat_row[6]) if len(stat_row) > 6 else 0,
                    }
                )
            except (ValueError, IndexError) as exc:
                raise RuntimeError(
                    f"Unexpected official ladder values for {grade['grade']}: "
                    f"team={team_row}, stats={stat_row}"
                ) from exc

        group_name = (
            data["poolNames"][index]
            if data["poolNames"]
            else f"Pool {index + 1}" if len(team_tables) > 1 else "Ladder"
        )
        groups.append({"name": group_name, "teams": ladder_teams})

    return {"url": ladder_url, "groups": groups}


def main():
    args = parse_args()
    data = json.loads(args.input.read_text(encoding="utf-8"))
    grades = data.get("grades")
    if data.get("complete") is not True:
        raise ValueError(f"Game scraping is incomplete in {args.input}.")
    if not isinstance(grades, list) or len(grades) != 14:
        raise ValueError(f"Expected 14 U12 Boys grades in {args.input}.")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=args.headless)
        try:
            page = browser.new_page()
            page.set_default_navigation_timeout(60000)
            for index, grade in enumerate(grades, start=1):
                print(
                    f"[{index}/{len(grades)}] {grade['grade']} ladder",
                    file=sys.stderr,
                    flush=True,
                )
                grade["ladder"] = scrape_ladder(page, grade)
                page.wait_for_timeout(1000)
        finally:
            browser.close()

    data["ladder_scraped_at"] = datetime.now(timezone.utc).isoformat()
    args.output.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    group_count = sum(len(grade["ladder"]["groups"]) for grade in grades)
    team_count = sum(
        len(group["teams"])
        for grade in grades
        for group in grade["ladder"]["groups"]
    )
    print(
        f"Wrote official ladders for {len(grades)} grades, "
        f"{group_count} pools, and {team_count} teams to {args.output}.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
