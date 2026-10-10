"""
Synthetic Souf oasis with known answers, used ONLY to verify the logic of notebooks/main.ipynb.
It is never used to produce any result reported in the README.

On import it replaces the Planetary Computer catalogue (pystac_client, planetary_computer) and the
loader (odc.stac.load) with offline stand-ins that return Sentinel-2-like digital numbers, including the
processing-baseline offset of +1000 from 25 January 2022, SCL cloud classes and random cloud blobs.

The oasis has TWO crop calendars, as in El Oued:
  late season  (arriere-saison): emerges ~September, harvested ~December
  main season  (premiere):       emerges ~January,   harvested ~May
Kinds of plot (day 0 = 1 August of the agricultural year):
  palm         perennial, nearly flat NDVI
  late_ok      late-season crop, normal ~95-day green season
  late_fail    late-season crop, season ends ~40 days early
  main_ok      main-season crop, normal ~115-day green season
  main_fail    main-season crop, season ends ~45 days early
  double       late_ok followed by main_ok on the same pivot
  dead         sparse vegetation, never a real crop
Leaf water (NDMI) is made to lead greenness by ~8 days on failing crops and ~2 days on normal ones,
so the notebook's group test must recover a stress-specific lead when one exists.
Some late-season plots switch to the main season in later years (exercises the cultivated-land figure).
The first two catalogue searches fail with the Planetary Computer rate-limit message (exercises the retry).
"""
import os
if os.environ.get("ASTRA_SYNTHETIC_CHECK") != "1":
    raise RuntimeError(
        "synthetic_oasis.py replaces the satellite catalogue with FAKE data and must never be run inside the "
        "analysis notebook. Run the known-answer test from a terminal at the repository root instead:\n"
        "    python tests/run_synthetic_check.py notebooks/main.ipynb\n"
        "(in Colab, put ! in front of that command).")
import sys, types, datetime as dt
import numpy as np
import xarray as xr
from odc.geo.geobox import GeoBox
from odc.geo.geom import BoundingBox
from odc.geo.xr import xr_zeros

RNG = np.random.default_rng(11)
UTM = "EPSG:32632"
WORLD_BBOX = [6.903, 33.422, 7.019, 33.523]

_bb = BoundingBox(*WORLD_BBOX, crs="EPSG:4326").to_crs(UTM)
N = 800
cx = RNG.uniform(_bb.left + 200, _bb.right - 200, N)
cy = RNG.uniform(_bb.bottom + 200, _bb.top - 200, N)
rad = RNG.uniform(40, 200, N)
kind = RNG.choice(["palm", "late_ok", "late_fail", "main_ok", "main_fail", "double", "dead"], N,
                  p=[0.20, 0.32, 0.08, 0.21, 0.06, 0.08, 0.05])
est_year = RNG.integers(2016, 2026, N); est_year[kind == "palm"] = 2010
switch_year = np.where((kind == "late_ok") & (RNG.random(N) < 0.35), RNG.integers(2021, 2026, N), 9999)
amp = RNG.uniform(0.45, 0.70, N)
g_late = RNG.normal(40, 8, N)
g_main = RNG.normal(170, 10, N)
len_late_ok, len_late_fail = RNG.normal(95, 7, N), RNG.normal(55, 6, N)
len_main_ok, len_main_fail = RNG.normal(115, 7, N), RNG.normal(70, 6, N)
SOIL = 0.08

def _s(x):
    return 1 / (1 + np.exp(-x))

def _cycle(d, g, length, a):
    return a * _s((d - g) / 5) * (1 - _s((d - (g + length)) / 5))

def ndvi_curve(day, year):
    d = np.full(N, float(day))
    k = np.where((kind == "late_ok") & (year >= switch_year), "main_ok", kind)
    v = np.full(N, SOIL)
    m = k == "palm";      v[m] = 0.34 + 0.015 * np.sin(d[m] / 25)
    m = k == "late_ok";   v[m] += _cycle(d[m], g_late[m], len_late_ok[m], amp[m] - SOIL)
    m = k == "late_fail"; v[m] += _cycle(d[m], g_late[m], len_late_fail[m], amp[m] - SOIL)
    m = k == "main_ok";   v[m] += _cycle(d[m], g_main[m], len_main_ok[m], amp[m] - SOIL)
    m = k == "main_fail"; v[m] += _cycle(d[m], g_main[m], len_main_fail[m], amp[m] - SOIL)
    m = k == "dead";      v[m] = 0.17 + _cycle(d[m], g_late[m], 60, 0.14)  # sparse, never a real crop
    m = k == "double"
    v[m] += _cycle(d[m], g_late[m], len_late_ok[m], amp[m] - SOIL) + \
            _cycle(d[m], g_main[m], len_main_ok[m], amp[m] - SOIL)
    return v

def ndmi_curve(day, year):
    lead = np.where(np.isin(kind, ["late_fail", "main_fail"]), 8.0, 2.0)
    return 0.85 * _shifted(day, year, lead) - 0.20

def _shifted(day, year, lead):
    out = np.empty(N)
    for L in np.unique(lead):
        sel = lead == L
        out[sel] = ndvi_curve(day + L, year)[sel]
    return out

_pid_cache = {}
def plot_raster(gbox):
    key = (tuple(gbox.shape), float(gbox.resolution.x))
    if key not in _pid_cache:
        base = xr_zeros(gbox, dtype="int16")
        X, Y = np.meshgrid(base.x.values, base.y.values)
        pid = np.full(X.shape, -1, dtype=np.int32)
        for i in range(N):
            pid[(X - cx[i]) ** 2 + (Y - cy[i]) ** 2 <= rad[i] ** 2] = i
        _pid_cache[key] = (pid, base)
    return _pid_cache[key]

def agri_info(date):
    year = date.year if date.month <= 7 else date.year + 1
    return year, (date - dt.date(year - 1, 8, 1)).days

class APIError(Exception):
    pass

RATE_LIMIT_FAILURES = {"remaining": 2}

class FakeItem:
    def __init__(self, when, tile):
        self.datetime = dt.datetime.combine(when, dt.time(10, 20), tzinfo=dt.timezone.utc)
        self.id = f"S2_{when:%Y%m%d}_{tile}"
        base = "05.09" if when >= dt.date(2022, 1, 25) else "02.14"
        self.properties = {"datetime": self.datetime.isoformat(), "s2:processing_baseline": base}

class _Search:
    def __init__(self, datetime):
        a, b = datetime.split("/")
        d0, d1 = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
        self._items, d, k = [], d0 + dt.timedelta(days=1), 0
        while d <= d1:
            for tile in range(4):
                self._items.append(FakeItem(d, tile))
            d += dt.timedelta(days=int(3 + (k % 3))); k += 1
    def items(self):
        return iter(self._items)

class _Client:
    @staticmethod
    def open(url, modifier=None):
        return _Client()
    def search(self, collections=None, bbox=None, datetime=None, query=None, **kw):
        if RATE_LIMIT_FAILURES["remaining"] > 0:
            RATE_LIMIT_FAILURES["remaining"] -= 1
            raise APIError("You have exceeded a rate limit. Contact planetarycomputer@microsoft.com")
        return _Search(datetime)

def load(items, bands=None, bbox=None, resolution=None, crs=None, groupby=None, geobox=None, **kw):
    if geobox is None:
        geobox = GeoBox.from_bbox(BoundingBox(*bbox, crs="EPSG:4326").to_crs(crs), resolution=resolution)
    pid, base = plot_raster(geobox)
    dates = sorted({it.datetime.date() for it in items})
    offsets = {it.datetime.date(): (1000 if float(it.properties["s2:processing_baseline"]) >= 4 else 0)
               for it in items}
    T, (H, W) = len(dates), pid.shape
    arr = {b: np.zeros((T, H, W), dtype="uint16") for b in bands}
    inside = pid >= 0
    for t, d in enumerate(dates):
        yr, day = agri_info(d)
        active = est_year <= yr
        ok = inside & active[np.clip(pid, 0, None)]
        v = np.full((H, W), SOIL); m = np.full((H, W), -0.15)
        v[ok] = ndvi_curve(day, yr)[pid[ok]]
        m[ok] = ndmi_curve(day, yr)[pid[ok]]
        nir = 0.30 + 0.20 * v + RNG.normal(0, 0.006, (H, W))
        red = nir * (1 - v) / (1 + v)
        swir = nir * (1 - m) / (1 + m)
        green, blue = red * 0.85 + 0.05 * v, red * 0.70
        scl = np.where(v > 0.3, 4, 5).astype("uint16")
        if RNG.random() < 0.25:
            yy, xx = np.ogrid[:H, :W]
            cyb, cxb, rb = RNG.integers(0, H), RNG.integers(0, W), RNG.integers(30, 140)
            cloud = (yy - cyb) ** 2 + (xx - cxb) ** 2 <= rb ** 2
            for x in (red, nir, swir, green, blue):
                x[cloud] = 0.55
            scl[cloud] = 9
        refl = {"B02": blue, "B03": green, "B04": red, "B08": nir, "B11": swir}
        for b in bands:
            arr[b][t] = scl if b == "SCL" else np.clip(np.round(refl[b] * 10000 + offsets[d]), 0, 65535)
    coords = dict(base.coords.items())
    coords["time"] = np.array([np.datetime64(d) for d in dates], dtype="datetime64[ns]")
    return xr.Dataset({b: (("time", "y", "x"), arr[b]) for b in bands}, coords=coords)

m_pc = types.ModuleType("planetary_computer"); m_pc.sign_inplace = lambda x: x
m_ps = types.ModuleType("pystac_client"); m_ps.Client = _Client
m_ex = types.ModuleType("pystac_client.exceptions"); m_ex.APIError = APIError; m_ps.exceptions = m_ex
m_os = types.ModuleType("odc.stac"); m_os.load = load
sys.modules.update({"planetary_computer": m_pc, "pystac_client": m_ps,
                    "pystac_client.exceptions": m_ex, "odc.stac": m_os})
import odc, time as _time
odc.stac = m_os
SLEEPS = []
_time.sleep = lambda s: SLEEPS.append(s)
