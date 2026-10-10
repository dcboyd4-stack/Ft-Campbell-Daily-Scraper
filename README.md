# Campbell pressure scraper

Takes a snapshot of Fort Campbell deer-hunting check-ins from the public iSportsman Areas page at about 7:45 AM and 4:45 PM Central, September through January, and saves it to `data/snapshots.csv`. It runs on GitHub's servers, free, whether your computer is on or not.

## Set it up (about 10 minutes, all in the browser)

1. Sign in to github.com (or create a free account).
2. Create a new repository named `campbell-pressure`. Make it **Public**. The data is already public on iSportsman, and a public repo gets unlimited free run time.
3. On the new repo's page, choose **Add file → Upload files** and upload `scrape.py`. Commit.
4. Choose **Add file → Create new file**. In the name box type `.github/workflows/snapshot.yml` (typing the slashes creates the folders). Paste in the contents of `snapshot.yml`. Commit.
5. Open the **Actions** tab. If GitHub asks, click to enable workflows.
6. Click **Pressure snapshot → Run workflow** to run a test right now.

After a minute or two the run should show a green check, and the repo will have a `data` folder with `snapshots.csv` and `latest_page.txt`. If the run fails, `latest_page.txt` shows what the scraper saw on the page.

## How the numbers are counted

- Only deer-hunting activities count: Deer Hunting, Deer Hunting - Archery Only, Doe Only Hunting, Youth Hunt, Cantonment Area and Handicap Area.
- Each area's archery and doe-only listings are merged into the area itself (`TA 09B (ARCHERY)` becomes `9B`).
- Only open listings count toward hunters and the hunter limit. Closed listings carry a different limit that doesn't apply that day.

## Things to know

- GitHub runs scheduled jobs a few minutes to half an hour late at busy times.
- GitHub pauses schedules on repos with no activity for 60 days. Each snapshot commits new data, so this stays active during the season. After January, the schedule stops on its own; if GitHub has paused it by next September, re-enable it from the Actions tab.
