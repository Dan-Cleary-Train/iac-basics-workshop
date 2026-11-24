# Tryout player analysis and competition lookup

This folder contains the PlayHQ scrapers and report builder used to match the
tryout list against Kilsyth, MEBA, and EDJBA competitions. Keep generated player
data and reports outside the repository.

## Data coverage

PlayHQ's public Statistics tables are the source. They list players with
recorded game statistics, not every registered player. This snapshot therefore
does not claim to be a complete registration roster; players without a
recorded appearance may be missing. Names are copied as PlayHQ displays them;
the CSV does not infer a split between first and family names.

The Kilsyth CSV contains only player name, team, age group, competition, grade,
the grade's PlayHQ source link, and the scrape timestamp. It intentionally
does not collect birth dates, contact details, player IDs, or performance
statistics.

## Kilsyth U9-U11 Boys CSV

Python 3.12 and Playwright are required. From the repository root, save the
CSV outside the repository so player names are not added to source control:

```powershell
$python = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
$output = "$env:USERPROFILE\Documents\kilsyth_u9_u11_boys.csv"
& $python challenges\Kilsyth\scrape_kilsyth_u9_u11_boys.py --output $output
```

Chromium runs visibly by default because PlayHQ may block headless requests.
Add `--headless` to try a headless run.

Source: [Junior Domestic Summer 2026/27 grades for Kilsyth Basketball on
PlayHQ](https://www.playhq.com/basketball-victoria/org/kilsyth-basketball/junior-domestic-summer-202627/5e26f10f).

## Data sources and matching

`Player Analysis 20261001.xlsx` is the source of truth for the report's player
list. The builder reads `Number`, `First Name`, `Last Name`, `Used Group`, and
column J from its `Master` sheet. The report labels the Used Group column
**Age group**; values `T`, `B`, and `BB` display as **Top**, **Bottom**, and
**Double Bottom**, while blanks stay blank. Column J is shown as **AC Court**
next to the automatically assigned Court. Other workbook analysis fields are
not read. The Kilsyth
U9-U11 CSV is used only to match names to scraped grades and teams; current
games played and points are read from each grade's PlayHQ Statistics table.

VJBL teams are matched by normalized first and last name against
`vjbl_2027_u12_boys_scorers.json`, the available 2027 U12 Boys Round 1 player
statistics across all divisions. The report shows each exact match's team.
A missing match only means the player was not found in this Round 1
snapshot; it does not confirm they are not registered with VJBL. Multiple exact
matches are shown separately for verification.

MEBA (GEBC) U9-U11 Boys are scraped with [scrape_gebc_u9_u11_boys.py](./scrape_gebc_u9_u11_boys.py)
from the [Melbourne East PlayHQ
competition](https://www.playhq.com/basketball-victoria/org/melbourne-east-basketball-association-meba/junior-domestic-saturday-competition-gebc-summer-202627/7f7c13f5).
The scraper saves each grade's player and team names to
`gebc_u9_u11_boys_2026_27.json` outside the repository. Like Kilsyth, this data
comes from public Statistics tables, so players without recorded statistics
may be missing. Each JSON player entry includes team, games played, total
points, and points per game; the parent grade entry provides the grade name.
The report matches normalized first and last names. MEBA grades appear in the
same competition-grade selector and use the same team, games, points, points
per game, grade bonus, rating, sorting, and court-assignment columns as Kilsyth.
MEBA grades without player statistics are not selectable. An absent match does
not establish that a player is not registered.

## EDJBA U10 and U12 Boys

The EDJBA scraper reads public U10 and U12 Boys Statistics tables, excluding
U11, from the [EDJBA Summer
2026/27 PlayHQ competition](https://www.playhq.com/basketball-victoria/org/eastern-districts-junior-basketball-association-edjba/edjba-summer-202627/3f50cd49)
and saves team, grade, games played, total points, and points per game to a
JSON snapshot. As with the other competitions, this is not a complete
registration roster.

```powershell
$python = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
$edjbaPlayers = "$env:USERPROFILE\Downloads\edjba_u10_u12_boys_2026_27.json"
& $python challenges\Kilsyth\scrape_edjba_u10_u12_boys.py --output $edjbaPlayers
```

Chromium runs visibly by default because PlayHQ may block headless requests.
Add `--headless` to try a headless run.

## Winter 2026 tryouts

The report can include a separate **2026 Tryouts** tab using the same Master
sheet player list and rating rules, while keeping the existing analysis under
**Current**. The Winter 2026 sources are [Kilsyth](https://www.playhq.com/basketball-victoria/org/kilsyth-basketball/junior-domestic-winter-2026/8ff9f39e),
[MEBA/GEBC](https://www.playhq.com/basketball-victoria/org/melbourne-east-basketball-association-meba/junior-domestic-saturday-competition-gebc-winter-2026/15908988),
and [EDJBA](https://www.playhq.com/basketball-victoria/org/eastern-districts-junior-basketball-association-edjba/edjba-winter-2026/1ae60211).
Kilsyth and GEBC use U9-U11 Boys; EDJBA Winter uses U11 Boys, the age group
available for that season. The Current tab continues to use EDJBA U10 and
U12, excluding U11. EDJBA grade bonuses follow the competition matrix below.

Save the Winter 2026 snapshots outside the repository:

```powershell
$python = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
$kilsythWinterUrl = "https://www.playhq.com/basketball-victoria/org/kilsyth-basketball/junior-domestic-winter-2026/8ff9f39e"
$gebcWinterUrl = "https://www.playhq.com/basketball-victoria/org/melbourne-east-basketball-association-meba/junior-domestic-saturday-competition-gebc-winter-2026/15908988"
$edjbaWinterUrl = "https://www.playhq.com/basketball-victoria/org/eastern-districts-junior-basketball-association-edjba/edjba-winter-2026/1ae60211"
$kilsythWinter = "$env:USERPROFILE\Downloads\kilsyth_u9_u11_boys_winter_2026.csv"
$gebcWinter = "$env:USERPROFILE\Downloads\gebc_u9_u11_boys_winter_2026.json"
$edjbaWinter = "$env:USERPROFILE\Downloads\edjba_u11_boys_winter_2026.json"
& $python challenges\Kilsyth\scrape_kilsyth_u9_u11_boys.py --competition-url $kilsythWinterUrl --output $kilsythWinter
& $python challenges\Kilsyth\scrape_gebc_u9_u11_boys.py --competition-url $gebcWinterUrl --output $gebcWinter
& $python challenges\Kilsyth\scrape_edjba_u10_u12_boys.py --competition-url $edjbaWinterUrl --age-groups U11 --output $edjbaWinter
```

Pass all three snapshots to the report builder to add the tab. The same
source-of-truth workbook and VJBL lookup are used in both tabs; grade selection
and court assignments are calculated independently for each period.

## Report behavior and build

`Player Analysis 20261001.xlsx` is the source of truth for the report's player
list, Used Group, and AC Court values. The builder reads `Number`, `First Name`,
`Last Name`, `Used Group` (shown as **Age group**), and column J from its
`Master` sheet; column J appears in the report as **AC Court**; numeric court values display as
**Court 1**, **Court 2**, and so on.
Kilsyth's CSV is used to match names to Kilsyth grades and teams. The
GEBC and EDJBA JSON snapshots include the statistics used by the report. Exact
normalized name matching uses Unicode normalization, case-folding, and
alphanumeric characters; it does not use fuzzy matching.

The self-contained HTML report contains every named Master-sheet row. If a
player has matches in multiple competitions, grades, or teams, the highest
grade with more than five games played is assigned automatically. If no team
has more than five games, the highest grade overall is used. Ties prefer
Kilsyth, then MEBA, then EDJBA. The grade order is EDJBA U12 A, B, C, D,
followed by U11 A, U11 ARES, U11 B, U10 A, other U11, U10 ARES, U10 B, U9 A,
other U10, U9 ARES, U9 B, then other U9. EDJBA U10 grades follow their
corresponding U10 position in that order. The selected competition and grade
are shown in the Statistics match column; use the grade filter above the
table to filter players by their assigned grade. The Age group filter uses the
report's Age group labels (Top, Bottom, and Double Bottom), supports multiple
checkbox selections, and includes an option for players whose Age group is
blank. Type part or all of a team name in the VJBL team filter to search; it
matches team names as you type.
Each row shows the source-list match, competition and abbreviated division
(for example, **GEBC U12A** or **Kilsyth U10B**), games played, total points,
points per game, grade bonus, player rating, and a clickable PlayHQ Statistics
link. Rating is points per game plus the grade bonus. EDJBA bonuses are U12
A +28, B +22, C +18, D +14; U11 A +25, ARES +22, B +19, other +15; U10 A +22,
ARES +19, B +16, other +12; and U9 A +4, other +0. Kilsyth bonuses are U11 A
+25, ARES +22, B +19, other +16; U10 A +19, ARES +16, B +13, other +1; U9 A
+10, other +5; U12 A +25, other +19. GEBC bonuses are U11 A +25, ARES +22,
B +19, other +13; U10 A +16, ARES +13, B +10, other +1; U9 A +4, other +0;
U12 A +25, other +20. Ratings are shown to two decimal places only when a
statistics match has at least one game.
The report displays the grade bonus rules for Kilsyth, GEBC, and EDJBA above
the analysis table.
Rows are sorted from highest rating to lowest; rows with no rating appear
last. The first 20 rated players are assigned to Court 1, then the next 20
players to each of Courts 2 through 6. Players outside the top 120 or without
a rating have a blank court. The PlayHQ source link is the final column.
Other player-analysis fields (including birth dates, rankings, and evaluation
notes) are not read or included. Keep the generated HTML outside the
repository because it contains player names.

```powershell
$python = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
$source = "$env:USERPROFILE\Downloads\Player Analysis 20261001.xlsx"
$scraped = "$env:USERPROFILE\Downloads\kilsyth_u9_u11_boys.csv"
$vjblPlayers = "$PWD\vjbl_2027_u12_boys_scorers.json"
$gebcPlayers = "$env:USERPROFILE\Downloads\gebc_u9_u11_boys_2026_27.json"
$edjbaPlayers = "$env:USERPROFILE\Downloads\edjba_u10_u12_boys_2026_27.json"
$kilsythWinter = "$env:USERPROFILE\Downloads\kilsyth_u9_u11_boys_winter_2026.csv"
$gebcWinter = "$env:USERPROFILE\Downloads\gebc_u9_u11_boys_winter_2026.json"
$edjbaWinter = "$env:USERPROFILE\Downloads\edjba_u11_boys_winter_2026.json"
$kilsythWinterUrl = "https://www.playhq.com/basketball-victoria/org/kilsyth-basketball/junior-domestic-winter-2026/8ff9f39e"
$output = "$env:USERPROFILE\Downloads\Player Analysis 20261001 - Kilsyth Statistics.html"
& $python challenges\Kilsyth\scrape_gebc_u9_u11_boys.py --output $gebcPlayers
& $python challenges\Kilsyth\scrape_edjba_u10_u12_boys.py --output $edjbaPlayers
& $python challenges\Kilsyth\build_kilsyth_u9_u11_tryout_report.py --source-of-truth $source --scraped-players $scraped --vjbl-players $vjblPlayers --gebc-players $gebcPlayers --edjba-players $edjbaPlayers --tryouts-kilsyth-players $kilsythWinter --tryouts-gebc-players $gebcWinter --tryouts-edjba-players $edjbaWinter --tryouts-competition-url $kilsythWinterUrl --output $output
```

Chromium runs visibly by default because PlayHQ may block headless requests.
Add `--headless` to try a headless run.
