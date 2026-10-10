# Football Model Lab

An editable academic NFL and CFB score model, a searchable Streamlit dashboard,
and explicit-refresh data connectors. Python 3.11 or 3.12 is recommended.

## Start here

You can open the private web dashboard linked in the conversation immediately.
It starts with the original **11-game, October 10, 2026 research snapshot**.
Changing weights recomputes every loaded game. Select a matchup for its math,
all quoted markets, and injury/weather/line scenarios. Use **Data & setup** to
edit any team statistic or the complete input JSON. Export keeps your settings.

The private web dashboard computes in your browser. It does **not** automatically
download new statistics or odds. Its JSON import accepts larger slates exported
by this Python application. Browser edits remain on that browser/device.

## Run the Python dashboard

Extract this ZIP into a folder. Open a terminal in that folder and run:

```bash
python -m venv .venv
```

Activate it on Windows:

```powershell
.venv\Scripts\Activate.ps1
```

Or on macOS/Linux:

```bash
source .venv/bin/activate
```

Install and launch:

```bash
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Open the local address printed by Streamlit. This is an application, so do not
upload `app.py` through Colab's **Upload notebook** menu. That menu expects JSON
in an `.ipynb` file; a Python script beginning with `#!` produces the reported
JSON parsing error.

## Host it without running your laptop

1. Create a GitHub repository and upload the extracted project files. Preserve
   the folder structure; do not upload only the ZIP.
2. At <https://share.streamlit.io/>, select **Create app** and your repository.
3. Set the entrypoint to `app.py` and Python to 3.12, then deploy.
4. Set the application's audience/access settings for your intended viewers.
5. Add provider keys in Streamlit's **Advanced settings → Secrets**, or enter
   them in the dashboard's password fields for the current session.

Secret names:

```toml
CFBD_API_KEY = "your CollegeFootballData key"
ODDS_API_KEY = "your The Odds API key"
```

Use a local `.streamlit/secrets.toml` when running locally. That file and the
cache are ignored by Git. Do not put real keys in the repository, exported
snapshots, or browser dashboard JSON. The data pipeline never exports keys.

Official deployment instructions:
<https://docs.streamlit.io/deploy/streamlit-community-cloud/deploy-your-app/deploy>

## Load the full date window

In **Data & setup**, choose a season, first and last date, and leagues, then
select **Refresh schedules, statistics and odds**. A first NFL download may
take time. Subsequent pulls reuse cached data. Sliders do not make API calls.

| Source | Purpose | Access |
| --- | --- | --- |
| nflverse | NFL schedules and eligible prior-week play-by-play | Public download; no key |
| CollegeFootballData | CFB schedules, prior-week advanced statistics, team box scores and name aliases | CFBD key; endpoint access/quota depend on your plan |
| The Odds API | NFL/CFB individual book spreads, totals and American odds | Odds API key; access/quota depend on your plan |

The CFBD adapter covers regular-season games involving FBS teams. NFL refresh
covers regular-season schedules. Postseason and a complete FCS-only slate need
additional schedule/filter configuration. A date window can contain completed
games; those remain visible and may have no current quotes. First-week teams
have no current-season sample, so supply preseason inputs rather than treating
them as league-average teams without evidence.

The pipeline uses statistics through the week **before the earliest game week
in the selected window**. This conservative cutoff prevents the forecast
week's outcomes from entering its features. For windows spanning multiple
weeks, later games deliberately use the same earlier cutoff; refresh each week
for newer information.

The window is interpreted in America/New_York. Injury, weather, unusual travel,
and extra-rest point modifiers are manual and are marked unverified on new
data pulls. The current turnover feature is regressed net observed turnovers;
an empirical fumble-luck decomposition is not implemented.

Games with missing team inputs remain in the table. Scores are withheld until
the eight metrics are complete. Games without usable odds can receive a score
projection but no EV ranking. Provider team names are joined using exact
normalized aliases; ambiguous or unmatched names are not guessed. A provider
can omit quotes for some scheduled games.

The multi-book median is a reference line. It is not paired with an invented
price. EV is evaluated on each actual book quote, then the best conditional EV
market per game is used for ranking. Both sides and all books remain in the
matchup detail. Keep quote timestamps and check for stale lines.

The public NFL loader was exercised for October 10–12, 2026: it returned all
14 scheduled games and complete score projections for each. The key-protected
adapters have fixture checks; authenticated CFBD/Odds refresh was not exercised
without your keys. Verify those connections in your deployment. If a provider
changes its schema, the app displays an error or
missing inputs instead of filling them with invented statistics.

Official data documentation:

- <https://nflreadr.nflverse.com/reference/load_schedules.html>
- <https://nflreadr.nflverse.com/reference/load_pbp.html>
- <https://github.com/CFBD/cfbd-python/blob/main/docs/GamesApi.md>
- <https://github.com/CFBD/cfbd-python/blob/main/docs/StatsApi.md>
- <https://the-odds-api.com/liveapi/guides/v4/>

## Refresh from a terminal

With provider keys in environment variables named `CFBD_API_KEY` and
`ODDS_API_KEY`:

```bash
python data_pipeline.py --season 2026 --start 2026-10-10 --end 2026-10-12 --output full_slate.json
```

For NFL only, add `--leagues NFL`. Exported `full_slate.json` can be imported
into either dashboard. This is an explicit refresh; unattended scheduling is
not configured in this package.

## Edit anything

- Weight controls: YPP, success rate, explosiveness, shrinkage, turnover
  retention/value, baseline, home field, residual sigmas and reference metrics.
- Matchup controls: spread/total, each side's odds, injuries, rest, weather and
  neutral venue. Changing a price creates a clearly labeled manual scenario.
- Team controls: `y`, `dy`, `s`, `ds`, `x`, `dx`, `p`, `t`; changes affect every
  game using that team.
- JSON controls: add teams/games, prices, notes, metadata and parameters.
- Code: `model.py` owns the equations; `data_pipeline.py` owns retrieval and
  feature definitions; `app.py` owns the Python interface.

Exports use the same format as `snapshot.json`. Team keys are arbitrary unique
identifiers. Fresh pulls prefix them with `NFL:` or `CFB:` to prevent collisions.
Every team needs `league` plus the eight numeric metrics before it can be scored.
Success rates and NFL explosive rates are percentage points (44, not .44).
CFB explosiveness uses the CFBD publisher index, not the NFL percentage.

## Make it a scientifically defensible predictive model

Uploading the script creates a running application; it does not train or
calibrate the model. The original coefficients, adjustment points, and error
sigmas are **instructional priors**, not historical estimates. A high modeled
EV value is not evidence of profitable or more accurate predictions.

Use this staged plan:

1. **Capture pregame data.** Store every snapshot with retrieval time, metric
   cutoff, quote time, provider, venue, sample size, final scores, and a model
   version. Keep old snapshots unchanged. Historical final-season aggregates
   cannot be used as pregame features for earlier weeks.
2. **Construct comparable features.** Use one eligible play set per league,
   account for opponent strength, and shrink small samples toward preseason
   priors. CFB box-score YPP and advanced efficiency still use different play
   filters; harmonizing them from play-by-play is a production improvement.
3. **Fit separate league models.** Start with regularized regressions for
   game margin and total or points per drive plus pace. Fit correlated YPP,
   success and explosiveness jointly. Learn home-field/rest terms, turnover
   skill versus recovery luck, and documented availability effects from data.
4. **Validate chronologically.** Fit on older seasons, tune on subsequent
   seasons, and hold out the most recent season. Roll the training window
   forward. Keep both teams from the same game in the same fold.
5. **Measure prediction error.** Compare margin/total MAE and RMSE with a
   timestamp-matched market baseline and a simple team-strength baseline.
   Report NFL and CFB separately, with uncertainty and sample sizes.
6. **Calibrate probabilities.** Estimate residuals on held-out predictions;
   model football's discrete scoring masses and pushes. Evaluate Brier score,
   log loss and reliability diagrams before interpreting EV.
7. **Validate selection.** Select thresholds on validation data only. Track
   calibration and errors for the selected games, odds movement, sensitivity
   to weight/injury changes, and repeated selection across many games/books.
8. **Operate consistently.** Move immutable inputs/results into a database,
   add scheduled refresh with failure alerts, and keep a changelog. Sliders
   should produce named scenarios alongside a locked validated baseline.

The package implements the editable application and initial data adapters.
Historical fitting, opponent adjustment, calibration, durable database storage,
automated injury/weather ingestion and unattended refresh are future stages.
They are described here rather than represented as already working features.

## Verification

```bash
python -m unittest discover -s tests -v
```

Checks cover the original 11-game calculation parity, probability/push
arithmetic, quote-specific EV, missing-input behavior, prior-week feature
cutoffs, and fixture-based odds joins. Additional development-time checks use
Streamlit's AppTest and parity checks against the private dashboard's JavaScript.
