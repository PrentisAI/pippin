"""Figure 3 of the AURORA paper (arXiv version): when the live auditor first flags a failure, and
recall per agent model for both auditors.

    python auditor_results.py auditor_results_data.json auditor_results.pdf stack

The data file holds the 93 detection leads (first detection minus annotated onset, in steps) and,
per agent model, [detected, annotated] failures for the live and the milestone auditor, as computed
from aurora-experiments-data (supervisor/, auditor/).  "side" puts the panels side by side."""
import json
import sys

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

D = json.load(open(sys.argv[1]))
OUT = sys.argv[2]
LAYOUT = sys.argv[3] if len(sys.argv) > 3 else "side"

plt.rcParams.update({
    "font.family": "STIXGeneral", "mathtext.fontset": "stix", "font.size": 8,
    "axes.titlesize": 8, "axes.labelsize": 8, "xtick.labelsize": 7, "ytick.labelsize": 7,
    "legend.fontsize": 7, "axes.linewidth": 0.6, "xtick.major.width": 0.6, "ytick.major.width": 0.6,
    "xtick.major.size": 2.5, "ytick.major.size": 2.5, "pdf.fonttype": 42, "ps.fonttype": 42,
})
LIVE, MILE = "#3B6EA8", "#E08A2E"          # live auditor, milestone auditor (same in both panels)
LIVE_LIGHT = "#A9C1DE"
COLW = 3.03                                # ACL column width, inches

if LAYOUT == "side":
    fig, (a, b) = plt.subplots(1, 2, figsize=(COLW, 1.75), gridspec_kw={"width_ratios": [1, 1.05]})
else:
    fig, (a, b) = plt.subplots(2, 1, figsize=(COLW, 2.8), gridspec_kw={"height_ratios": [1, 1.3]})

# (a) lead of the first detection relative to the annotated onset (negative: before the failure)
leads = np.array(D["leads"])
clipped = np.clip(leads, -60, 19.9)
bins = np.arange(-60, 25, 5)
before = clipped[leads < 0]
after = clipped[leads >= 0]
a.hist([before, after], bins=bins, stacked=True, color=[LIVE, LIVE_LIGHT], edgecolor="white", linewidth=0.4,
       label=[f"before onset ({len(before)})", f"at or after ({len(after)})"])
a.axvline(0, color="#333333", lw=0.7, ls=(0, (3, 2)))
a.text(-1.0, a.get_ylim()[1] * 0.97, "onset", fontsize=7, va="top", ha="right", color="#333333")
a.set_xlabel("first detection $-$ onset (steps)")
a.set_ylabel("detected failures")
a.set_yticks([0, 10, 20, 30])
a.set_xticks([-60, -40, -20, 0, 20])
a.set_xticklabels(["≤ −60", "−40", "−20", "0", "≥ 20"])
a.legend(frameon=False, loc="upper left", handlelength=1.0, handletextpad=0.4, borderaxespad=0.1)
a.set_title("(a) When the live auditor flags", loc="left", pad=3)

# (b) recall per agent model, as dots with the counts
names = {"claude-sonnet-5": "Claude Sonnet 5", "opus-4.8": "Claude Opus 4.8", "deepseek-v4-pro": "DeepSeek-V4-Pro",
         "gemini-3.5-flash": "Gemini 3.5 Flash", "glm-5.2": "GLM-5.2", "gpt-5-mini": "GPT-5 mini",
         "minimax-m3": "MiniMax-M3", "qwen3.7-max": "Qwen3.7-Max"}
models = list(names)
y = np.arange(len(models))
for i, m in enumerate(models):
    b.axhline(i, color="#E6E6E6", lw=0.5, zorder=0)
    lv = D["live"].get(m)
    mv = D["milestone"][m]
    if lv:
        b.plot(lv[0] / lv[1], i - 0.17, "o", ms=3.6, color=LIVE, zorder=3, label="live auditor" if i == 0 else None)
    # the counts, in a column right of the plot: detected / annotated
    b.text(1.04, i, f"{lv[0]}/{lv[1]}" if lv else "n/a", transform=b.get_yaxis_transform(), fontsize=6.5,
           color=LIVE, va="center", ha="left")
    b.plot(mv[0] / mv[1], i + 0.17, "s", ms=3.3, color=MILE, zorder=3, label="milestone auditor" if i == 0 else None)
    b.text(1.19, i, f"{mv[0]}/{mv[1]}", transform=b.get_yaxis_transform(), fontsize=6.5,
           color="#B5651D", va="center", ha="left")
b.set_yticks(y)
b.set_yticklabels([names[m] for m in models])
b.invert_yaxis()
b.set_xlim(0.7, 1.02)
b.set_xticks([0.7, 0.8, 0.9, 1.0])
b.set_xticklabels(["0.7", "0.8", "0.9", "1"])
b.set_xlabel("recall (share of annotated failures)")
b.tick_params(axis="y", length=0)
b.legend(frameon=False, loc="lower left", ncol=1, handletextpad=0.2, borderaxespad=0.2)
b.text(1.04, -0.75, "live", transform=b.get_yaxis_transform(), fontsize=6.5, color=LIVE, ha="left", va="center")
b.text(1.19, -0.75, "mile.", transform=b.get_yaxis_transform(), fontsize=6.5, color="#B5651D", ha="left", va="center")
b.set_title("(b) Recall by agent model" if LAYOUT != "side" else "", loc="left", pad=3)
if LAYOUT == "side":
    b.text(-0.02, 1.13, "(b) Recall by agent model", transform=b.transAxes, fontsize=8, ha="left")
tot_l = [sum(D["live"][m][k] for m in D["live"]) for k in (0, 1)]
tot_m = [sum(D["milestone"][m][k] for m in D["milestone"]) for k in (0, 1)]
for ax in (a, b):
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
b.spines["left"].set_visible(False)
fig.tight_layout(pad=0.25, w_pad=0.5, h_pad=0.6)
fig.savefig(OUT)
fig.savefig(OUT.replace(".pdf", ".png"), dpi=300)
print("live", tot_l, "milestone", tot_m, "before", len(before), "after", len(after))
