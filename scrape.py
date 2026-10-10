"""Fort Campbell iSportsman check-in snapshot.

Opens the public Areas page in headless Chromium (the area list is drawn by
JavaScript, so a plain HTTP request won't see it), reads the deer-hunting
listings, and appends one row per training area to data/snapshots.csv.

Columns match the Campbell Pressure Log import: date,time,area,hunters,capacity,open
"""
import csv
import os
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

URL = "https://ftcampbell.isportsman.net/Areas.aspx"
TZ = ZoneInfo("America/Chicago")
OUT = "data/snapshots.csv"
RAW = "data/latest_page.txt"

# Activities that count as deer-hunting pressure
DEER = ("Deer Hunting", "Doe Only Hunting", "Youth Hunt", "Cantonment Area", "Handicap Area")

COUNT_RE = re.compile(r"^(\d+)\s+checked in(?:\s*\((\d+)\s+max\))?", re.I)


def norm_area(name: str) -> str:
    a = re.sub(r"\(.*?\)", "", name)
    a = re.sub(r"\s+-.*$", "", a).strip().upper()
    a = re.sub(r"^(TA|TRAINING AREA|AREA)\s*", "", a)
    a = re.sub(r"\s+", "", a)
    return re.sub(r"^0+(\d)", r"\1", a)


def parse(lines):
    """Walk the page text in order: '<Activity> Parent', area, Open/Closed, 'Area', 'N checked in (M max)'."""
    recs, cur = [], None
    for raw in lines:
        v = raw.strip()
        if not v:
            continue
        if v.endswith("Parent"):
            cur = {"act": v[: -len("Parent")].strip()}
            recs.append(cur)
            continue
        if cur is None:
            continue
        if "area" not in cur:
            cur["area"] = v
        elif v in ("Open", "Closed"):
            cur["status"] = v
        elif v == "Area":
            pass
        else:
            m = COUNT_RE.match(v)
            if m and "used" not in cur:
                cur["used"] = int(m.group(1))
                cur["cap"] = int(m.group(2)) if m.group(2) else None
    return recs


def aggregate(recs):
    """One row per area. Only open listings count: closed ones carry a different, irrelevant limit."""
    areas = {}
    for r in recs:
        if not r["act"].startswith(DEER) or "area" not in r:
            continue
        a = norm_area(r["area"])
        o = areas.setdefault(a, {"used": 0, "cap": 0, "open": False})
        if r.get("status") != "Open":
            continue
        o["open"] = True
        o["used"] += r.get("used") or 0
        o["cap"] += r.get("cap") or 0
    return areas


def main():
    now = datetime.now(TZ)
    # GitHub schedules run in UTC; the workflow fires at both CDT and CST times and we keep the one that lands at 7 or 16 local.
    if os.environ.get("GITHUB_EVENT_NAME") == "schedule" and now.hour not in (7, 16):
        print(f"Skipping: {now:%H:%M} Central isn't a snapshot hour.")
        return

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page()
        page.goto(URL, wait_until="networkidle", timeout=90_000)
        try:
            page.wait_for_function("document.body.innerText.includes('checked in')", timeout=60_000)
        except Exception:
            pass
        text = page.inner_text("body")
        browser.close()

    os.makedirs("data", exist_ok=True)
    with open(RAW, "w") as f:
        f.write(text)

    recs = parse(text.splitlines())
    areas = aggregate(recs)
    if not areas:
        print("No deer-hunting listings found on the page. See data/latest_page.txt.")
        sys.exit(1)

    new_file = not os.path.exists(OUT)
    with open(OUT, "a", newline="") as f:
        w = csv.writer(f)
        if new_file:
            w.writerow(["date", "time", "area", "hunters", "capacity", "open"])
        for a, o in sorted(areas.items()):
            w.writerow([f"{now:%Y-%m-%d}", f"{now:%H:%M}", a, o["used"], o["cap"] or "", "yes" if o["open"] else "no"])

    opened = [o for o in areas.values() if o["open"]]
    fills = [o["used"] / o["cap"] for o in opened if o["cap"]]
    avg = f"{100 * sum(fills) / len(fills):.0f}%" if fills else "n/a"
    print(f"{now:%Y-%m-%d %H:%M}: {len(areas)} areas, {len(opened)} open, "
          f"{sum(o['used'] for o in opened)} hunters, average fill {avg}")


if __name__ == "__main__":
    main()
