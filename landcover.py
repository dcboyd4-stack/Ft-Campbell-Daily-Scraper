"""Land cover per Fort Campbell training area from the USGS National Land Cover Database (NLCD).

The 2024 Hunting & Fishing Map is a GeoPDF: its corners carry latitude/longitude (in the PDF's
/GPTS entry, Tennessee State Plane projection). That lets us place every pixel of the map image
(gis/zones.png, 2000×1318, one value per training area) on the ground, look up the NLCD land-cover
class there, and summarize it per zone.

Outputs (committed back to the repo):
  gis/habitat.json   per-zone acres and land-cover shares, forest/opening edge density
  gis/landcover.png  transparent overlay in NLCD colors, aligned to the map image
  gis/check.jpg      the map with the overlay blended in, for checking alignment
  gis/source.txt     which NLCD layer was used
"""
import io
import json
import re
import sys

import numpy as np
import rasterio
import requests
from PIL import Image
from pyproj import Transformer

W, H = 2000, 1318
# GeoPDF viewport: PDF-space bounding box and its corner coordinates (lat, lon), in LPTS order
# (0,0) bottom-left, (0,1) top-left, (1,1) top-right, (1,0) bottom-right
PAGE_W, PAGE_H = 913.038, 601.5376
BBOX = (0.5, 0.5, 912.5384, 601.0376)
GPTS = [(36.53058, -87.82062), (36.74379, -87.82555), (36.74916, -87.4232), (36.53595, -87.41936)]
LPTS = [(0, 0), (0, 1), (1, 1), (1, 0)]
MAP_CRS = "EPSG:2274"   # NAD83 / Tennessee (ftUS), as declared in the GeoPDF
NLCD_CRS = "EPSG:5070"  # CONUS Albers, NLCD's native grid

WCS = "https://www.mrlc.gov/geoserver/mrlc_download/wcs"

# NLCD classes grouped the way a deer hunter reads them
GROUPS = {
    "hardwood": [41], "evergreen": [42], "mixed": [43],
    "shrub": [52], "grass": [71], "pasture": [81], "crops": [82],
    "wetland": [90, 95], "water": [11], "developed": [21, 22, 23, 24], "barren": [31],
}
FOREST = {41, 42, 43, 90}       # woody cover, including wooded wetland
OPENING = {52, 71, 81, 82}      # shrub, grass/old field, hay/pasture, crops
COLORS = {11: (70, 107, 159), 21: (222, 197, 197), 22: (217, 146, 130), 23: (235, 0, 0), 24: (171, 0, 0),
          31: (179, 172, 159), 41: (104, 171, 95), 42: (28, 95, 44), 43: (181, 197, 143), 52: (204, 184, 121),
          71: (223, 223, 194), 81: (220, 217, 57), 82: (171, 108, 40), 90: (184, 217, 235), 95: (108, 159, 184)}


def map_grid():
    """Projected (map CRS) coordinates of every pixel center of the 2000×1318 map image."""
    to_map = Transformer.from_crs("EPSG:4269", MAP_CRS, always_xy=True)
    corners = np.array([to_map.transform(lon, lat) for lat, lon in GPTS])
    # affine from unit square (u, v) to projected (x, y), least squares over the 4 corners
    A = np.array([[u, v, 1] for u, v in LPTS], float)
    cx, *_ = np.linalg.lstsq(A, corners[:, 0], rcond=None)
    cy, *_ = np.linalg.lstsq(A, corners[:, 1], rcond=None)
    i, j = np.meshgrid(np.arange(W) + 0.5, np.arange(H) + 0.5)
    pdf_x = i * PAGE_W / W
    pdf_y = PAGE_H - j * PAGE_H / H
    u = (pdf_x - BBOX[0]) / (BBOX[2] - BBOX[0])
    v = (pdf_y - BBOX[1]) / (BBOX[3] - BBOX[1])
    X = cx[0] * u + cx[1] * v + cx[2]
    Y = cy[0] * u + cy[1] * v + cy[2]
    px_ft = float(np.hypot(X[0, 1] - X[0, 0], Y[0, 1] - Y[0, 0]))
    return X, Y, px_ft


def pick_coverage():
    caps = requests.get(WCS, params={"service": "WCS", "version": "2.0.1", "request": "GetCapabilities"}, timeout=120).text
    ids = re.findall(r"<wcs:CoverageId>([^<]+)</wcs:CoverageId>", caps)
    land = [c for c in ids if re.search(r"(LndCov|Land_Cover)", c, re.I) and re.search(r"(L48|CU|CONUS)", c, re.I)
            and not re.search(r"(Change|Conf|Fct|Index|Science|Imperv|Canopy|SpcChg|Disturb)", c, re.I)]
    def year(c):
        m = re.findall(r"(19|20)(\d\d)", c)
        return int(m[-1][0] + m[-1][1]) if m else 0
    land.sort(key=year)
    print(f"{len(ids)} coverages offered; land-cover candidates (oldest→newest): {land[-6:]}")
    if not land:
        sys.exit("No NLCD land-cover coverage found in the WCS capabilities.")
    return land[-1], year(land[-1])


def fetch_nlcd(cov, X, Y):
    to_alb = Transformer.from_crs(MAP_CRS, NLCD_CRS, always_xy=True)
    ax, ay = to_alb.transform(X, Y)
    pad = 300
    x0, x1, y0, y1 = ax.min() - pad, ax.max() + pad, ay.min() - pad, ay.max() + pad
    params = [("service", "WCS"), ("version", "2.0.1"), ("request", "GetCoverage"), ("CoverageId", cov),
              ("subset", f"X({x0:.0f},{x1:.0f})"), ("subset", f"Y({y0:.0f},{y1:.0f})"), ("format", "image/geotiff")]
    r = requests.get(WCS, params=params, timeout=300)
    if r.status_code != 200 or not r.content[:4] in (b"II*\x00", b"MM\x00*"):
        sys.exit(f"NLCD download failed ({r.status_code}): {r.text[:400]}")
    with rasterio.open(io.BytesIO(r.content)) as src:
        arr = src.read(1)
        rows, cols = rasterio.transform.rowcol(src.transform, ax.ravel(), ay.ravel())
        print(f"NLCD tile {arr.shape} in {src.crs}, resolution {src.res}")
    rows, cols = np.asarray(rows).reshape(ax.shape), np.asarray(cols).reshape(ax.shape)
    inside = (rows >= 0) & (rows < arr.shape[0]) & (cols >= 0) & (cols < arr.shape[1])
    lc = np.zeros(ax.shape, np.uint8)
    lc[inside] = arr[rows[inside], cols[inside]]
    return lc


def main():
    zones = json.load(open("gis/zones.json"))["zones"]
    lab = np.array(Image.open("gis/zones.png"))
    X, Y, px_ft = map_grid()
    acres_per_px = (px_ft ** 2) / 43560
    print(f"map pixel ≈ {px_ft * 0.3048:.1f} m")
    cov, yr = pick_coverage()
    lc = fetch_nlcd(cov, X, Y)

    forest = np.isin(lc, list(FOREST))
    opening = np.isin(lc, list(OPENING))
    # forest/opening edge: forest pixels with an opening pixel as a 4-neighbor
    nb_open = np.zeros_like(opening)
    nb_open[1:, :] |= opening[:-1, :]; nb_open[:-1, :] |= opening[1:, :]
    nb_open[:, 1:] |= opening[:, :-1]; nb_open[:, :-1] |= opening[:, 1:]
    edge_px = forest & nb_open

    out = {}
    for k, name in zones.items():
        z = lab == int(k)
        n = int(z.sum())
        if not n:
            continue
        vals = lc[z]
        known = vals[vals > 0]
        tot = max(1, known.size)
        rec = {"acres": round(n * acres_per_px)}
        for g, codes in GROUPS.items():
            rec[g] = round(float(np.isin(known, codes).sum()) / tot, 3)
        sqmi = n * acres_per_px / 640
        rec["edge"] = round(float(edge_px[z].sum()) * px_ft / 5280 / sqmi, 2)  # miles of forest/opening edge per sq mi
        out[name] = rec
    json.dump({"source": f"USGS NLCD {yr} ({cov}), 30 m", "zones": out}, open("gis/habitat.json", "w"), indent=1)
    open("gis/source.txt", "w").write(f"{cov}\n")

    rgba = np.zeros((H, W, 4), np.uint8)
    for code, col in COLORS.items():
        m = lc == code
        rgba[m, :3] = col
        rgba[m, 3] = 255
    rgba[lab == 0, 3] = 0
    Image.fromarray(rgba).save("gis/landcover.png", optimize=True)

    base = Image.open("ftc-map.jpg").convert("RGB").resize((W, H))
    over = Image.fromarray(rgba)
    chk = base.copy()
    chk.paste(Image.blend(base, over.convert("RGB"), 0.55), mask=over.split()[3])
    chk.save("gis/check.jpg", quality=82)
    print(f"Wrote {len(out)} zones from {cov}")


if __name__ == "__main__":
    main()
