#!/usr/bin/env python3
"""Export players listed in Kilsyth's U9-U11 Boys PlayHQ statistics."""

import argparse
import csv
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


DEFAULT_COMPETITION_URL = (
    "https://www.playhq.com/basketball-victoria/org/kilsyth-basketball/"
    "junior-domestic-summer-202627/5e26f10f"
)
AGE_PATTERN = re.compile(r"\bU\s*0?(9|10|11|12)\b", re.IGNORECASE)
BOYS_PATTERN = re.compile(r"\bboys\b", re.IGNORECASE)
CSV_FIELDS = (
    "player_name",
    "team",
    "age_group",
    "competition",
    "grade",
    "grade_url",
    "scraped_at",
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Export player names from Kilsyth U9-U11 Boys statistics tables."
    )
    parser.add_argument(
        "--competition-url",
        default=DEFAULT_COMPETITION_URL,
        help="PlayHQ competition grades page (default: Kilsyth Summer 2026/27)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=Path.home() / "Documents" / "kilsyth_u9_u11_boys.csv",
        help="CSV output path (default: %(default)s)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chromium without a window; PlayHQ may block headless requests.",
    )
    return parser.parse_args()


def find_grade_links(page, competition_url, age_groups=("U9", "U10", "U11")):
    allowed_age_groups = {age_group.upper() for age_group in age_groups}
    if not allowed_age_groups:
        raise ValueError("At least one age group must be selected.")

    try:
        page.wait_for_function(
            """() => [...document.querySelectorAll('a[href]')].some(anchor =>
                /\\bU\\s*0?(9|10|11|12)\\b/i.test(anchor.innerText)
                    && /\\bBoys\\b/i.test(anchor.innerText)
            )""",
            timeout=45000,
        )
    except PlaywrightTimeoutError as exc:
        raise RuntimeError(
            "No matching Boys grade links appeared; check the competition URL."
        ) from exc

    links = page.evaluate(
        """() => [...document.querySelectorAll('a[href]')].map(anchor => ({
            name: anchor.innerText.trim().split(/\\n+/)[0] || '',
            text: anchor.innerText.trim(),
            url: anchor.href
        }))"""
    )
    competition_path = urlparse(competition_url).path.rsplit("/", 1)[0].rstrip("/")
    grades = []
    seen_urls = set()
    for link in links:
        age_match = AGE_PATTERN.search(link["text"])
        age_group = f"U{age_match.group(1)}" if age_match else ""
        if (
            age_match
            and age_group in allowed_age_groups
            and BOYS_PATTERN.search(link["text"])
            and not re.search(r"\bgirls\b", link["text"], re.IGNORECASE)
            and urlparse(link["url"]).path.startswith(competition_path + "/")
            and link["url"] not in seen_urls
        ):
            seen_urls.add(link["url"])
            grades.append(
                {
                    "grade": link["name"],
                    "age_group": age_group,
                    "url": link["url"],
                }
            )

    if not grades:
        expected_ages = ", ".join(sorted(allowed_age_groups))
        raise RuntimeError(
            f"No Boys grades for {expected_ages} were found."
        )
    return grades


def read_statistics_rows(page, grade, allow_empty=False):
    try:
        page.wait_for_function(
            """allowEmpty => (allowEmpty
                && document.body.innerText.includes(
                    'There are currently no statistics available for this grade'
                )) || [...document.querySelectorAll('table')].some(table => {
                    const rows = [...table.querySelectorAll('tr')];
                    return rows.length > 1
                        && rows[0].innerText.toUpperCase().includes('PLAYER')
                        && rows[0].innerText.toUpperCase().includes('TEAM');
                })""",
            arg=allow_empty,
            timeout=30000,
        )
    except PlaywrightTimeoutError as exc:
        raise RuntimeError(
            f"Player statistics table did not load for {grade['grade']}: {page.url}"
        ) from exc

    if allow_empty and (
        "There are currently no statistics available for this grade"
        in page.locator("body").inner_text()
    ):
        return []

    tables = page.evaluate(
        """() => [...document.querySelectorAll('table')].map(table =>
            [...table.querySelectorAll('tr')].map(row =>
                [...row.children].map(cell => cell.innerText.trim())
            )
        )"""
    )
    table = next(
        (
            rows
            for rows in tables
            if rows
            and "PLAYER" in [cell.strip().upper() for cell in rows[0]]
            and "TEAM" in [cell.strip().upper() for cell in rows[0]]
        ),
        None,
    )
    if table is None:
        raise RuntimeError(f"Unexpected statistics table for {grade['grade']}.")

    headers = [cell.strip().upper() for cell in table[0]]
    player_index = headers.index("PLAYER")
    team_index = headers.index("TEAM")
    stat_columns = ("GP", "TP")
    has_stat_columns = [column in headers for column in stat_columns]
    if any(has_stat_columns) and not all(has_stat_columns):
        raise RuntimeError(
            f"Incomplete GP/TP columns in {grade['grade']} statistics."
        )
    stat_indices = {
        column: headers.index(column) for column in stat_columns
    } if all(has_stat_columns) else {}
    records = []
    for row_number, row in enumerate(table[1:], start=2):
        required_indices = [player_index, team_index, *stat_indices.values()]
        if len(row) <= max(required_indices):
            raise RuntimeError(
                f"Unexpected row {row_number} in {grade['grade']} statistics: {row}"
            )
        player_name = row[player_index].strip()
        team = row[team_index].strip()
        if not player_name or not team:
            raise RuntimeError(
                f"Missing player or team in {grade['grade']} statistics row "
                f"{row_number}: {row}"
            )
        record = {"player_name": player_name, "team": team}
        if stat_indices:
            record["games_played"] = parse_stat(
                row[stat_indices["GP"]],
                grade["grade"],
                "GP",
            )
            record["total_points"] = parse_stat(
                row[stat_indices["TP"]],
                grade["grade"],
                "TP",
            )
        records.append(record)
    return records


def parse_stat(value, grade, column):
    value = value.strip().replace(",", "")
    if value == "-":
        return 0
    try:
        return int(value)
    except ValueError as exc:
        raise ValueError(
            f"Unexpected {column} value for {grade}: {value!r}"
        ) from exc


def scrape_grade(page, grade, allow_empty=False):
    statistics_url = grade["url"].rstrip("/") + "/statistics?source=grade-tab"
    records = None
    for attempt in range(1, 4):
        try:
            response = page.goto(statistics_url, wait_until="domcontentloaded")
            if response is not None and response.status >= 400:
                raise RuntimeError(
                    f"PlayHQ returned HTTP {response.status} for {grade['grade']}: "
                    f"{statistics_url}"
                )
            records = read_statistics_rows(page, grade, allow_empty=allow_empty)
            break
        except (PlaywrightTimeoutError, RuntimeError) as exc:
            if attempt == 3:
                raise RuntimeError(
                    f"Could not load statistics for {grade['grade']} after "
                    f"{attempt} attempts: {exc}"
                ) from exc
            print(
                f"  Could not load {grade['grade']} statistics; "
                f"retrying ({attempt}/3).",
                file=sys.stderr,
                flush=True,
            )
            page.wait_for_timeout(attempt * 2000)

    if records is None:
        raise RuntimeError(f"No player rows were loaded for {grade['grade']}.")
    if not records:
        if allow_empty and (
            "There are currently no statistics available for this grade"
            in page.locator("body").inner_text()
        ):
            return []
        raise RuntimeError(f"No player rows were listed for {grade['grade']}.")

    body = page.locator("body").inner_text()
    footer = re.search(r"Showing\s+(\d+)\s*-\s*(\d+)\s+of\s+(\d+)", body)
    if not footer:
        raise RuntimeError(f"Could not determine the statistics row count for {grade['grade']}.")
    first_row, last_row, total_rows = map(int, footer.groups())
    page_size = last_row - first_row + 1
    if page_size <= 0:
        raise RuntimeError(f"Invalid statistics page size for {grade['grade']}.")
    page_count = math.ceil(total_rows / page_size)

    for page_number in range(2, page_count + 1):
        expected_first_rank = (page_number - 1) * page_size + 1
        page_link = page.locator(
            f'a[data-testid="page-{page_number}"]:visible'
        ).last
        if page_link.count() == 0:
            raise RuntimeError(
                f"Statistics page {page_number} is missing for {grade['grade']}."
            )
        target_url = page_link.evaluate("(link) => link.href")
        for attempt in range(1, 4):
            try:
                if attempt == 1:
                    page_link.click(timeout=30000)
                else:
                    response = page.goto(target_url, wait_until="domcontentloaded")
                    if response is not None and response.status >= 400:
                        raise RuntimeError(
                            f"PlayHQ returned HTTP {response.status} for "
                            f"{grade['grade']} statistics page {page_number}."
                        )
                page.wait_for_url(
                    lambda current_url: parse_qs(urlparse(current_url).query).get(
                        "page"
                    )
                    == [str(page_number)],
                    timeout=30000,
                )
                page.wait_for_function(
                    """expectedRank => {
                        const table = [...document.querySelectorAll('table')].find(candidate =>
                            [...candidate.querySelectorAll('tr:first-child th, tr:first-child td')]
                                .some(cell => cell.innerText.trim().toUpperCase() === 'PLAYER')
                        );
                        if (!table) return false;
                        const row = table.querySelectorAll('tr')[1];
                        return row
                            && Number(row.children[0].innerText.trim()) === expectedRank;
                    }""",
                    arg=expected_first_rank,
                    timeout=30000,
                )
                break
            except (PlaywrightTimeoutError, RuntimeError) as exc:
                if attempt == 3:
                    raise RuntimeError(
                        f"Statistics page {page_number} did not load for "
                        f"{grade['grade']} after {attempt} attempts: {exc}"
                    ) from exc
                print(
                    f"  Could not load page {page_number} of {grade['grade']} "
                    f"statistics; retrying ({attempt}/3).",
                    file=sys.stderr,
                    flush=True,
                )
                page.wait_for_timeout(attempt * 1000)
        records.extend(read_statistics_rows(page, grade))

    if len(records) != total_rows:
        raise RuntimeError(
            f"Scraped {len(records)} of {total_rows} statistics rows for {grade['grade']}."
        )

    return records


def main():
    args = parse_args()
    competition_url = args.competition_url

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=args.headless)
        try:
            page = browser.new_page()
            page.set_default_navigation_timeout(60000)
            response = page.goto(competition_url, wait_until="domcontentloaded")
            if response is not None and response.status >= 400:
                raise RuntimeError(
                    f"PlayHQ returned HTTP {response.status} for {competition_url}"
                )
            title = page.title().split(" | PlayHQ", 1)[0].strip()
            grades = find_grade_links(page, competition_url)

            rows = []
            seen = set()
            for index, grade in enumerate(grades, start=1):
                print(
                    f"[{index}/{len(grades)}] {grade['grade']}",
                    file=sys.stderr,
                    flush=True,
                )
                for player in scrape_grade(page, grade):
                    key = (
                        grade["grade"].casefold(),
                        player["team"].casefold(),
                        player["player_name"].casefold(),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    rows.append(
                        {
                            "player_name": player["player_name"],
                            "team": player["team"],
                            "age_group": grade["age_group"],
                            "competition": title,
                            "grade": grade["grade"],
                            "grade_url": grade["url"],
                        }
                    )
                page.wait_for_timeout(500)
        finally:
            browser.close()

    scraped_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows.sort(
        key=lambda row: (
            int(row["age_group"][1:]),
            row["grade"].casefold(),
            row["team"].casefold(),
            row["player_name"].casefold(),
        )
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", newline="", encoding="utf-8-sig") as output_file:
        writer = csv.DictWriter(output_file, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({**row, "scraped_at": scraped_at})

    grade_counts = {}
    for row in rows:
        grade_counts[row["age_group"]] = grade_counts.get(row["age_group"], 0) + 1
    print(
        f"Wrote {len(rows)} player-grade rows across {len(grades)} grades "
        f"({', '.join(f'{age}: {count}' for age, count in sorted(grade_counts.items()))}) "
        f"to {args.output}.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
