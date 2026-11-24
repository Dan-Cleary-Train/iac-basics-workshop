#!/usr/bin/env python3
"""Build a local tryout report from Kilsyth, MEBA, and EDJBA PlayHQ stats."""

import argparse
import csv
import json
import math
import posixpath
import re
import sys
import unicodedata
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


DEFAULT_COMPETITION_URL = (
    "https://www.playhq.com/basketball-victoria/org/kilsyth-basketball/"
    "junior-domestic-summer-202627/5e26f10f"
)
DEFAULT_TRYOUTS_COMPETITION_URL = (
    "https://www.playhq.com/basketball-victoria/org/kilsyth-basketball/"
    "junior-domestic-winter-2026/8ff9f39e"
)
MAIN_NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
PACKAGE_REL_NS = "http://schemas.openxmlformats.org/package/2006/relationships"
AGE_PATTERN = re.compile(r"\bU\s*0?(9|10|11|12)\b", re.IGNORECASE)
BOYS_PATTERN = re.compile(r"\bboys\b", re.IGNORECASE)
STATISTICS_FOOTER = re.compile(r"Showing\s+(\d+)\s*-\s*(\d+)\s+of\s+(\d+)")
CSV_FIELDS = ("player_name", "team", "age_group", "grade")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Match competition stats to the Player Analysis source-of-truth list."
    )
    parser.add_argument(
        "--source-of-truth",
        required=True,
        type=Path,
        help="Player Analysis XLSX workbook; reads names and column J from Master",
    )
    parser.add_argument(
        "--scraped-players",
        required=True,
        type=Path,
        help="Kilsyth U9-U11 CSV containing player_name, team, age_group, and grade",
    )
    parser.add_argument(
        "--vjbl-players",
        required=True,
        type=Path,
        help="VJBL U12 Boys player data JSON containing grades and player teams",
    )
    parser.add_argument(
        "--gebc-players",
        required=True,
        type=Path,
        help="GEBC U9-U11 Boys player data JSON containing grades and player teams",
    )
    parser.add_argument(
        "--edjba-players",
        required=True,
        type=Path,
        help="EDJBA U10 and U12 Boys player data JSON containing grades and stats",
    )
    parser.add_argument(
        "--tryouts-kilsyth-players",
        type=Path,
        help="Optional Kilsyth Winter 2026 U9-U11 player CSV for the 2026 Tryouts tab",
    )
    parser.add_argument(
        "--tryouts-gebc-players",
        type=Path,
        help="Optional GEBC Winter 2026 player-stat JSON for the 2026 Tryouts tab",
    )
    parser.add_argument(
        "--tryouts-edjba-players",
        type=Path,
        help="Optional EDJBA Winter 2026 U11 player-stat JSON for the 2026 Tryouts tab",
    )
    parser.add_argument(
        "--tryouts-competition-url",
        default=DEFAULT_TRYOUTS_COMPETITION_URL,
        help="Kilsyth Winter 2026 grades page for the 2026 Tryouts tab",
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
        default=Path.home()
        / "Downloads"
        / "Player Analysis 20261001 - Kilsyth Statistics.html",
        help="HTML output path (default: %(default)s)",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chromium without a window; PlayHQ may block headless requests.",
    )
    return parser.parse_args()


def excel_cell_value(cell, shared_strings):
    value = cell.find(f"{{{MAIN_NS}}}v")
    cell_type = cell.attrib.get("t")
    if cell_type == "inlineStr":
        return "".join(text.text or "" for text in cell.findall(f".//{{{MAIN_NS}}}t"))
    if value is None:
        return ""
    if cell_type == "s":
        return shared_strings[int(value.text)]
    return value.text or ""


def read_source_players(workbook_path):
    if not workbook_path.is_file():
        raise FileNotFoundError(
            f"Player Analysis workbook was not found: {workbook_path}"
        )

    with zipfile.ZipFile(workbook_path) as workbook:
        workbook_xml = ET.fromstring(workbook.read("xl/workbook.xml"))
        relationships_xml = ET.fromstring(
            workbook.read("xl/_rels/workbook.xml.rels")
        )
        relationships = {
            relationship.attrib["Id"]: relationship.attrib["Target"]
            for relationship in relationships_xml.findall(
                f"{{{PACKAGE_REL_NS}}}Relationship"
            )
        }
        shared_strings = []
        if "xl/sharedStrings.xml" in workbook.namelist():
            strings_xml = ET.fromstring(workbook.read("xl/sharedStrings.xml"))
            shared_strings = [
                "".join(text.text or "" for text in item.findall(f".//{{{MAIN_NS}}}t"))
                for item in strings_xml.findall(f"{{{MAIN_NS}}}si")
            ]

        sheet = next(
            (
                item
                for item in workbook_xml.findall(f".//{{{MAIN_NS}}}sheet")
                if item.attrib["name"] == "Master"
            ),
            None,
        )
        if sheet is None:
            raise ValueError(
                f"No 'Master' sheet was found in {workbook_path}."
            )
        target = relationships[sheet.attrib[f"{{{REL_NS}}}id"]]
        sheet_path = (
            target.lstrip("/")
            if target.startswith("/")
            else posixpath.normpath(posixpath.join("xl", target))
        )
        sheet_xml = ET.fromstring(workbook.read(sheet_path))
        rows = sheet_xml.findall(f".//{{{MAIN_NS}}}sheetData/{{{MAIN_NS}}}row")
        if not rows:
            raise ValueError("The Player Analysis Master sheet is empty.")

        def row_values(row):
            values = {}
            for cell in row.findall(f"{{{MAIN_NS}}}c"):
                match = re.match(r"([A-Z]+)", cell.attrib["r"])
                if match:
                    values[match.group(1)] = excel_cell_value(cell, shared_strings)
            return values

        headers = {
            column: value.strip().casefold()
            for column, value in row_values(rows[0]).items()
        }
        required_headers = {
            "number": None,
            "first name": None,
            "last name": None,
            "used group": None,
        }
        for column, header in headers.items():
            if header in required_headers:
                required_headers[header] = column
        missing_headers = [header for header, column in required_headers.items() if not column]
        if missing_headers:
            raise ValueError(
                f"Player Analysis Master sheet is missing columns: "
                f"{', '.join(missing_headers)}"
            )

        players = []
        for row in rows[1:]:
            values = row_values(row)
            first_name = values.get(required_headers["first name"], "").strip()
            last_name = values.get(required_headers["last name"], "").strip()
            if not first_name and not last_name:
                continue
            if not first_name or not last_name:
                raise ValueError(
                    f"Incomplete first or last name on Master row {row.attrib['r']}."
                )
            player_number = values.get(required_headers["number"], "").strip()
            players.append(
                {
                    "source_number": player_number or row.attrib["r"],
                    "name": f"{first_name} {last_name}",
                    "used_group": values.get(required_headers["used group"], "").strip(),
                    "ac_court": values.get("J", "").strip(),
                }
            )

    if not players:
        raise ValueError("No named players were found on the Master sheet.")
    return players


def read_scraped_players(csv_path):
    if not csv_path.is_file():
        raise FileNotFoundError(f"Scraped players CSV was not found: {csv_path}")

    players = []
    with csv_path.open(encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        missing_fields = set(CSV_FIELDS) - set(reader.fieldnames or [])
        if missing_fields:
            raise ValueError(
                f"Scraped CSV is missing required columns: {', '.join(sorted(missing_fields))}"
            )
        for row_number, row in enumerate(reader, start=2):
            player = {field: (row.get(field) or "").strip() for field in CSV_FIELDS}
            if any(not value for value in player.values()):
                raise ValueError(
                    f"Scraped CSV row {row_number} has a missing name, team, age, or grade."
                )
            if player["age_group"] not in {"U9", "U10", "U11"}:
                raise ValueError(
                    f"Unexpected age group on scraped CSV row {row_number}: "
                    f"{player['age_group']!r}"
                )
            players.append(player)

    if not players:
        raise ValueError(f"No player records were found in {csv_path}.")
    return players


def read_competition_players(json_path, competition_name):
    if not json_path.is_file():
        raise FileNotFoundError(
            f"{competition_name} player data JSON was not found: {json_path}"
        )

    data = json.loads(json_path.read_text(encoding="utf-8"))
    grades = data.get("grades")
    if not isinstance(grades, list) or not grades:
        raise ValueError(f"No {competition_name} grades were found in {json_path}.")

    players = []
    for grade_index, grade in enumerate(grades, start=1):
        grade_name = (grade.get("grade") or "").strip()
        grade_players = grade.get("players")
        if not grade_name or not isinstance(grade_players, list):
            raise ValueError(
                f"{competition_name} grade {grade_index} has no grade name "
                "or player list."
            )
        for player_index, player in enumerate(grade_players, start=1):
            name = (player.get("name") or "").strip()
            team = (player.get("team") or "").strip()
            if not name or not team:
                raise ValueError(
                    f"{competition_name} player {player_index} in {grade_name} "
                    "has no name or team."
                )
            record = {
                "name": name,
                "team": team,
                "competition": competition_name,
                "grade": grade_name,
                "age_group": grade.get("age_group", ""),
                "statistics_url": grade.get("statistics_url", ""),
            }
            has_games = "games_played" in player
            has_points = "total_points" in player
            if has_games != has_points:
                raise ValueError(
                    f"{competition_name} player {player_index} in {grade_name} "
                    "has incomplete games-played or total-points data."
                )
            if has_games:
                games_played = player["games_played"]
                total_points = player["total_points"]
                if (
                    not isinstance(games_played, int)
                    or games_played < 0
                    or not isinstance(total_points, int)
                    or total_points < 0
                ):
                    raise ValueError(
                        f"{competition_name} player {player_index} in {grade_name} "
                        "has invalid games-played or total-points data."
                    )
                record["games_played"] = games_played
                record["total_points"] = total_points
                if "points_per_game" in player:
                    ppg = player["points_per_game"]
                    if ppg is not None and (
                        not isinstance(ppg, (int, float)) or ppg < 0
                    ):
                        raise ValueError(
                            f"{competition_name} player {player_index} in "
                            f"{grade_name} has invalid points-per-game data."
                        )
                    record["points_per_game"] = ppg
            players.append(record)

    if not players:
        raise ValueError(f"No {competition_name} players were found in {json_path}.")
    return players


def normalize_name(name):
    return "".join(
        character
        for character in unicodedata.normalize("NFKC", name).casefold()
        if character.isalnum()
    )


def match_age_group(match):
    age_group = match.get("age_group", "").upper()
    if not age_group:
        age_match = AGE_PATTERN.search(match["grade"])
        if age_match:
            age_group = f"U{age_match.group(1)}"
    return age_group


def grade_priority(match):
    age_group = match_age_group(match)
    is_edjba = match.get("competition", "").startswith("EDJBA")
    grade_text = match["grade"].upper()
    age_match = AGE_PATTERN.search(grade_text)
    grade_suffix = grade_text[age_match.end() :] if age_match else grade_text
    grade_suffix = re.sub(r"^\s*BOYS\b", "", grade_suffix).strip()
    grade_codes = re.findall(r"[A-Z]+", grade_suffix)
    if age_group == "U12":
        grade_letter = next(
            (
                code
                for code in grade_codes
                if code in {"A", "AR", "ARES", "B", "C", "D"}
            ),
            "",
        )
    elif "A" in grade_codes:
        grade_letter = "A"
    elif "AR" in grade_codes or "ARES" in grade_codes:
        grade_letter = "ARES"
    elif "B" in grade_codes or "BR" in grade_codes:
        grade_letter = "B"
    elif (
        is_edjba
        and age_group == "U11"
        and any(code.startswith("B") for code in grade_codes)
    ):
        grade_letter = "B"
    elif (
        is_edjba
        and age_group == "U11"
        and any(code.startswith("A") for code in grade_codes)
    ):
        grade_letter = "A"
    else:
        grade_letter = grade_codes[-1] if grade_codes else ""
    if age_group == "U12":
        return {"A": -4, "B": -3, "C": -2, "D": -1}.get(
            "ARES" if grade_letter in {"AR", "ARES"} else grade_letter,
            -1,
        )
    preferred_order = {
        ("U11", "A"): 0,
        ("U11", "ARES"): 1,
        ("U11", "B"): 2,
        ("U10", "A"): 3,
        ("U10", "ARES"): 5,
        ("U10", "B"): 6,
        ("U9", "A"): 7,
        ("U9", "ARES"): 9,
        ("U9", "B"): 10,
    }
    if (age_group, grade_letter) in preferred_order:
        return preferred_order[(age_group, grade_letter)]
    return {"U11": 4, "U10": 8, "U9": 11}.get(age_group, 99)


def grade_bonus(match):
    competition = match.get("competition") or ""
    age_group = match_age_group(match)
    if competition.startswith("Kilsyth"):
        priority = grade_priority(match)
        if age_group == "U12":
            return 25 if priority == -4 else 19
        kilsyth_bonuses = {
            "U11": ({0: 25, 1: 22, 2: 19}, 16),
            "U10": ({3: 19, 5: 16, 6: 13}, 1),
            "U9": ({7: 10}, 5),
        }
        bonuses = kilsyth_bonuses.get(age_group)
        if bonuses:
            grade_bonuses, other_bonus = bonuses
            return grade_bonuses.get(priority, other_bonus)
    if competition.startswith(("MEBA", "GEBC")):
        priority = grade_priority(match)
        if age_group == "U12":
            return 25 if priority == -4 else 20
        if age_group == "U10" and priority == 6:
            return 10
    if competition.startswith("EDJBA"):
        priority = grade_priority(match)
        edjba_bonuses = {
            "U12": ({-4: 28, -3: 22, -2: 18, -1: 14}, 14),
            "U11": ({0: 25, 1: 22, 2: 19}, 15),
            "U10": ({3: 22, 5: 19, 6: 16}, 12),
            "U9": ({7: 4}, 0),
        }
        bonuses = edjba_bonuses.get(age_group)
        if bonuses:
            grade_bonuses, other_bonus = bonuses
            return grade_bonuses.get(priority, other_bonus)
    if age_group == "U12":
        return 0
    if age_group == "U10" and grade_priority(match) == 5:
        return 13
    return max(0, 25 - 3 * grade_priority(match))


def division_label(grade):
    competition = {"MEBA": "GEBC"}.get(grade["competition"], grade["competition"])
    age_group = match_age_group(grade)
    grade_text = grade["grade"].upper()
    age_match = AGE_PATTERN.search(grade_text)
    division = grade_text[age_match.end() :] if age_match else grade_text
    division = re.sub(r"^\s*BOYS\b", "", division)
    division = re.sub(r"\bGRADING\b", "", division)
    division = re.sub(r"\s+", "", division)
    return f"{competition} {age_group}{division}".strip()


def build_snapshot_grades(competition_players, competition_name):
    grades_by_name = {}
    for player in competition_players:
        grade = grades_by_name.setdefault(
            player["grade"],
            {
                "competition": competition_name,
                "grade": player["grade"],
                "age_group": player.get("age_group", ""),
                "statistics_url": player["statistics_url"],
                "players": [],
            },
        )
        grade["players"].append(
            {
                key: player[key]
                for key in ("name", "team", "games_played", "total_points")
            }
        )

    grades = list(grades_by_name.values())
    grades.sort(
        key=lambda grade: (
            grade_priority(grade),
            grade["grade"].casefold(),
        )
    )
    return grades


def match_source_players(source_players, scraped_players):
    scraped_by_name = {}
    for player in scraped_players:
        scraped_by_name.setdefault(normalize_name(player["player_name"]), []).append(
            {
                "age_group": player["age_group"],
                "grade": player["grade"],
                "team": player["team"],
            }
        )

    matched_count = 0
    for player in source_players:
        matches = scraped_by_name.get(normalize_name(player["name"]), [])
        unique_matches = list(
            {
                (match["grade"], match["team"]): match
                for match in matches
            }.values()
        )
        player["scraped_matches"] = unique_matches
        preferred_match = min(
            unique_matches,
            key=lambda match: (
                grade_priority(match),
                match["grade"].casefold(),
                match["team"].casefold(),
            ),
            default=None,
        )
        player["initial_grade"] = (
            preferred_match["grade"] if preferred_match else ""
        )
        if unique_matches:
            matched_count += 1
    return matched_count


def match_competition_players(
    source_players,
    competition_players,
    result_field,
    include_stats=False,
):
    players_by_name = {}
    for player in competition_players:
        record = {
            "team": player["team"],
            "competition": player.get("competition", ""),
            "grade": player["grade"],
            "age_group": player.get("age_group", ""),
        }
        if include_stats:
            for field in ("games_played", "total_points", "points_per_game"):
                if field in player:
                    record[field] = player[field]
            if "games_played" not in record or "total_points" not in record:
                raise ValueError(
                    f"Missing {result_field} games or points for {player['name']}."
                )
        players_by_name.setdefault(normalize_name(player["name"]), []).append(record)

    matched_count = 0
    for player in source_players:
        matches = players_by_name.get(normalize_name(player["name"]), [])
        unique_matches = {}
        for match in matches:
            match_key = (match["grade"], match["team"])
            if include_stats:
                match_key += (match["games_played"], match["total_points"])
            unique_matches[match_key] = match
        player[result_field] = sorted(
            unique_matches.values(),
            key=lambda match: (match["grade"].casefold(), match["team"].casefold()),
        )
        if player[result_field]:
            matched_count += 1
    return matched_count


def find_grade_links(page, competition_url):
    try:
        page.wait_for_function(
            """() => [...document.querySelectorAll('a[href]')].some(anchor =>
                /\\bU\\s*0?(9|10|11)\\b/i.test(anchor.innerText)
                    && /\\bBoys\\b/i.test(anchor.innerText)
            )""",
            timeout=45000,
        )
    except PlaywrightTimeoutError as exc:
        raise RuntimeError(
            "No U9-U11 Boys grade links appeared; check the competition URL."
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
        parsed_url = urlparse(link["url"])
        age_match = AGE_PATTERN.search(link["text"])
        if (
            age_match
            and age_match.group(1) in {"9", "10", "11"}
            and BOYS_PATTERN.search(link["text"])
            and not re.search(r"\bgirls\b", link["text"], re.IGNORECASE)
            and parsed_url.netloc == "www.playhq.com"
            and parsed_url.path.startswith(competition_path + "/")
            and link["url"] not in seen_urls
        ):
            seen_urls.add(link["url"])
            grades.append(
                {
                    "grade": link["name"],
                    "age_group": f"U{age_match.group(1)}",
                    "url": link["url"],
                    "statistics_url": link["url"].rstrip("/") + "/statistics",
                }
            )

    if not grades:
        raise RuntimeError("No U9-U11 Boys grades were found.")
    return grades


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


def read_statistics_rows(page, grade):
    try:
        page.wait_for_function(
            """() => [...document.querySelectorAll('table')].some(table => {
                const rows = [...table.querySelectorAll('tr')];
                if (rows.length < 2) return false;
                const headers = [...rows[0].children]
                    .map(cell => cell.innerText.trim().toUpperCase());
                return ['PLAYER', 'TEAM', 'GP', 'TP'].every(header =>
                    headers.includes(header)
                );
            })""",
            timeout=30000,
        )
    except PlaywrightTimeoutError as exc:
        raise RuntimeError(
            f"Player statistics table did not load for {grade['grade']}: {page.url}"
        ) from exc

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
            and all(
                header in [cell.strip().upper() for cell in rows[0]]
                for header in ("PLAYER", "TEAM", "GP", "TP")
            )
        ),
        None,
    )
    if table is None:
        raise RuntimeError(f"Unexpected statistics table for {grade['grade']}.")

    headers = [cell.strip().upper() for cell in table[0]]
    indices = {
        header: headers.index(header) for header in ("PLAYER", "TEAM", "GP", "TP")
    }
    players = []
    for row in table[1:]:
        if len(row) <= max(indices.values()):
            raise RuntimeError(
                f"Unexpected statistics row in {grade['grade']}: {row}"
            )
        name = row[indices["PLAYER"]].strip()
        team = row[indices["TEAM"]].strip()
        if not name or not team:
            raise RuntimeError(
                f"Missing player or team in {grade['grade']} statistics: {row}"
            )
        players.append(
            {
                "name": name,
                "team": team,
                "games_played": parse_stat(row[indices["GP"]], grade["grade"], "GP"),
                "total_points": parse_stat(row[indices["TP"]], grade["grade"], "TP"),
            }
        )
    return players


def scrape_grade(page, grade):
    statistics_url = grade["url"].rstrip("/") + "/statistics?source=grade-tab"
    players = None
    for attempt in range(1, 4):
        try:
            response = page.goto(statistics_url, wait_until="domcontentloaded")
            if response is not None and response.status >= 400:
                raise RuntimeError(
                    f"PlayHQ returned HTTP {response.status} for {grade['grade']}: "
                    f"{statistics_url}"
                )
            players = read_statistics_rows(page, grade)
            break
        except (PlaywrightTimeoutError, RuntimeError) as exc:
            if attempt == 3:
                raise RuntimeError(
                    f"Could not load {grade['grade']} statistics after "
                    f"{attempt} attempts: {exc}"
                ) from exc
            print(
                f"  Could not load {grade['grade']} statistics; "
                f"retrying ({attempt}/3).",
                file=sys.stderr,
                flush=True,
            )
            page.wait_for_timeout(attempt * 2000)

    if players is None or not players:
        raise RuntimeError(f"No player statistics were listed for {grade['grade']}.")

    footer = STATISTICS_FOOTER.search(page.locator("body").inner_text())
    if not footer:
        raise RuntimeError(
            f"Could not determine the statistics row count for {grade['grade']}."
        )
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
        page_link.click(timeout=30000)
        try:
            page.wait_for_url(
                lambda current_url: parse_qs(urlparse(current_url).query).get(
                    "page"
                )
                == [str(page_number)],
                timeout=30000,
            )
            page.wait_for_function(
                """expectedStart => {
                    const match = document.body.innerText.match(
                        /Showing\\s+(\\d+)\\s*-/
                    );
                    return match && Number(match[1]) === expectedStart;
                }""",
                arg=expected_first_rank,
                timeout=30000,
            )
        except PlaywrightTimeoutError:
            page_url = (
                f"{grade['url'].rstrip('/')}/statistics?page={page_number}"
            )
            print(
                f"  Pagination did not complete for {grade['grade']}; "
                f"retrying page {page_number} directly.",
                file=sys.stderr,
                flush=True,
            )
            response = page.goto(page_url, wait_until="domcontentloaded")
            if response is not None and response.status >= 400:
                raise RuntimeError(
                    f"PlayHQ returned HTTP {response.status} for "
                    f"{grade['grade']} page {page_number}: {page_url}"
                )
            try:
                page.wait_for_function(
                    """expectedStart => {
                        const match = document.body.innerText.match(
                            /Showing\\s+(\\d+)\\s*-/
                        );
                        return match && Number(match[1]) === expectedStart;
                    }""",
                    arg=expected_first_rank,
                    timeout=30000,
                )
            except PlaywrightTimeoutError as retry_exc:
                raise RuntimeError(
                    f"Statistics page {page_number} did not load for "
                    f"{grade['grade']} after direct retry: {page_url}"
                ) from retry_exc
        players.extend(read_statistics_rows(page, grade))

    if len(players) != total_rows:
        raise RuntimeError(
            f"Scraped {len(players)} of {total_rows} statistics rows "
            f"for {grade['grade']}."
        )
    return players


def assign_initial_matches(report):
    grades_by_key = {grade["key"]: grade for grade in report["grades"]}
    grades_by_competition = {
        (grade["competition"], grade["grade"]): grade
        for grade in report["grades"]
    }

    for candidate in report["candidates"]:
        available_matches = []
        scraped_grade_names = dict.fromkeys(
            match["grade"] for match in candidate["scraped_matches"]
        )
        for grade_name in scraped_grade_names:
            grade = grades_by_competition.get(("Kilsyth", grade_name))
            if not grade:
                continue
            name_matches = [
                player
                for player in grade["players"]
                if normalize_name(player["name"]) == normalize_name(candidate["name"])
            ]
            expected_teams = {
                normalize_name(scraped_match["team"])
                for scraped_match in candidate["scraped_matches"]
                if scraped_match["grade"] == grade_name
            }
            team_matches = [
                player
                for player in name_matches
                if normalize_name(player["team"]) in expected_teams
            ]
            for player_match in team_matches or name_matches:
                available_matches.append(
                    {
                        "competition": "Kilsyth",
                        "grade": grade["grade"],
                        "age_group": grade.get("age_group", ""),
                        "team": player_match["team"],
                        "games_played": player_match["games_played"],
                        "grade_key": grade["key"],
                    }
                )

        for competition, result_field in (
            ("MEBA", "gebc_matches"),
            ("EDJBA", "edjba_matches"),
        ):
            for match in candidate[result_field]:
                grade = grades_by_competition.get((competition, match["grade"]))
                if grade:
                    available_matches.append(
                        {
                            "competition": competition,
                            "grade": grade["grade"],
                            "age_group": match.get("age_group")
                            or grade.get("age_group", ""),
                            "team": match["team"],
                            "games_played": match["games_played"],
                            "grade_key": grade["key"],
                        }
                    )

        eligible_matches = [
            match for match in available_matches if match["games_played"] > 5
        ]
        selected_match = min(
            eligible_matches or available_matches,
            key=lambda match: (
                grade_priority(match),
                {"Kilsyth": 0, "MEBA": 1, "EDJBA": 2}[match["competition"]],
                match["grade"].casefold(),
                match["team"].casefold(),
            ),
            default=None,
        )
        candidate["initial_grade_key"] = (
            selected_match["grade_key"] if selected_match else ""
        )
        candidate["initial_team"] = selected_match["team"] if selected_match else ""


def build_html(reports):
    for report in reports.values():
        assign_initial_matches(report)
        for grade in report["grades"]:
            grade["division"] = division_label(grade)
            grade["bonus_points"] = grade_bonus(grade)
    payload = json.dumps(reports, ensure_ascii=False)
    payload = payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(
        ">", "\\u003e"
    )
    tryouts_tab = (
        '<button id="tab-tryouts" type="button" role="tab" '
        'aria-selected="false" aria-controls="analysis-panel" tabindex="-1" '
        'data-report="tryouts_2026">2026 Tryouts</button>'
        if "tryouts_2026" in reports
        else ""
    )
    return (
        HTML_TEMPLATE.replace("__REPORT_DATA__", payload)
        .replace("__TRYOUTS_TAB__", tryouts_tab)
    )


HTML_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Player Analysis 20261001 - Kilsyth, MEBA, and EDJBA Statistics</title>
  <style>
    :root { color-scheme: light; font-family: Segoe UI, Arial, sans-serif; color: #17212b; background: #f3f6f9; }
    * { box-sizing: border-box; }
    body { margin: 0; }
    header { background: #123b5d; color: white; padding: 1.4rem max(1rem, calc((100% - 1500px) / 2)); }
    header h1 { margin: 0 0 .35rem; font-size: 1.6rem; }
    header p { margin: .25rem 0; color: #e2edf5; }
    main { max-width: 1500px; margin: 1.25rem auto; padding: 0 1rem 2rem; }
    .notice, .controls { background: white; border: 1px solid #d5dee7; border-radius: 8px; padding: 1rem; margin-bottom: 1rem; }
    .notice p { margin: .25rem 0; }
    .tabs { display: flex; gap: .5rem; margin-bottom: .75rem; }
    .tabs button { font: inherit; font-weight: 600; padding: .65rem 1rem; border: 1px solid #9aabba; border-radius: 6px 6px 0 0; background: #eaf0f5; color: inherit; cursor: pointer; }
    .tabs button[aria-selected="true"] { background: #123b5d; border-color: #123b5d; color: white; }
    .controls { display: flex; flex-wrap: wrap; gap: .75rem; align-items: end; }
    label { display: grid; gap: .3rem; font-weight: 600; }
    input, select { font: inherit; padding: .5rem; border: 1px solid #9aabba; border-radius: 5px; background: white; color: inherit; }
    input { min-width: 16rem; }
    .age-group-filter { min-width: 16rem; border: 0; padding: 0; margin: 0; }
    .age-group-filter legend { font-weight: 600; padding: 0; margin-bottom: .3rem; }
    .age-group-options { display: flex; flex-wrap: wrap; gap: .5rem; }
    .age-group-options label { display: flex; align-items: center; gap: .25rem; font-weight: 400; }
    .age-group-options input { min-width: 0; margin: 0; }
    .age-group-filter small { display: block; margin-top: .25rem; color: #536575; }
    #summary { margin-left: auto; font-weight: 600; }
    .table-wrap { overflow-x: auto; background: white; border: 1px solid #d5dee7; border-radius: 8px; }
    table { border-collapse: collapse; width: 100%; min-width: 2120px; table-layout: fixed; }
    col.source-number { width: 85px; }
    col.player-name { width: 200px; }
    col.court-column { width: 95px; }
    col.ac-court-column { width: 115px; }
    col.vjbl-team { width: 180px; }
    col.used-group { width: 125px; }
    col.division-column { width: 175px; }
    col.games-column { width: 80px; }
    col.points-column { width: 110px; }
    col.ppg-column { width: 120px; }
    col.bonus-column { width: 100px; }
    col.rating-column { width: 145px; }
    col.csv-match { width: 140px; }
    col.statistics-match { width: 300px; }
    col.source-link { width: 150px; }
    table.grade-bonus-table { width: 100%; min-width: 0; table-layout: auto; margin: .5rem 0; }
    .grade-bonus-table th, .grade-bonus-table td { border: 1px solid #d5dee7; padding: .4rem; text-align: left; vertical-align: top; }
    th, td { padding: .6rem .65rem; border-bottom: 1px solid #e3e9ee; text-align: left; vertical-align: top; overflow-wrap: anywhere; }
    th { position: sticky; top: 0; z-index: 1; background: #eaf0f5; font-size: .9rem; line-height: 1.25; }
    tbody tr:nth-child(even) { background: #f8fafc; }
    tbody tr:hover { background: #f0f6fb; }
    th.numeric, td.numeric { text-align: right; font-variant-numeric: tabular-nums; }
    td.numeric { white-space: nowrap; }
    td a { color: #075b99; }
    td.court, td.ac-court { font-weight: 600; white-space: nowrap; }
    td.player-name { font-weight: 600; }
    td.division { white-space: nowrap; }
    .muted { color: #586777; }
    .match-note { font-size: .88rem; }
    .stat-line + .stat-line { margin-top: .35rem; padding-top: .35rem; border-top: 1px dashed #d5dee7; }
    .empty { color: #7a4b00; }
    footer { color: #586777; font-size: .85rem; margin-top: .75rem; }
    @media (max-width: 640px) {
      header { padding: 1rem; }
      input { min-width: 100%; }
      #summary { margin-left: 0; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Player Analysis 20261001 — Kilsyth, MEBA, and EDJBA Statistics</h1>
    <p id="competition"></p>
    <p id="snapshot"></p>
  </header>
  <main>
    <section class="notice">
      <p><strong>Source of truth:</strong> player number and name come from the workbook's Master sheet. The scraped CSV is used only to find matching Kilsyth grades and teams; Kilsyth, MEBA, and EDJBA statistics supply games played and points.</p>
      <p class="muted">Only the Master sheet's Number, First Name, Last Name, Used Group (shown as Age group), and column J (shown as AC Court) are read. Other analysis fields are not included. Matches use normalized first and last names. The report selects the highest matched grade for which the player has more than 5 games; if none have more than 5 games, it selects the highest matched grade overall. Grade order is EDJBA U12 A, B, C, or D, then U11 A, U11 ARES, U11 B, U10 A, other U11, U10 ARES, U10 B, U9 A, other U10, U9 ARES, U9 B, then other U9. Ties prefer Kilsyth, then MEBA, then EDJBA. Use the clickable PlayHQ link to verify the statistics.</p>
      <p class="muted"><strong>VJBL team lookup:</strong> names are matched exactly after normalization against the available 2027 U12 Boys Round 1 player statistics across all divisions. A missing match means the player was not found in this snapshot, not necessarily that they are not registered with VJBL. Multiple exact matches are all shown for checking.</p>
      <p class="muted"><strong>MEBA lookup:</strong> exact normalized name matches from the saved GEBC U9-U11 Boys Statistics data use the same team, statistics, grade bonus, and rating columns as Kilsyth. A missing match means the player was not found in this snapshot, not necessarily that they are not registered with MEBA.</p>
      <p class="muted"><strong>EDJBA lookup:</strong> exact normalized name matches from each period's saved Boys Statistics data use those same columns. Current uses U10 and U12; 2026 Tryouts uses U11. The bonus table below lists the EDJBA bonus for each age group and grade. A missing match means the player was not found in this snapshot, not necessarily that they are not registered with EDJBA.</p>
      <p class="muted"><strong>Grade bonus points by competition:</strong></p>
      <table class="grade-bonus-table">
        <thead><tr><th scope="col">Competition</th><th scope="col">Grade bonuses</th></tr></thead>
        <tbody>
          <tr><th scope="row">Kilsyth</th><td>U11: A +25, ARES +22, B +19, other +16; U10: A +19, ARES +16, B +13, other +1; U9: A +10, other +5; U12: A +25, other +19.</td></tr>
          <tr><th scope="row">GEBC</th><td>U11: A +25, ARES +22, B +19, other +13; U10: A +16, ARES +13, B +10, other +1; U9: A +4, other +0; U12: A +25, other +20.</td></tr>
          <tr><th scope="row">EDJBA</th><td>U12: A +28, B +22, C +18, D +14; U11: A +25, ARES +22, B +19, other +15; U10: A +22, ARES +19, B +16, other +12; U9: A +4, other +0.</td></tr>
        </tbody>
      </table>
      <p class="muted">Player rating is points per game plus the grade bonus shown above.</p>
    </section>
    <nav class="tabs" role="tablist" aria-label="Analysis period">
      <button id="tab-current" type="button" role="tab" aria-selected="true" aria-controls="analysis-panel" tabindex="0" data-report="current">Current</button>
      __TRYOUTS_TAB__
    </nav>
    <section id="analysis-panel" role="tabpanel" aria-labelledby="tab-current">
    <section class="controls">
      <label>Find a player
        <input id="search" type="search" placeholder="Search by name" autocomplete="off">
      </label>
      <label>Filter by assigned grade
        <select id="grade-filter"><option value="">All players</option><option value="__unassigned">No grade match</option></select>
      </label>
      <label>Filter by VJBL team
        <input id="vjbl-team-filter" type="search" placeholder="Type a VJBL team" autocomplete="off">
      </label>
      <fieldset id="age-group-filter" class="age-group-filter">
        <legend>Filter by age group</legend>
        <div id="age-group-options" class="age-group-options"></div>
        <small>Leave all unchecked to show all age groups.</small>
      </fieldset>
      <span id="summary" aria-live="polite"></span>
    </section>
    <div class="table-wrap">
      <table>
        <colgroup>
          <col class="source-number">
          <col class="player-name">
          <col class="court-column">
          <col class="ac-court-column">
          <col class="vjbl-team">
          <col class="used-group">
          <col class="division-column">
          <col class="games-column">
          <col class="points-column">
          <col class="ppg-column">
          <col class="bonus-column">
          <col class="rating-column">
          <col class="csv-match">
          <col class="statistics-match">
          <col class="source-link">
        </colgroup>
        <thead>
          <tr>
            <th scope="col">Source #</th>
            <th scope="col">Player Analysis name</th>
            <th scope="col">Court</th>
            <th scope="col">AC Court</th>
            <th scope="col">VJBL team</th>
            <th scope="col">Age group</th>
            <th scope="col">Division</th>
            <th class="numeric" scope="col">Games played</th>
            <th class="numeric" scope="col">Total points</th>
            <th class="numeric" scope="col">Points per game</th>
            <th class="numeric" scope="col">Grade bonus</th>
            <th class="numeric" scope="col">Player rating (high to low)</th>
            <th scope="col">Scraped CSV match</th>
            <th scope="col">Statistics match</th>
            <th scope="col">PlayHQ source</th>
          </tr>
        </thead>
        <tbody id="players"></tbody>
      </table>
    </div>
    <footer>Points per game is total points divided by games played, rounded to two decimals. Player rating is points per game plus the automatically assigned grade's bonus, rounded to two decimals; it is not shown when games played is zero or no statistics match. Rows sort by rating from highest to lowest; rows without a rating appear last. The first 20 rated players are assigned to Court 1, then the next 20 to each of Courts 2 through 6; players outside the top 120 or without a rating are left blank.</footer>
    </section>
  </main>
  <script id="report-data" type="application/json">__REPORT_DATA__</script>
  <script>
    const reports = JSON.parse(document.getElementById("report-data").textContent);
    let report;
    const gradeFilter = document.getElementById("grade-filter");
    const ageGroupFilter = document.getElementById("age-group-filter");
    const ageGroupOptions = document.getElementById("age-group-options");
    const vjblTeamFilter = document.getElementById("vjbl-team-filter");
    const tbody = document.getElementById("players");
    const rows = [];
    const tabButtons = [...document.querySelectorAll('[role="tab"]')];
    const usedGroupLabels = new Map([
      ["T", "Top"],
      ["B", "Bottom"],
      ["BB", "Double Bottom"],
    ]);
    const normalise = value => value.normalize("NFKC").toLocaleLowerCase()
      .replace(/[^\\p{L}\\p{N}]+/gu, " ").trim();

    function setCellText(cell, value) {
      cell.replaceChildren(document.createTextNode(value));
    }

    function updateSummary() {
      const statsMatched = rows.filter(row => row.currentMatches.length === 1).length;
      document.getElementById("summary").textContent =
        `${report.candidates.length} source players · ${report.vjbl_match_count} VJBL matches · ${report.gebc_match_count} MEBA matches · ${report.edjba_match_count} EDJBA matches · ${report.source_match_count} Kilsyth CSV matches · ${statsMatched} stats matches`;
    }

    function sortRowsByRating() {
      rows.sort((a, b) => {
        if (a.ratingValue === null && b.ratingValue !== null) return 1;
        if (b.ratingValue === null && a.ratingValue !== null) return -1;
        return (b.ratingValue ?? 0) - (a.ratingValue ?? 0)
          || a.name.localeCompare(b.name)
          || a.sourceOrder - b.sourceOrder;
      });
      rows.forEach((row, index) => {
        const courtNumber = row.ratingValue !== null && index < 120
          ? Math.floor(index / 20) + 1
          : null;
        setCellText(row.court, courtNumber === null ? "" : `Court ${courtNumber}`);
        tbody.append(row.element);
      });
    }

    function applyFilters() {
      const query = normalise(document.getElementById("search").value);
      const selectedGrade = gradeFilter.value;
      const selectedAgeGroups = new Set(
        [...ageGroupFilter.querySelectorAll("input:checked")].map(input => input.value)
      );
      const selectedVjblTeam = normalise(vjblTeamFilter.value);
      for (const row of rows) {
        const nameMatches = !query || normalise(row.name).includes(query);
        const gradeMatches = !selectedGrade
          || (selectedGrade === "__unassigned" ? !row.gradeKey : row.gradeKey === selectedGrade);
        const ageGroupMatches = !selectedAgeGroups.size
          || selectedAgeGroups.has(row.ageGroup || "__unassigned");
        const vjblTeamMatches = !selectedVjblTeam
          || [...row.vjblTeams].some(team => normalise(team).includes(selectedVjblTeam));
        row.element.hidden = !(
          nameMatches && gradeMatches && ageGroupMatches && vjblTeamMatches
        );
      }
    }

    function updatePlayer(row) {
      row.ratingValue = null;
      const grade = report.grades.find(item => item.key === row.gradeKey);
      const sourceMatchesForGrade = grade && grade.competition === "Kilsyth"
        ? row.candidate.scraped_matches.filter(match => match.grade === grade.grade)
        : [];
      const nameMatches = grade
        ? grade.players.filter(player => normalise(player.name) === normalise(row.name))
        : [];
      const selectedTeam = row.candidate.initial_team;
      const selectedTeamMatches = nameMatches.filter(
        player => normalise(player.team) === normalise(selectedTeam)
      );
      row.teamMatches = grade && grade.competition === "Kilsyth"
        ? nameMatches.filter(player => sourceMatchesForGrade.some(
          match => normalise(match.team) === normalise(player.team)
        ))
        : selectedTeamMatches;
      row.currentMatches = selectedTeamMatches.length
        ? selectedTeamMatches
        : nameMatches;
      row.division.replaceChildren();
      row.games.replaceChildren();
      row.points.replaceChildren();
      row.ppg.replaceChildren();
      row.bonus.replaceChildren();
      row.rating.replaceChildren();
      row.source.replaceChildren();

      setCellText(
        row.scrapedMatch,
        row.candidate.scraped_matches.length
          ? `${row.candidate.scraped_matches.length} scraped ${row.candidate.scraped_matches.length === 1 ? "entry" : "entries"}`
          : "No scraped CSV match"
      );

      if (!grade) {
        setCellText(row.division, "—");
        setCellText(row.games, "—");
        setCellText(row.points, "—");
        setCellText(row.ppg, "—");
        setCellText(row.bonus, "—");
        setCellText(row.rating, "—");
        setCellText(row.source, "—");
        setCellText(row.status, "No competition-grade match");
        row.status.className = "match-note muted";
      } else {
        setCellText(row.division, grade.division);
        setCellText(row.bonus, `+${grade.bonus_points}`);
        const link = document.createElement("a");
        link.href = grade.statistics_url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = "View grade statistics";
        row.source.append(link);

        if (row.currentMatches.length) {
          for (const match of row.currentMatches) {
            const gamesLine = document.createElement("div");
            gamesLine.className = "stat-line";
            setCellText(gamesLine, String(match.games_played));
            row.games.append(gamesLine);
            const pointsLine = document.createElement("div");
            pointsLine.className = "stat-line";
            setCellText(pointsLine, String(match.total_points));
            row.points.append(pointsLine);
            const ppg = match.games_played
              ? (match.total_points / match.games_played).toFixed(2)
              : "—";
            const ppgLine = document.createElement("div");
            ppgLine.className = "stat-line";
            setCellText(ppgLine, ppg);
            row.ppg.append(ppgLine);
            const rating = match.games_played
              ? (match.total_points / match.games_played + grade.bonus_points).toFixed(2)
              : "—";
            if (rating !== "—") {
              const numericRating = Number(rating);
              row.ratingValue = row.ratingValue === null
                ? numericRating
                : Math.max(row.ratingValue, numericRating);
            }
            const ratingLine = document.createElement("div");
            ratingLine.className = "stat-line";
            setCellText(ratingLine, rating);
            row.rating.append(ratingLine);
          }
          const label = grade.competition === "Kilsyth"
            ? row.teamMatches.length
              ? "Name and team match"
              : row.currentMatches.length === 1
                ? sourceMatchesForGrade.length
                  ? "Name matched; team differs"
                  : "Name matched; no CSV entry for this grade"
                : `${row.currentMatches.length} name matches; check team`
            : row.currentMatches.length === 1
              ? `Name matched in ${grade.competition} statistics`
              : `${row.currentMatches.length} name matches; check team`;
          setCellText(row.status, `${grade.label}: ${label}`);
          row.status.className = "match-note";
        } else {
          setCellText(row.games, "—");
          setCellText(row.points, "—");
          setCellText(row.ppg, "—");
          setCellText(row.rating, "—");
          setCellText(
            row.status,
            `${grade.label}: no statistics match in this grade`
          );
          row.status.className = "match-note empty";
        }
      }
      sortRowsByRating();
      updateSummary();
      applyFilters();
    }

    function buildRows() {
      report.candidates.forEach((candidate, index) => {
        const tr = document.createElement("tr");
        const sourceNumber = document.createElement("td");
        setCellText(sourceNumber, candidate.source_number);
        tr.append(sourceNumber);

        const name = document.createElement("td");
        name.className = "player-name";
        setCellText(name, candidate.name);
        tr.append(name);

        const court = document.createElement("td");
        court.className = "court";
        tr.append(court);

        const acCourt = document.createElement("td");
        acCourt.className = "ac-court";
        const acCourtValue = String(candidate.ac_court || "").trim();
        setCellText(
          acCourt,
          /^\\d+$/.test(acCourtValue) ? `Court ${acCourtValue}` : acCourtValue
        );
        tr.append(acCourt);

        const vjbl = document.createElement("td");
        if (candidate.vjbl_matches.length) {
          for (const match of candidate.vjbl_matches) {
            const line = document.createElement("div");
            line.className = "stat-line";
            setCellText(line, match.team);
            vjbl.append(line);
          }
        } else {
          setCellText(vjbl, "Not found in Round 1 data");
          vjbl.className = "match-note muted";
        }
        tr.append(vjbl);

        const usedGroup = document.createElement("td");
        const ageGroup = usedGroupLabels.get(candidate.used_group)
          || candidate.used_group
          || "";
        setCellText(
          usedGroup,
          ageGroup
        );
        tr.append(usedGroup);

        const division = document.createElement("td");
        division.className = "division";
        const games = document.createElement("td");
        const points = document.createElement("td");
        const ppg = document.createElement("td");
        const bonus = document.createElement("td");
        const rating = document.createElement("td");
        games.className = points.className = ppg.className = bonus.className =
          rating.className = "numeric";
        const source = document.createElement("td");
        const scrapedMatch = document.createElement("td");
        const status = document.createElement("td");
        status.className = "match-note muted";
        tr.append(division, games, points, ppg, bonus, rating, scrapedMatch, status, source);
        tbody.append(tr);

        const row = {
          candidate,
          name: candidate.name,
          ageGroup,
          vjblTeams: new Set(candidate.vjbl_matches.map(match => match.team)),
          court,
          vjbl,
          sourceOrder: index,
          element: tr,
          gradeKey: candidate.initial_grade_key,
          division,
          games,
          points,
          ppg,
          bonus,
          rating,
          source,
          scrapedMatch,
          status,
          currentMatches: [],
          teamMatches: [],
        };
        rows.push(row);
        updatePlayer(row);
      });
    }

    function renderReport(reportKey) {
      report = reports[reportKey];
      rows.length = 0;
      tbody.replaceChildren();
      gradeFilter.replaceChildren(
        new Option("All players", ""),
        new Option("No grade match", "__unassigned")
      );
      ageGroupOptions.replaceChildren();
      vjblTeamFilter.value = "";
      document.getElementById("search").value = "";
      document.getElementById("competition").textContent = report.competition;
      document.getElementById("snapshot").textContent =
        `Statistics snapshot: ${new Date(report.scraped_at).toLocaleString()}`;
      document.getElementById("analysis-panel").setAttribute(
        "aria-labelledby",
        `tab-${reportKey === "current" ? "current" : "tryouts"}`
      );
      for (const tab of tabButtons) {
        const selected = tab.dataset.report === reportKey;
        tab.setAttribute("aria-selected", String(selected));
        tab.tabIndex = selected ? 0 : -1;
      }
      for (const grade of report.grades) {
        const option = document.createElement("option");
        option.value = grade.key;
        option.textContent = grade.label;
        gradeFilter.append(option);
      }
      const ageGroups = new Set(report.candidates.map(candidate =>
        usedGroupLabels.get(candidate.used_group) || candidate.used_group || ""
      ));
      for (const ageGroup of [...ageGroups].filter(Boolean).sort()) {
        const label = document.createElement("label");
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.value = ageGroup;
        label.append(checkbox, document.createTextNode(ageGroup));
        ageGroupOptions.append(label);
      }
      if (ageGroups.has("")) {
        const label = document.createElement("label");
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.value = "__unassigned";
        label.append(checkbox, document.createTextNode("No age group"));
        ageGroupOptions.append(label);
      }
      buildRows();
    }

    document.getElementById("search").addEventListener("input", applyFilters);
    gradeFilter.addEventListener("change", applyFilters);
    ageGroupOptions.addEventListener("change", applyFilters);
    vjblTeamFilter.addEventListener("input", applyFilters);
    tabButtons.forEach(tab => tab.addEventListener(
      "click",
      () => renderReport(tab.dataset.report)
    ));
    renderReport("current");
  </script>
</body>
</html>
"""


def scrape_kilsyth_grades(page, competition_url, scraped_players, fallback_label):
    response = page.goto(competition_url, wait_until="domcontentloaded")
    if response is not None and response.status >= 400:
        raise RuntimeError(
            f"PlayHQ returned HTTP {response.status} for {competition_url}"
        )
    competition = page.title().split(" | PlayHQ", 1)[0].strip()
    if not competition or competition.casefold() == "playhq":
        competition = fallback_label
    grades = find_grade_links(page, competition_url)
    grade_names = {grade["grade"] for grade in grades}
    missing_grades = sorted(
        {player["grade"] for player in scraped_players} - grade_names
    )
    if missing_grades:
        raise RuntimeError(
            f"Scraped CSV grades are missing from {competition_url}: "
            + ", ".join(missing_grades)
        )

    for index, grade in enumerate(grades, start=1):
        print(
            f"[{index}/{len(grades)}] {grade['grade']}",
            file=sys.stderr,
            flush=True,
        )
        grade["players"] = scrape_grade(page, grade)
        page.wait_for_timeout(500)
    for grade in grades:
        grade["competition"] = "Kilsyth"
    return competition, grades


def build_report_data(
    source_players,
    scraped_players,
    vjbl_players,
    gebc_players,
    edjba_players,
    kilsyth_grades,
    competition,
    source_url,
):
    source_match_count = match_source_players(source_players, scraped_players)
    vjbl_match_count = match_competition_players(
        source_players,
        vjbl_players,
        "vjbl_matches",
    )
    gebc_match_count = match_competition_players(
        source_players,
        gebc_players,
        "gebc_matches",
        include_stats=True,
    )
    edjba_match_count = match_competition_players(
        source_players,
        edjba_players,
        "edjba_matches",
        include_stats=True,
    )
    meba_grades = build_snapshot_grades(gebc_players, "MEBA")
    edjba_grades = build_snapshot_grades(edjba_players, "EDJBA")
    grades = kilsyth_grades + meba_grades + edjba_grades
    for grade in grades:
        grade["key"] = f"{grade['competition']}::{grade['grade']}"
        grade["label"] = f"{grade['competition']} — {grade['grade']}"
        grade["bonus_points"] = grade_bonus(grade)

    return {
        "competition": competition,
        "source_url": source_url,
        "scraped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source_match_count": source_match_count,
        "vjbl_match_count": vjbl_match_count,
        "gebc_match_count": gebc_match_count,
        "edjba_match_count": edjba_match_count,
        "candidates": source_players,
        "grades": grades,
    }


def main():
    args = parse_args()
    tryouts_paths = (
        args.tryouts_kilsyth_players,
        args.tryouts_gebc_players,
        args.tryouts_edjba_players,
    )
    if any(tryouts_paths) and not all(tryouts_paths):
        raise SystemExit(
            "The 2026 Tryouts tab requires all three "
            "--tryouts-* player data files."
        )

    source_players = read_source_players(args.source_of_truth)
    scraped_players = read_scraped_players(args.scraped_players)
    vjbl_players = read_competition_players(args.vjbl_players, "VJBL")
    gebc_players = read_competition_players(args.gebc_players, "GEBC")
    edjba_players = read_competition_players(args.edjba_players, "EDJBA")
    tryouts_scraped_players = (
        read_scraped_players(args.tryouts_kilsyth_players)
        if all(tryouts_paths)
        else None
    )
    tryouts_gebc_players = (
        read_competition_players(args.tryouts_gebc_players, "GEBC Winter 2026")
        if all(tryouts_paths)
        else None
    )
    tryouts_edjba_players = (
        read_competition_players(args.tryouts_edjba_players, "EDJBA Winter 2026")
        if all(tryouts_paths)
        else None
    )

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=args.headless)
        try:
            page = browser.new_page()
            page.set_default_navigation_timeout(60000)
            competition, kilsyth_grades = scrape_kilsyth_grades(
                page,
                args.competition_url,
                scraped_players,
                "Summer 2026/27 competitions — Kilsyth, MEBA (GEBC), and EDJBA",
            )
            tryouts_competition = None
            tryouts_kilsyth_grades = None
            if tryouts_scraped_players is not None:
                tryouts_competition, tryouts_kilsyth_grades = scrape_kilsyth_grades(
                    page,
                    args.tryouts_competition_url,
                    tryouts_scraped_players,
                    "Winter 2026 competitions — Kilsyth, MEBA (GEBC), and EDJBA",
                )
        finally:
            browser.close()

    reports = {
        "current": build_report_data(
            read_source_players(args.source_of_truth),
            scraped_players,
            vjbl_players,
            gebc_players,
            edjba_players,
            kilsyth_grades,
            competition,
            args.competition_url,
        )
    }
    if tryouts_scraped_players is not None:
        reports["tryouts_2026"] = build_report_data(
            read_source_players(args.source_of_truth),
            tryouts_scraped_players,
            vjbl_players,
            tryouts_gebc_players,
            tryouts_edjba_players,
            tryouts_kilsyth_grades,
            tryouts_competition,
            args.tryouts_competition_url,
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(build_html(reports), encoding="utf-8")
    print(
        f"Wrote tabs {', '.join('Current' if key == 'current' else '2026 Tryouts' for key in reports)} "
        f"for {len(reports['current']['candidates'])} source players to {args.output}.",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
