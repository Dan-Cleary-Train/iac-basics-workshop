#!/usr/bin/env python3
"""Export South Pen 2026 U12 Boys games, player statistics, and fixture details."""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from scrape_vjbl_u12_boys import load_game_details


DEFAULT_COMPETITION_URL = (
    "https://www.playhq.com/basketball-victoria/org/southern-peninsula-tournament/"
    "new-balance-representative-tournament-2026/0671b6cf"
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Scrape completed South Pen 2026 U12 Boys games and player statistics."
    )
    parser.add_argument(
        "competition_url",
        nargs="?",
        default=DEFAULT_COMPETITION_URL,
        help="PlayHQ competition grades page",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("south_pen_u12_boys_2026_games.json"),
        help="Output JSON path (default: %(default)s)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chromium without a window (PlayHQ may block headless requests)",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip grades already completed in the output JSON",
    )
    return parser.parse_args()


def find_grades(page):
    try:
        page.wait_for_function(
            """() => [...document.querySelectorAll('a[href]')].some(anchor =>
                /\\/u12-boys-(?:championship|division-\\d+)\\//i.test(
                    new URL(anchor.href).pathname
                )
            )""",
            timeout=45000,
        )
    except PlaywrightTimeoutError as exc:
        raise RuntimeError(
            "No U12 Boys Championship or Division links appeared on the competition page."
        ) from exc

    links = page.evaluate(
        """() => [...document.querySelectorAll('a[href]')].map(anchor => ({
            name: anchor.innerText.trim().split(/\\n+/)[0].trim(),
            url: anchor.href
        }))"""
    )
    grades_by_key = {}
    for link in links:
        path = link["url"].split("?", 1)[0].rstrip("/")
        match = re.search(r"/(u12-boys-(?:championship|division-(\d+)))/[^/]+$", path, re.I)
        if not match:
            continue
        slug = match.group(1).lower()
        if slug == "u12-boys-championship":
            key = (0, "championship")
            grade_name = "U12 Boys Championship"
        else:
            division = int(match.group(2))
            key = (division, f"division-{division}")
            grade_name = f"U12 Boys Division {division}"
        grades_by_key[key] = {"grade": grade_name, "url": path}

    grades = [grades_by_key[key] for key in sorted(grades_by_key)]
    expected_keys = {"championship"} | {f"division-{number}" for number in range(1, 14)}
    actual_keys = {
        grade["grade"].lower().replace("u12 boys ", "").replace(" ", "-")
        for grade in grades
    }
    if actual_keys != expected_keys:
        missing = sorted(expected_keys - actual_keys)
        unexpected = sorted(actual_keys - expected_keys)
        raise RuntimeError(
            "Expected the Championship and Divisions 1-13; "
            f"missing={missing}, unexpected={unexpected}."
        )
    return grades


def click_fixture_scope(page, scope):
    button = page.get_by_role("button", name=scope, exact=True)
    if button.count():
        button.click()
        page.wait_for_timeout(1000)
        return True
    return False


def read_fixture_cards(page, grade_name, scope, required):
    try:
        page.wait_for_function(
            """() => document.querySelectorAll(
                'a[aria-label^="View game details for"][href*="/game-centre/"]'
            ).length > 0""",
            timeout=20000,
        )
    except PlaywrightTimeoutError:
        if required:
            raise RuntimeError(
                f"No game detail links loaded for {grade_name} in the {scope} fixture view."
            )
        return []

    cards = page.evaluate(
        """() => [...document.querySelectorAll(
            'a[aria-label^="View game details for"][href*="/game-centre/"]'
        )].map(anchor => {
            let roundContainer = anchor.parentElement;
            let roundMatch = null;
            while (roundContainer && !roundMatch) {
                roundMatch = roundContainer.innerText.trim().match(
                    /^(Finals\\s+)?Round\\s+(\\d+)\\b/i
                );
                if (!roundMatch) roundContainer = roundContainer.parentElement;
            }
            return {
                url: anchor.href,
                label: anchor.getAttribute('aria-label') || '',
                summary: anchor.parentElement.innerText.trim().replace(/\\s+/g, ' '),
                round: roundMatch ? Number(roundMatch[2]) : null,
                round_label: roundMatch
                    ? `${roundMatch[1] ? 'Finals ' : ''}Round ${roundMatch[2]}`
                    : null
            };
        })"""
    )

    results = []
    seen_urls = set()
    for card in cards:
        if card["url"] in seen_urls or not re.search(r"\bFINAL\b", card["summary"], re.I):
            continue
        seen_urls.add(card["url"])
        if card["round"] is None:
            raise RuntimeError(
                f"Could not identify the round for a game in {grade_name} ({scope}): "
                f"{card['url']}"
            )
        card["scope"] = scope
        results.append(card)
    return results


def scrape_grade(page, grade):
    response = page.goto(grade["url"], wait_until="domcontentloaded")
    if response is not None and response.status >= 400:
        raise RuntimeError(
            f"PlayHQ returned HTTP {response.status} for {grade['grade']}: {grade['url']}"
        )

    try:
        page.wait_for_function(
            """() => [...document.querySelectorAll('h1,h2,h3,h4')].some(heading =>
                /^Round\\s+\\d+/i.test(heading.innerText.trim())
            ) || [...document.querySelectorAll('a[href]')].some(anchor =>
                /\\/game-centre\\//.test(anchor.href)
            )""",
            timeout=30000,
        )
    except PlaywrightTimeoutError as exc:
        raise RuntimeError(f"No fixtures loaded for {grade['grade']}.") from exc

    all_pools_available = click_fixture_scope(page, "All Pools")
    if not all_pools_available:
        raise RuntimeError(f"The All Pools fixture view is missing for {grade['grade']}.")
    cards = read_fixture_cards(page, grade["grade"], "All Pools", required=True)

    finals_available = click_fixture_scope(page, "Finals")
    if finals_available:
        cards.extend(read_fixture_cards(page, grade["grade"], "Finals", required=False))

    unique_cards = []
    seen_urls = set()
    for card in cards:
        if card["url"] not in seen_urls:
            seen_urls.add(card["url"])
            unique_cards.append(card)

    grouped_rounds = {}
    for index, card in enumerate(unique_cards, start=1):
        print(
            f"    Game {index}/{len(unique_cards)}: {card['label']}",
            file=sys.stderr,
            flush=True,
        )
        game = load_game_details(page, grade["grade"], card["round"], card)
        game["round_label"] = card["round_label"]
        game["fixture_scope"] = card["scope"]
        key = card["round_label"]
        if key not in grouped_rounds:
            grouped_rounds[key] = {
                "round": card["round"],
                "round_label": card["round_label"],
                "round_url": grade["url"],
                "completed_games": [],
            }
        grouped_rounds[key]["completed_games"].append(game)
        page.wait_for_timeout(1000)

    rounds = []
    for round_data in grouped_rounds.values():
        round_data["completed_game_count"] = len(round_data["completed_games"])
        rounds.append(round_data)
    return {**grade, "rounds": rounds}


def main():
    args = parse_args()
    results = []
    completed_urls = set()
    if args.resume and args.output.exists():
        existing_data = json.loads(args.output.read_text(encoding="utf-8"))
        if existing_data.get("competition_url") != args.competition_url:
            raise ValueError(
                f"Cannot resume {args.output}: it belongs to a different competition."
            )
        results = existing_data.get("grades", [])
        if not isinstance(results, list):
            raise ValueError(f"Invalid grade list in {args.output}.")
        completed_urls = {grade["url"] for grade in results}

    def save_results(complete):
        data = {
            "competition_url": args.competition_url,
            "competition": "New Balance Representative Tournament, 2026",
            "gender": "Boys",
            "age_group": "U12",
            "complete": complete,
            "scraped_at": datetime.now(timezone.utc).isoformat(),
            "grades": results,
        }
        args.output.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=args.headless)
        try:
            page = browser.new_page()
            page.set_default_navigation_timeout(60000)
            response = page.goto(args.competition_url, wait_until="domcontentloaded")
            if response is not None and response.status >= 400:
                raise RuntimeError(
                    f"PlayHQ returned HTTP {response.status} for {args.competition_url}"
                )
            grades = find_grades(page)
            print(f"Found {len(grades)} South Pen U12 Boys grades.", file=sys.stderr)

            for index, grade in enumerate(grades, start=1):
                if grade["url"] in completed_urls:
                    print(
                        f"[{index}/{len(grades)}] Skipping completed {grade['grade']}.",
                        file=sys.stderr,
                        flush=True,
                    )
                    continue
                print(
                    f"[{index}/{len(grades)}] {grade['grade']}",
                    file=sys.stderr,
                    flush=True,
                )
                results.append(scrape_grade(page, grade))
                save_results(complete=False)
        finally:
            browser.close()

    results.sort(
        key=lambda grade: 0 if grade["grade"].endswith("Championship")
        else int(grade["grade"].rsplit(" ", 1)[1])
    )
    save_results(complete=True)
    game_count = sum(
        len(round_data["completed_games"])
        for grade in results
        for round_data in grade["rounds"]
    )
    player_rows = sum(
        len(team["players"])
        for grade in results
        for round_data in grade["rounds"]
        for game in round_data["completed_games"]
        for team in game["player_statistics"]
    )
    print(
        f"Wrote {game_count} completed games and {player_rows} player rows "
        f"for {len(results)} grades to {args.output}.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
