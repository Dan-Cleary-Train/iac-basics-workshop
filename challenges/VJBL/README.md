# VJBL U12 v1.2 - 2027 results and handoff

## Deliverables

- [VJBL U12 v1.2 report](../../VJBL_U12_v1.2.html)
- [Round 1 game data](../../vjbl_2027_u12_boys_game_results.json)
- [Historical rankings data, 2023–2026](./historical_rankings.json)
- [PlayHQ scraper](./scrape_vjbl_u12_boys.py)
- [Historical rankings updater](./update_historical_rankings.py)
- [South Pen game scraper](./scrape_south_pen_u12_boys.py)
- [South Pen ladder scraper](./scrape_south_pen_ladders.py)
- [South Pen report updater](./update_south_pen_report.py)
- [South Pen 2026 data](./south_pen_u12_boys_2026.json)
- [Tryout player analysis and competition lookup](../Kilsyth/README.md)

The report is version 1.2 and has six competition tabs: South Pen, 12 Boys Grading 1, 12 Boys Crossover 1, 12 Boys Grading 2, 12 Boys Crossover 2, and Australia Day Tournament, followed by a global Rankings tab. VJBL competitions have nested Games, Top scorers, and Standings tabs. South Pen has Games, Top scorers, and an official PlayHQ Ladder view.

The separate tryout analysis uses the VJBL U12 Boys Round 1 player snapshot
only for exact normalized-name team lookups; it does not use this report's
rankings as a player rating.

The HTML report is self-contained: it embeds the game data and can be shared on its own. Shared division, pool, team, and player filters sit above the competition tabs and Rankings and can be reset with Clear all filters. Division and pool filters apply to VJBL views only. Team and player searches also filter South Pen games to matches; South Pen top scorers include all players in each grade with matching games, and its official ladders are not filtered. For VJBL, division selections scope all views; the pool selector scopes Games and Standings, but not Top scorers. Team searches show only matching games; Standings shows the full pool(s) those teams belong to, while Top scorers covers their full division(s), across all pools. Player searches resolve the matching division/pool scope(s) and show all games and standings for those scopes; Top scorers covers their full division(s), across all pools. The PlayHQ game links need an internet connection.

VJBL Standings are calculated from completed game scores in the embedded data. A win is worth 3 premiership points, a draw 2, and a loss 1. Teams are ordered by premiership points, then head-to-head premiership points and head-to-head percentage among tied teams, then overall pool percentage (points for ÷ points against × 100). Exact remaining ties are ordered alphabetically by team name. Tables show games played, premiership points, percentage, wins, losses, draws, points for, and points against.

The Rankings tab has separate **Round rankings** and **Historical rankings** views. Round rankings are calculated from the embedded VJBL grading game data only. Australia Day Tournament and South Pen results are excluded from all rankings. Choose a round to rank cumulative results through that round. Division 1 ranks before Division 2 and so on; within each division, teams are ranked by points-for-to-points-against percentage, with premiership points and team name as tie-breakers. The Change column compares each team's overall position with the previous available round (up/down places, unchanged, or New). Division, pool, and search filters narrow the displayed rows without changing the rank. The current snapshot contains only Round 1, so movement will become meaningful when later round results are added.

Historical rankings integrate the separate VJBL Rankings work and compare seasons **2023, 2024, 2025, and 2026**. The report embeds the season ranks and also keeps the normalized snapshot in [historical_rankings.json](./historical_rankings.json). VC and VC Reserve lead the division order, followed by VJL divisions and then RDL/RL; P1/P2/P3 and regional competitions are combined. Teams are ordered within a division by wins, fewer losses, then percentage. Overall-rank movement is compared with the previous available season; upward movement is green, downward movement red, and unavailable or unchanged movement black. Historical rankings have separate Team, Year, and Division filters because those division labels and season ranks are independent of the current-round grading divisions. The view defaults to All years; choose a specific year to focus on one season. The all-years table scrolls horizontally on smaller screens.

To refresh the integrated history after scraping or updating seasons in the separate VJBL Rankings project, run this from the repository root:

```powershell
$python = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
$historicalData = "C:\GIT\vjbl-rankings\vjbl_u12_boys_ladders.json"
& $python challenges\VJBL\update_historical_rankings.py $historicalData
```

The updater writes the normalized JSON snapshot and refreshes the report’s embedded data, keeping the HTML self-contained.

## South Pen tournament

The South Pen tab covers U12 Boys only, matching the scope of this report. It includes the Championship and Divisions 1–13. Games and player points include completed pool and finals games. Top scorers are totaled per player and team within each grade. The snapshot contains 240 completed games and 4,376 player-stat rows; the Ladder view preserves the official PlayHQ standings for 28 pools and 113 teams. These tournament results do not affect either ranking view.

To refresh the snapshot from the repository root:

```powershell
$python = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
$competition = "https://www.playhq.com/basketball-victoria/org/southern-peninsula-tournament/new-balance-representative-tournament-2026/0671b6cf"
$gameData = "$env:TEMP\south_pen_u12_boys_2026_games.json"
& $python challenges\VJBL\scrape_south_pen_u12_boys.py $competition --output $gameData
& $python challenges\VJBL\scrape_south_pen_ladders.py $gameData --output challenges\VJBL\south_pen_u12_boys_2026.json
& $python challenges\VJBL\update_south_pen_report.py
```

If game scraping is interrupted, rerun the first command with `--resume` to skip grades already saved in the partial JSON. The ladder scraper and report updater reject incomplete snapshots.

## Data snapshot

Scraped on 21 November 2026. The current data is for Round 1 of 12 Boys Grading 1, across all 64 grades in Divisions 1–16: 124 completed games and 2,320 player rows. Rounds 2 and 3 were upcoming at the time, so they have no completed games in this snapshot.

Final scores were checked against quarter totals and player points where available. Where PlayHQ records points in a separate team-level statistics row, the report shows those separately; they are not attributed to an individual player in the leaderboard. Two South Pen games had no allocated player line-ups; they use the PlayHQ scoreboard and explicitly show that player statistics are unavailable.

## Running the scraper

Python 3.12 and Playwright are installed for the current Windows user. From the repository root, run the scraper in PowerShell:

```powershell
$python = "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe"
$competition = "https://www.playhq.com/basketball-victoria/org/victorian-junior-basketball-league/2027/e5908800"
$division = 10
& $python challenges\VJBL\scrape_vjbl_u12_boys.py $competition --round 1 --division $division --output "$env:TEMP\vjbl_division_$($division).json"
```

Change `$division` to another division number as needed. Omit `--division` to request all divisions, or `--round` to collect all rounds. Chromium runs visibly by default; PlayHQ may block headless requests.

The complete snapshot was scraped in division-sized batches because PlayHQ pages can intermittently fail to load when many grades are processed in one run. The scraper writes JSON only; it does not rebuild the HTML report. The existing report remains a snapshot until its embedded data is regenerated.
