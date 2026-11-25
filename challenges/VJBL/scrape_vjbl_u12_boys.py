#!/usr/bin/env python3
"""Export completed U12 Boys game results and player statistics from PlayHQ.

Install:
    python -m pip install playwright
    python -m playwright install chromium

Run:
    python scrape_vjbl_u12_boys.py "https://www.playhq.com/.../e5908800" --division 10
"""

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


AGE_PATTERN = re.compile(r"\b(?:u\s*12|under\s*12|12\s*boys)\b", re.IGNORECASE)
BOYS_PATTERN = re.compile(r"\bboys\b", re.IGNORECASE)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Scrape completed game results and player statistics for U12 Boys grades."
    )
    parser.add_argument("competition_url", help="URL of the PlayHQ competition grades page")
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path("vjbl_2027_u12_boys_game_results.json"),
        help="Output JSON path (default: %(default)s)",
    )
    parser.add_argument(
        "--division",
        help="Only include a division/pool number, such as 10 (matches Pool 10A, 10B, etc.)",
    )
    parser.add_argument(
        "--round",
        type=int,
        help="Only collect one round number (useful for scraping the currently completed round)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chromium without a window (PlayHQ may block headless requests)",
    )
    return parser.parse_args()


def validate_url(url):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("competition_url must be an absolute http:// or https:// URL")


def find_grade_links(page, division=None):
    try:
        page.wait_for_function(
            """() => [...document.querySelectorAll('a[href]')].some((anchor) => {
                const text = anchor.innerText.trim();
                return /\\b(?:u\\s*12|under\\s*12|12\\s*boys)\\b/i.test(text)
                    && /\\bboys\\b/i.test(text);
            })""",
            timeout=45000,
        )
    except PlaywrightTimeoutError as exc:
        raise RuntimeError(
            "No U12 Boys grade links appeared. Check the URL and that PlayHQ is reachable."
        ) from exc

    links = page.evaluate(
        """() => [...document.querySelectorAll('a[href]')].map((anchor) => {
            const lines = anchor.innerText.trim().split(/\\n+/).map(line => line.trim());
            return {name: lines[0] || '', url: anchor.href, text: anchor.innerText.trim()};
        })"""
    )

    grades = []
    seen_urls = set()
    division_pattern = None
    if division:
        division_pattern = re.compile(
            rf"\b(?:pool|division)\s*{re.escape(division)}[a-z]?\b",
            re.IGNORECASE,
        )
    for link in links:
        if (
            AGE_PATTERN.search(link["text"])
            and BOYS_PATTERN.search(link["text"])
            and link["url"] not in seen_urls
            and (division_pattern is None or division_pattern.search(link["name"]))
        ):
            seen_urls.add(link["url"])
            grades.append({"name": link["name"], "url": link["url"]})

    if not grades:
        scope = f" for Division {division}" if division else ""
        raise RuntimeError(f"No U12 Boys grades were found{scope}.")
    return grades


def parse_stat(value):
    value = value.strip().replace(",", "")
    if value == "-":
        return 0
    if not value:
        return None
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(f"Unexpected non-numeric statistics value: {value!r}") from exc


def find_round_links(page, grade):
    last_error = "the round links did not appear"
    last_timeout = None
    for attempt in range(1, 4):
        try:
            response = page.goto(grade["url"], wait_until="domcontentloaded")
            if response is not None and response.status >= 400:
                last_error = f"PlayHQ returned HTTP {response.status}"
            else:
                try:
                    page.wait_for_function(
                        """() => [...document.querySelectorAll('a[href]')].some((anchor) =>
                            /\\/R\\d+(?:[?#]|$)/.test(anchor.href)
                        )""",
                        timeout=20000,
                    )
                    links = page.evaluate(
                        """() => [...document.querySelectorAll('a[href]')].map(anchor => {
                            const match = anchor.href.match(/\\/R(\\d+)(?:[?#]|$)/);
                            return match ? {number: Number(match[1]), url: anchor.href} : null;
                        }).filter(Boolean)"""
                    )
                    rounds = {link["number"]: link for link in links}
                    if rounds:
                        return [rounds[number] for number in sorted(rounds)]
                    last_error = "no round links were found"
                except PlaywrightTimeoutError as exc:
                    last_timeout = exc
        except PlaywrightTimeoutError as exc:
            last_timeout = exc
            last_error = "grade navigation timed out"

        if attempt < 3:
            print(
                f"  {last_error}; retrying ({attempt}/3).",
                file=sys.stderr,
                flush=True,
            )
            page.wait_for_timeout(attempt * 2000)

    error = RuntimeError(f"No fixture rounds loaded for {grade['name']}: {last_error}.")
    if last_timeout is not None:
        raise error from last_timeout
    raise error


def read_round_games(page, grade_name, round_number, round_url):
    ready = False
    last_error = "the fixture cards did not appear"
    last_timeout = None
    for attempt in range(1, 4):
        try:
            current_url = page.url.split("?", 1)[0].rstrip("/")
            target_url = round_url.split("?", 1)[0].rstrip("/")
            response = None
            if current_url != target_url or attempt > 1:
                response = page.goto(round_url, wait_until="domcontentloaded")
            if response is not None and response.status >= 400:
                last_error = f"PlayHQ returned HTTP {response.status}"
            else:
                try:
                    page.wait_for_function(
                        f"""() => {{
                            const body = document.body.innerText;
                            return document.querySelectorAll(
                                'a[aria-label^="View game details for"][href*="/game-centre/"]'
                            ).length > 0
                                || (body.includes("Round {round_number}")
                                    && /\\b(?:FINAL|UPCOMING|POSTPONED|CANCELLED|BYE)\\b/i.test(body));
                        }}""",
                        timeout=20000,
                    )
                    ready = True
                    break
                except PlaywrightTimeoutError as exc:
                    last_timeout = exc
        except PlaywrightTimeoutError as exc:
            last_timeout = exc
            last_error = "navigation timed out"

        if attempt < 3:
            print(
                f"  {last_error}; retrying ({attempt}/3).",
                file=sys.stderr,
                flush=True,
            )
            page.wait_for_timeout(attempt * 1500)

    if not ready:
        error = RuntimeError(
            f"No fixture cards loaded for {grade_name}, Round {round_number}: "
            f"{last_error} ({round_url})"
        )
        if last_timeout is not None:
            raise error from last_timeout
        raise error

    cards = page.evaluate(
        """() => [...document.querySelectorAll(
            'a[aria-label^="View game details for"][href*="/game-centre/"]'
        )].map(anchor => ({
            url: anchor.href,
            label: anchor.getAttribute('aria-label') || '',
            summary: anchor.parentElement.innerText.trim().replace(/\\s+/g, ' ')
        }))"""
    )
    if not cards:
        page_body = page.locator("body").inner_text()
        if re.search(r"\bFINAL\b", page_body, re.IGNORECASE):
            raise RuntimeError(
                f"Completed fixtures have no game detail links for {grade_name}, "
                f"Round {round_number}."
            )
        return []

    games = []
    seen_urls = set()
    for card in cards:
        if card["url"] in seen_urls:
            continue
        seen_urls.add(card["url"])
        if re.search(r"\bFINAL\b", card["summary"], re.IGNORECASE):
            games.append(card)
    return games


def read_game_details(page, grade_name, round_number, game):
    page.wait_for_function(
        """() => {
            const tables = [...document.querySelectorAll('table')];
            const hasPeriodScores = tables.some(table =>
                [...table.querySelectorAll('th')].some(cell =>
                    cell.innerText.trim().toUpperCase() === 'BY PERIOD'
                )
            );
            const playerTables = tables.filter(table => {
                const headers = [...table.querySelectorAll('th')]
                    .map(cell => cell.innerText.trim().toUpperCase());
                return headers.includes('TP') && headers.includes('F')
                    && headers.some(header => header === 'PLAYER' || header === 'PLAYERS');
            });
            const lines = document.body.innerText.split(/\\n+/).map(line => line.trim());
            const finalIndex = lines.findIndex(line => line.toUpperCase() === 'FINAL');
            const hasFinalScoreboard = finalIndex >= 0
                && lines.slice(finalIndex + 1).filter(line => /^\\d+$/.test(line)).length >= 2;
            return (hasPeriodScores && playerTables.length >= 2) || hasFinalScoreboard;
        }""",
        timeout=30000,
    )

    data = page.evaluate(
        """() => ({
            title: document.title,
            body: document.body.innerText,
            tables: [...document.querySelectorAll('table')].map(table =>
                [...table.querySelectorAll('tr')].map(row =>
                    [...row.children].map(cell => ({
                        tag: cell.tagName,
                        text: cell.innerText.trim()
                    }))
                )
            )
        })"""
    )

    period_table = None
    player_tables = []
    for table in data["tables"]:
        if not table:
            continue
        headers = [cell["text"].strip().upper() for cell in table[0]]
        if headers and headers[0] == "BY PERIOD":
            period_table = (headers, table[1:])
        elif (
            "TP" in headers
            and "F" in headers
            and any(header in {"PLAYER", "PLAYERS"} for header in headers)
        ):
            player_tables.append((headers, table[1:]))

    if len(player_tables) == 1:
        raise RuntimeError(
            f"Unexpected game statistics layout for {grade_name}, "
            f"Round {round_number}: {game['url']}"
        )

    body_lines = [line.strip() for line in data["body"].splitlines() if line.strip()]
    final_index = next(
        (index for index, line in enumerate(body_lines) if line.upper() == "FINAL"),
        None,
    )
    final_scores = []
    if final_index is not None:
        for line in body_lines[final_index + 1:]:
            if re.fullmatch(r"\d+", line):
                final_scores.append(int(line))
                if len(final_scores) == 2:
                    break
    label = re.sub(r"^View game details for\s+", "", game.get("label", ""), flags=re.IGNORECASE)
    final_team_names = re.split(r"\s+v\s+", label, maxsplit=1, flags=re.IGNORECASE)
    if len(final_scores) != 2:
        final_scores = []
    if len(final_team_names) != 2:
        final_team_names = []

    teams = []
    if period_table is not None:
        period_headers, period_rows = period_table
        for row in period_rows:
            if not row or row[0]["tag"] != "TH":
                continue
            team_name = row[0]["text"]
            values = [cell["text"] for cell in row[1:] if cell["tag"] == "TD"]
            periods = dict(zip(period_headers[1:], values))
            total = periods.pop("TOTAL", None)
            if not team_name:
                continue
            teams.append(
                {
                    "name": team_name,
                    "score": parse_stat(total) if total and total != "-" else None,
                    "period_scores": {
                        name: None if value == "-" else parse_stat(value)
                        for name, value in periods.items()
                        if name != "T"
                    },
                }
            )
    elif len(final_team_names) == 2 and len(final_scores) == 2:
        teams = [
            {"name": name, "score": score, "period_scores": {}}
            for name, score in zip(final_team_names, final_scores)
        ]
    if len(teams) != 2:
        raise RuntimeError(
            f"Could not parse both teams' scores for {grade_name}, "
            f"Round {round_number}: {game['url']}"
        )
    if any(team["score"] is None for team in teams):
        if len(final_scores) != 2:
            raise RuntimeError(
                f"Could not read the final scoreboard for {grade_name}, "
                f"Round {round_number}: {game['url']}"
            )
        for index, team in enumerate(teams):
            if team["score"] is None:
                team["score"] = final_scores[index]

    player_stats = []
    for team_index, team in enumerate(teams):
        players = []
        team_stats = {"points": 0, "fouls": 0}
        lineup_status = None
        if player_tables:
            headers, rows = player_tables[team_index]
            jersey_index = headers.index("#")
            player_index = next(
                index for index, header in enumerate(headers) if header in {"PLAYER", "PLAYERS"}
            )
            points_index = headers.index("TP")
            fouls_index = headers.index("F")
            for row in rows:
                if not row:
                    continue
                if re.fullmatch(
                    r"No players allocated to line-up", row[0]["text"], re.IGNORECASE
                ):
                    lineup_status = row[0]["text"]
                    continue
                if row[0]["text"].strip().upper() == "TEAM STATS":
                    values = [cell["text"] for cell in row[1:]]
                    if len(values) != 2:
                        raise RuntimeError(
                            f"Unexpected team statistics row in {grade_name}, "
                            f"Round {round_number}: {values}"
                        )
                    team_stats = {
                        "points": parse_stat(values[0]),
                        "fouls": parse_stat(values[1]),
                    }
                    continue
                if row[0]["tag"] != "TD":
                    continue
                values = [cell["text"] for cell in row]
                if len(values) <= max(jersey_index, player_index, points_index, fouls_index):
                    raise RuntimeError(
                        f"Unexpected player row in {grade_name}, Round {round_number}: {values}"
                    )
                if not values[player_index]:
                    continue
                players.append(
                    {
                        "jersey_number": parse_stat(values[jersey_index]),
                        "name": values[player_index],
                        "points": parse_stat(values[points_index]),
                        "fouls": parse_stat(values[fouls_index]),
                    }
                )
        else:
            lineup_status = "Player statistics are not available for this game."
        player_stats.append(
            {
                "team": team["name"],
                "players": players,
                "team_stats": team_stats,
                "lineup_status": lineup_status,
            }
        )

    for team, stats in zip(teams, player_stats):
        stats["player_points_total"] = sum(player["points"] or 0 for player in stats["players"])
        stats["team_points_total"] = stats["player_points_total"] + stats["team_stats"]["points"]
        stats["points_match_score"] = stats["team_points_total"] == team["score"]

    game_id_match = re.search(r"/game-centre/([^/?#]+)", game["url"])
    return {
        "game_id": game_id_match.group(1) if game_id_match else None,
        "url": game["url"],
        "round": round_number,
        "status": "FINAL",
        "title": data["title"],
        "fixture_summary": game["summary"],
        "teams": teams,
        "player_statistics": player_stats,
    }


def load_game_details(page, grade_name, round_number, game):
    last_error = "player statistics did not load"
    last_timeout = None

    for attempt in range(1, 4):
        response_status = None
        try:
            response = page.goto(game["url"], wait_until="domcontentloaded")
            if response is not None and response.status >= 400:
                response_status = response.status
                last_error = f"PlayHQ returned HTTP {response.status}"
            else:
                try:
                    return read_game_details(page, grade_name, round_number, game)
                except PlaywrightTimeoutError as exc:
                    last_timeout = exc
                    last_error = "the game statistics tables did not appear"
        except PlaywrightTimeoutError as exc:
            last_timeout = exc
            last_error = "navigation timed out"

        if attempt < 3:
            print(
                f"  {last_error}; retrying ({attempt}/3).",
                file=sys.stderr,
                flush=True,
            )
            retry_delay = attempt * 5000 if response_status == 403 else attempt * 1000
            page.wait_for_timeout(retry_delay)

    error = RuntimeError(
        f"Could not load game statistics for {grade_name}, Round {round_number}: "
        f"{last_error} ({game['url']})"
    )
    if last_timeout is not None:
        raise error from last_timeout
    raise error


def main():
    args = parse_args()
    validate_url(args.competition_url)

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
            grades = find_grade_links(page, args.division)
            scope = f" in Division {args.division}" if args.division else ""
            print(f"Found {len(grades)} U12 Boys grades{scope}.", file=sys.stderr)

            results = []
            for index, grade in enumerate(grades, start=1):
                print(
                    f"[{index}/{len(grades)}] {grade['name']}",
                    file=sys.stderr,
                    flush=True,
                )
                round_results = []
                rounds = find_round_links(page, grade)
                if args.round is not None:
                    rounds = [
                        round_info
                        for round_info in rounds
                        if round_info["number"] == args.round
                    ]
                    if not rounds:
                        raise RuntimeError(
                            f"Round {args.round} was not found for {grade['name']}."
                        )
                for round_info in rounds:
                    number = round_info["number"]
                    print(f"  Round {number}", file=sys.stderr, flush=True)
                    completed_game_cards = read_round_games(
                        page,
                        grade["name"],
                        number,
                        round_info["url"],
                    )
                    completed_games = []
                    for game_index, game in enumerate(completed_game_cards, start=1):
                        print(
                            f"    Game {game_index}/{len(completed_game_cards)}: {game['label']}",
                            file=sys.stderr,
                            flush=True,
                        )
                        completed_games.append(
                            load_game_details(page, grade["name"], number, game)
                        )
                        page.wait_for_timeout(400)
                    round_results.append(
                        {
                            "round": number,
                            "round_url": round_info["url"],
                            "completed_game_count": len(completed_game_cards),
                            "completed_games": completed_games,
                        }
                    )
                results.append(
                    {
                        "grade": grade["name"],
                        "url": grade["url"],
                        "rounds": round_results,
                    }
                )
                page.wait_for_timeout(400)
        finally:
            browser.close()

    output = {
        "competition_url": args.competition_url,
        "scraped_at": datetime.now(timezone.utc).isoformat(),
        "grades": results,
    }
    args.output.write_text(
        json.dumps(output, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    games = [
        game
        for grade in results
        for round_result in grade["rounds"]
        for game in round_result["completed_games"]
    ]
    player_records = sum(
        len(team["players"])
        for game in games
        for team in game["player_statistics"]
    )
    print(
        f"Saved {len(games)} completed games and {player_records} player records "
        f"across {len(results)} grades to {args.output}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()