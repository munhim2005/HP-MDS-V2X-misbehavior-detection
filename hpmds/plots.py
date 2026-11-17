"""All figures for the Phase 3 report (saved to <out>/figures/*.png). Each figure is skipped if its
experiment has not been run yet."""
import json
import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from sklearn.metrics import confusion_matrix, precision_recall_curve, roc_curve  # noqa: E402

from .data import CLASS_NAMES  # noqa: E402

PALETTE = ["#2a6fdb", "#e4572e", "#29a36a", "#f3a712", "#8e44ad", "#17becf", "#7f7f7f", "#bc5090", "#003f5c"]
HP_COLOR = "#e4572e"
plt.rcParams.update({"figure.dpi": 130, "savefig.dpi": 200, "font.size": 9, "axes.spines.top": False,
                     "axes.spines.right": False, "axes.grid": True, "grid.alpha": 0.25})


def _read_jsonl(p):
    if not os.path.exists(p):
        return []
    with open(p, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def _save(fig, out, name):
    os.makedirs(os.path.join(out, "figures"), exist_ok=True)
    fig.tight_layout()
    fig.savefig(os.path.join(out, "figures", name), bbox_inches="tight")
    plt.close(fig)
    print("  figure:", name)


def _color(m, i):
    return HP_COLOR if m.startswith("HP-MDS") else [c for c in PALETTE if c != HP_COLOR][i % (len(PALETTE) - 1)]


def fig_roc_pr(out):
    p = os.path.join(out, "expA", "preds_seed0.npz")
    if not os.path.exists(p):
        return
    z = np.load(p)
    yb = (z["y"] > 0).astype(int)
    models = [k.split("|")[0] for k in z.files if k.endswith("|score")]
    fig, ax = plt.subplots(1, 2, figsize=(10, 4))
    for i, m in enumerate(models):
        s = np.nan_to_num(z[f"{m}|score"])
        fpr, tpr, _ = roc_curve(yb, s)
        pr, rc, _ = precision_recall_curve(yb, s)
        lw = 2.2 if m == "HP-MDS" else 1.2
        ax[0].plot(fpr, tpr, label=m, color=_color(m, i), lw=lw)
        ax[1].plot(rc, pr, label=m, color=_color(m, i), lw=lw)
    ax[0].plot([0, 1], [0, 1], "k--", lw=0.7)
    ax[0].set(xlabel="False positive rate", ylabel="True positive rate", title="ROC curves (test, seed 0)")
    ax[0].set_xscale("symlog", linthresh=0.01)
    ax[1].set(xlabel="Recall", ylabel="Precision", title="Precision-recall curves (test, seed 0)")
    ax[1].legend(fontsize=7, loc="lower left")
    _save(fig, out, "fig01_roc_pr.png")


def fig_confusion(out):
    p = os.path.join(out, "expA", "preds_seed0.npz")
    if not os.path.exists(p):
        return
    z = np.load(p)
    for m, fname in (("HP-MDS", "fig02_confusion_hpmds.png"), ("XGBoost", "fig02b_confusion_xgboost.png")):
        if f"{m}|label" not in z.files:
            continue
        y, lab = z["y"], z[f"{m}|label"]
        labels = sorted(set(np.unique(y)) | ({-1} if (lab == -1).any() else set()))
        cm = confusion_matrix(y, lab, labels=labels).astype(float)
        cm = cm / np.maximum(cm.sum(1, keepdims=True), 1)
        names = [CLASS_NAMES.get(int(c), "Unknown") if c != -1 else "Unknown" for c in labels]
        fig, ax = plt.subplots(figsize=(9, 8))
        im = ax.imshow(cm, cmap="Blues", vmin=0, vmax=1)
        ax.set_xticks(range(len(names)), names, rotation=90, fontsize=7)
        ax.set_yticks(range(len(names)), names, fontsize=7)
        for i in range(len(names)):
            for j in range(len(names)):
                if cm[i, j] >= 0.05:
                    ax.text(j, i, f"{cm[i, j]:.2f}", ha="center", va="center", fontsize=5.5,
                            color="white" if cm[i, j] > 0.6 else "black")
        ax.set(xlabel="Predicted", ylabel="True", title=f"{m}: row-normalised confusion matrix (test, seed 0)")
        ax.grid(False)
        fig.colorbar(im, fraction=0.04)
        _save(fig, out, fname)


def _mean_by(rows, key, sub):
    out = {}
    for r in rows:
        for c, v in r.get(key, {}).items():
            out.setdefault(r["model"], {}).setdefault(c, []).append(v)
    return {m: {c: float(np.mean(v)) for c, v in d.items()} for m, d in out.items()}


def fig_per_attack(out):
    rows = _read_jsonl(os.path.join(out, "expA", "metrics.jsonl"))
    if not rows:
        return
    for key, fname, title in (("per_class_detection", "fig03_per_attack_detection.png",
                               "Detection rate per attack type (binary alarm, mean over seeds)"),
                              ("per_class_f1", "fig04_per_attack_f1.png", "Per-class F1 (multi-class, mean over seeds)")):
        d = _mean_by(rows, key, None)
        if not d:
            continue
        models = list(d.keys())
        classes = list(next(iter(d.values())).keys())
        fig, ax = plt.subplots(figsize=(12, 4.2))
        wdt = 0.8 / len(models)
        for i, m in enumerate(models):
            ax.bar(np.arange(len(classes)) + i * wdt, [d[m].get(c, np.nan) for c in classes], wdt, label=m,
                   color=_color(m, i))
        ax.set_xticks(np.arange(len(classes)) + 0.4 - wdt / 2, classes, rotation=60, ha="right", fontsize=7)
        ax.set(ylim=(0, 1.05), title=title)
        ax.legend(fontsize=7, ncol=5, loc="lower left")
        _save(fig, out, fname)


def fig_scores(out):
    p = os.path.join(out, "ablation", "scores_seed0.npz")
    if not os.path.exists(p):
        return
    z = np.load(p)
    ben = z["y"] == 0
    fig, ax = plt.subplots(1, 3, figsize=(12, 3.4))
    for a, (k, t) in zip(ax, (("s_ae", "Autoencoder reconstruction error"), ("s_phys", "Physics violation score"),
                              ("fused", "Fused score"))):
        s = z[k]
        lo, hi = np.percentile(s, [0.5, 99.5])
        bins = np.linspace(lo, hi, 80)
        a.hist(np.clip(s[ben], lo, hi), bins, alpha=0.6, density=True, label="benign", color="#2a6fdb")
        a.hist(np.clip(s[~ben], lo, hi), bins, alpha=0.6, density=True, label="attack", color="#e4572e")
        a.set(title=t, yscale="log")
        a.legend(fontsize=7)
    _save(fig, out, "fig05_score_distributions.png")


def fig_sweeps(out):
    p = os.path.join(out, "ablation", "sweeps.json")
    if not os.path.exists(p):
        return
    s = json.load(open(p))
    fig, ax = plt.subplots(1, 2, figsize=(10, 3.6))
    a = [r["alpha"] for r in s["alpha"]]
    ax[0].plot(a, [r["test_f1"] for r in s["alpha"]], "o-", color=HP_COLOR, label="test F1")
    ax[0].plot(a, [r["val_f1"] for r in s["alpha"]], "s--", color="#2a6fdb", label="validation F1")
    ax[0].set(xlabel="alpha (1 = autoencoder only, 0 = physics only)", ylabel="Binary F1", title="Score-fusion weight")
    ax[0].legend()
    pc = [r["pct"] for r in s["pct"]]
    for k, c in (("f1", HP_COLOR), ("precision", "#29a36a"), ("recall", "#2a6fdb"), ("fpr", "#7f7f7f")):
        ax[1].plot(range(len(pc)), [r[k] for r in s["pct"]], "o-", label=k, color=c)
    ax[1].set_xticks(range(len(pc)), [str(x) for x in pc])
    ax[1].set(xlabel="Threshold = benign validation percentile", title="Threshold sensitivity")
    ax[1].legend(fontsize=7)
    _save(fig, out, "fig06_alpha_threshold_sweeps.png")


def fig_efficiency(out):
    p = os.path.join(out, "expF", "efficiency.json")
    rows = _read_jsonl(os.path.join(out, "expA", "metrics.jsonl"))
    if not os.path.exists(p) or not rows:
        return
    eff = json.load(open(p))
    eff = eff["rows"] if isinstance(eff, dict) else eff
    f1 = {}
    for r in rows:
        f1.setdefault(r["model"], []).append(r["binary"]["f1"])
    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    for i, e in enumerate(eff):
        m = e["model"]
        base = m.split(" (")[0]
        if base not in f1:
            continue
        size = e.get("size_mb") or 0.05
        ax.scatter(e["latency_ms_median"], np.mean(f1[base]), s=40 + 300 * np.sqrt(size), color=_color(m, i),
                   alpha=0.8, edgecolor="k")
        ax.annotate(m, (e["latency_ms_median"], np.mean(f1[base])), fontsize=7,
                    xytext=(6, -12 if "Stage 1" in m else 6), textcoords="offset points")
    ax.set_xscale("log")
    ax.set(xlabel="Median latency per window, single CPU thread (ms, log)", ylabel="Binary F1 (mean over seeds)",
           title="Accuracy vs latency (bubble area ~ model size)")
    ax.axvline(100, color="red", ls="--", lw=0.8)
    ax.text(100, ax.get_ylim()[0], " 100 ms BSM budget", color="red", fontsize=7, va="bottom")
    _save(fig, out, "fig07_accuracy_latency.png")


def fig_openset(out):
    rows = _read_jsonl(os.path.join(out, "expD", "metrics.jsonl"))
    if not rows:
        return
    models = list(dict.fromkeys(r["model"] for r in rows))
    hs = sorted(set(r["holdout"] for r in rows))
    names = {r["holdout"]: "\n".join(r["holdout_classes"]) for r in rows}
    fig, ax = plt.subplots(1, 2, figsize=(12, 4))
    wdt = 0.8 / len(models)
    for i, m in enumerate(models):
        for k, a in (("unseen_recall", ax[0]), ("fpr", ax[1])):
            vals = [np.mean([r[k] for r in rows if r["model"] == m and r["holdout"] == h]) for h in hs]
            err = [np.std([r[k] for r in rows if r["model"] == m and r["holdout"] == h]) for h in hs]
            a.bar(np.arange(len(hs)) + i * wdt, vals, wdt, yerr=err, label=m, color=_color(m, i), capsize=2)
    for a, t in ((ax[0], "Detection rate on UNSEEN attack types"), (ax[1], "False-positive rate on benign")):
        a.set_xticks(np.arange(len(hs)) + 0.4 - wdt / 2, [f"Hold-out {h + 1}\n{names[h]}" for h in hs], fontsize=7)
        a.set_title(t)
    ax[0].set_ylim(0, 1.05)
    ax[0].legend(fontsize=7, ncol=2)
    _save(fig, out, "fig08_openset.png")


def fig_noise(out):
    p = os.path.join(out, "expE", "noise.json")
    if not os.path.exists(p):
        return
    rows = json.load(open(p))
    lv = list(dict.fromkeys(r["level"] for r in rows))
    models = list(dict.fromkeys(r["model"] for r in rows))
    fig, ax = plt.subplots(1, 3, figsize=(13, 3.8))
    for i, m in enumerate(models):
        for a, k in zip(ax, ("f1", "fpr", "recall")):
            a.plot(range(len(lv)), [next(r[k] for r in rows if r["model"] == m and r["level"] == l) for l in lv],
                   "o-", label=m, color=_color(m, i), lw=2.2 if m == "HP-MDS" else 1.2)
    for a, k in zip(ax, ("Binary F1", "False-positive rate", "Recall")):
        a.set_xticks(range(len(lv)), lv, rotation=45, ha="right", fontsize=7)
        a.set_title(k + " under noise / packet loss")
    ax[0].legend(fontsize=7)
    _save(fig, out, "fig09_noise_robustness.png")


def fig_ablation(out):
    rows = _read_jsonl(os.path.join(out, "ablation", "ablation.jsonl"))
    if not rows:
        return
    studies = list(dict.fromkeys(r["study"] for r in rows))
    fig, axes = plt.subplots(len(studies), 1, figsize=(8, 1.2 + 0.45 * len(rows) / max(1, len(set(r["seed"] for r in rows)))))
    axes = np.atleast_1d(axes)
    for a, st in zip(axes, studies):
        vs = list(dict.fromkeys(r["variant"] for r in rows if r["study"] == st))
        f = [[r["f1"] for r in rows if r["study"] == st and r["variant"] == v] for v in vs]
        mf = [[r["macro_f1"] for r in rows if r["study"] == st and r["variant"] == v and r.get("macro_f1") is not None]
              for v in vs]
        y = np.arange(len(vs))
        a.barh(y - 0.2, [np.mean(x) for x in f], 0.4, xerr=[np.std(x) for x in f], color=HP_COLOR, label="binary F1",
               capsize=2)
        if any(mf):
            a.barh(y + 0.2, [np.mean(x) if x else 0 for x in mf], 0.4, xerr=[np.std(x) if x else 0 for x in mf],
                   color="#2a6fdb", label="macro-F1", capsize=2)
        a.set_yticks(y, vs, fontsize=7)
        a.set_title(f"Ablation: {st}", fontsize=8)
        lo = min([np.mean(x) for x in f] + [np.mean(x) for x in mf if x])
        a.set_xlim(max(0, np.floor((lo - 0.05) * 20) / 20), 1.0)
        a.legend(fontsize=6, loc="lower right")
    _save(fig, out, "fig10_ablations.png")


def fig_cross(out):
    rows = _read_jsonl(os.path.join(out, "expC", "metrics.jsonl"))
    if not rows:
        return
    models = list(dict.fromkeys(r["model"] for r in rows))
    fig, ax = plt.subplots(figsize=(8, 3.8))
    for j, (k, c) in enumerate((("in_dist", "#2a6fdb"), ("cross", "#e4572e"))):
        ax.bar(np.arange(len(models)) + j * 0.4, [np.mean([r[k]["f1"] for r in rows if r["model"] == m]) for m in models],
               0.4, color=c, label="VeReMi Extension test (in-distribution)" if k == "in_dist" else "Original VeReMi (cross-dataset)")
    ax.set_xticks(np.arange(len(models)) + 0.2, models, rotation=30, ha="right")
    ax.set(ylabel="Binary F1", title="Cross-dataset generalisation (shared fields: position, speed, time)", ylim=(0, 1.05))
    ax.legend(fontsize=7)
    _save(fig, out, "fig11_cross_dataset.png")


def fig_training(out):
    p = os.path.join(out, "expA", "hpmds_seed0_details.json")
    if not os.path.exists(p):
        return
    d = json.load(open(p))
    fig, ax = plt.subplots(1, 2, figsize=(9, 3.2))
    for a, k, t in ((ax[0], "hist_ae", "Stage 1 autoencoder (MSE)"), (ax[1], "hist_clf", "Stage 2 classifier (CE)")):
        h = d.get(k)
        if not h:
            continue
        h = np.array(h)
        a.plot(h[:, 0], h[:, 1], "o-", label="train")
        a.plot(h[:, 0], h[:, 2], "s--", label="validation")
        a.set(xlabel="epoch", title=t)
        a.legend()
    _save(fig, out, "fig12_training_curves.png")


def make_all(out):
    print("[figures] writing to", os.path.join(out, "figures"))
    for f in (fig_roc_pr, fig_confusion, fig_per_attack, fig_scores, fig_sweeps, fig_efficiency, fig_openset,
              fig_noise, fig_ablation, fig_cross, fig_training):
        try:
            f(out)
        except Exception as e:  # keep going so one broken figure does not block the rest
            print(f"  [warn] {f.__name__} failed: {e}")


def fig_density(out):
    rows = _read_jsonl(os.path.join(out, "expB", "metrics.jsonl"))
    if not rows:
        return
    models = list(dict.fromkeys(r["model"] for r in rows))
    dirs = list(dict.fromkeys(r["direction"] for r in rows))
    fig, ax = plt.subplots(1, len(dirs), figsize=(6 * len(dirs), 3.8), squeeze=False)
    for a, d in zip(ax[0], dirs):
        for j, (k, c) in enumerate((("source_test", "#2a6fdb"), ("target_test", "#e4572e"))):
            a.bar(np.arange(len(models)) + j * 0.4,
                  [np.mean([r[k]["f1"] for r in rows if r["model"] == m and r["direction"] == d]) for m in models], 0.4,
                  color=c, label=("same density (source)" if j == 0 else "shifted density (target)"))
        a.set_xticks(np.arange(len(models)) + 0.2, models, rotation=30, ha="right")
        a.set(title=f"Density shift: train {d.split('->')[0]} → test {d.split('->')[1]}", ylabel="Binary F1", ylim=(0, 1.05))
        a.legend(fontsize=7)
    _save(fig, out, "fig13_density_shift.png")


def fig_delay(out):
    p = os.path.join(out, "expG", "detection_delay.json")
    if not os.path.exists(p):
        return
    d = json.load(open(p))
    fig, ax = plt.subplots(1, 2, figsize=(11, 3.8))
    for i, (m, v) in enumerate(d.items()):
        x = np.sort(np.array(v["delay_msgs_all"], dtype=float))
        x = x[~np.isnan(x)]
        if len(x):
            ax[0].step(x, np.arange(1, len(x) + 1) / v["attacker_streams"], where="post", label=m, color=_color(m, i),
                       lw=2.2 if m == "HP-MDS" else 1.2)
    ax[0].set(xlabel="Messages received from the attacker before the first alarm", ylabel="Share of attacker streams detected",
              title="Detection delay (CDF, test vehicles)", xscale="log")
    ax[0].legend(fontsize=7)
    p2 = os.path.join(out, "expG", "error_analysis.json")
    if os.path.exists(p2):
        e = json.load(open(p2))["errors_by_context"]
        for i, (m, v) in enumerate(e.items()):
            bins = list(v["by_speed"].keys())
            ax[1].plot(range(len(bins)), [v["by_speed"][b]["fnr"] if v["by_speed"][b]["fnr"] is not None else np.nan
                                          for b in bins], "o-", label=m, color=_color(m, i))
        ax[1].set_xticks(range(len(bins)), bins)
        ax[1].set(xlabel="Mean speed in window (m/s)", ylabel="Missed-attack rate (FNR)", title="Missed attacks by speed")
    _save(fig, out, "fig14_delay_and_errors.png")


_old_make_all = make_all


def make_all(out):  # noqa: F811
    _old_make_all(out)
    for f in (fig_density, fig_delay):
        try:
            f(out)
        except Exception as e:
            print(f"  [warn] {f.__name__} failed: {e}")
