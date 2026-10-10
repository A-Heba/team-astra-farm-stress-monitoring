"""
Known-answer check of notebooks/ASTRA_PoC.ipynb on a synthetic oasis (see synthetic_oasis.py).

Usage, from a terminal at the repository root, after installing requirements.txt
(in Colab, put ! in front of the command):
    python tests/run_synthetic_check.py notebooks/ASTRA_PoC.ipynb

No network access or credentials are needed. The notebook's code cells are executed unchanged, except that
shell lines (pip installs) are skipped and figures are not displayed. Outputs are written to
tests/synthetic_outputs/. The script then compares each assessed plot's category with the behaviour that
was simulated for it.
"""
import contextlib
import io
import json
import os
import sys

try:
    HERE = os.path.dirname(os.path.abspath(__file__))
except NameError:
    raise SystemExit("This is a script, not a notebook cell. From the repository root run:\n"
                     "    python tests/run_synthetic_check.py notebooks/ASTRA_PoC.ipynb\n"
                     "(in Colab, put ! in front of that command).")
sys.path.insert(0, HERE)
os.environ["ASTRA_SYNTHETIC_CHECK"] = "1"      # unlocks synthetic_oasis.py for this run only

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
plt.show = lambda *a, **k: plt.close("all")

import numpy as np
import pandas as pd
import synthetic_oasis as fake_env          # installs the offline stand-ins before the notebook imports

nb_path = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "..", "notebooks", "ASTRA_PoC.ipynb"))
cells = json.load(open(nb_path))["cells"]
code = []
for c in cells:
    if c["cell_type"] == "code":
        lines = "".join(c["source"]).split("\n")
        code.append("\n".join(l for l in lines if not l.strip().startswith("!")))

out_dir = os.path.join(HERE, "synthetic_outputs")
os.makedirs(out_dir, exist_ok=True)
os.chdir(out_dir)

ns = {}
log = io.StringIO()
with contextlib.redirect_stdout(log):
    exec("\n\n".join(code), ns)
open("notebook_log.txt", "w").write(log.getvalue())

# ---------------- compare with the simulated truth ----------------
labels, field_ids, category = ns["labels"], ns["field_ids"], ns["category"]
pid, _ = fake_env.plot_raster(ns["ds"].odc.geobox)
analysis_year = pd.Timestamp(ns["END_DATE"]).year          # agricultural year, labelled by harvest year

rows = []
for f in field_ids:
    p = pid[labels == f]
    p = p[p >= 0]
    if p.size == 0:
        continue
    vals, cnt = np.unique(p, return_counts=True)
    top = vals[np.argmax(cnt)]                              # the simulated plot that dominates this object
    kind = fake_env.kind[top]
    if kind == "late_ok" and fake_env.switch_year[top] <= analysis_year:
        kind = "main_ok"
    rows.append({"field_id": int(f), "kind": kind, "category": category.get(f, "missing")})
t = pd.DataFrame(rows)

group = {"late_fail": "Failing crop (either calendar)", "main_fail": "Failing crop (either calendar)",
         "late_ok": "Normal crop (late or main season)", "main_ok": "Normal crop (late or main season)",
         "double": "Double-cropped pivot", "palm": "Palm grove", "dead": "Abandoned or sparse plot"}
t["simulated"] = t.kind.map(group)
short = {c: c.split(":")[0].split(" (")[0] for c in t.category.unique()}
table = pd.crosstab(t.simulated, t.category.map(short), margins=True, margins_name="Total")
print("Assessed objects by simulated behaviour (rows) and notebook category (columns)\n")
print(table.to_string())

fail = t.simulated.str.startswith("Failing")
flag = t.category.str.startswith("Priority")
normal_like = t.simulated.isin(["Normal crop (late or main season)", "Double-cropped pivot"])
print(f"\nfailing crops flagged:            {int((fail & flag).sum())} of {int(fail.sum())}")
print(f"normal or double-cropped flagged: {int((normal_like & flag).sum())} of {int(normal_like.sum())}")
print(f"flags that are true failures:     {int((fail & flag).sum())} of {int(flag.sum())}")
print(f"leaf-water lead recovered:        priority {ns['LEAD_PRI']:+.1f} d, normal {ns['LEAD_NOR']:+.1f} d "
      f"(simulated +8 and +2); group test p = {ns['P_GROUP']:.2g}, scaled by decline speed p = {ns['P_GROUP_REL']:.2g}")
print(f"hindcast: caught {ns['CAUGHT_G']:.0%} (greenness only), {ns['CAUGHT_B']:.0%} (beyond green); "
      f"median extra warning from leaf water {ns['GAIN_DAYS']:.0f} d; "
      f"alarms on normal or perennial plots {ns['FA_G']} and {ns['FA_B']} of {len(ns['non'])}")
