"""Writes <out>/RESULTS.md: every results table, ready to copy into the Phase 3 report."""
import json
import os

import numpy as np

from .plots import _read_jsonl


def _ms(v, d=4):
    v = [x for x in v if x is not None and not (isinstance(x, float) and np.isnan(x))]
    if not v:
        return "–"
    return f"{np.mean(v):.{d}f}" + (f" ± {np.std(v):.{d}f}" if len(v) > 1 else "")


def _table(head, rows):
    s = "| " + " | ".join(head) + " |\n|" + "|".join(["---"] * len(head)) + "|\n"
    for r in rows:
        s += "| " + " | ".join(str(x) for x in r) + " |\n"
    return s + "\n"


# Numbers quoted from the papers reviewed in Phase 2 (NOT re-run; different protocols)
LITERATURE = [
    ("E1 VeMisNet (Bi-LSTM)", "VeReMi Ext., 500K subset, random 80/20", "Multi-class", "Acc 99.81% (abstract) / 91.80% (body)"),
    ("E5 HyperLSTM", "VeReMi Ext., 3.19M, chronological 80/20", "Binary", "Acc 98%, F1 ≈0.98, 0.6 ms"),
    ("E7 ABC stacked ensemble", "VeReMi/F2MD, 22M, stratified", "19-class", "Acc 0.94, F1 0.93"),
    ("E8 Fed-LSTM-AE", "VeReMi, non-IID clients", "Binary", "F1 0.963, ROC-AUC 0.966"),
    ("E4 Fed VAE+GMM", "VeReMi (5 position attacks)", "Binary", "F1 0.775, precision 0.672"),
    ("E9 LA-DETECTS", "VeReMi Ext. high density", "Per-attack", "see per-attack table"),
]
# E9 per-attack F1 (%) quoted in Phase 2
E9_F1 = {"ConstSpeedOffset": 99.46, "DoSDisruptiveSybil": 97.36, "ConstPosOffset": 64.45, "EventualStop": 63.69,
         "GridSybil": 60.24, "DelayedMessages": 74.45, "DataReplay": 80.19, "DoS": 99.9, "DoSRandom": 99.9,
         "DoSDisruptive": 99.9}


def write(out):
    md = ["# HP-MDS — Phase 3 results (auto-generated)\n",
          "All numbers are on the vehicle-grouped test split unless stated. ± = std over seeds. "
          "Models marked * were trained on a stratified subsample (secondary baselines).\n"]
    p = os.path.join(out, "dataset_summary.json")
    if os.path.exists(p):
        s = json.load(open(p))
        md.append("## Dataset and splits\n")
        md.append(_table(["Split", "Windows", "Vehicles", "Benign windows", "Attack windows"],
                         [[k, f"{v['windows']:,}", f"{v['vehicles']:,}", f"{v['classes'].get('Genuine', 0):,}",
                           f"{v['windows'] - v['classes'].get('Genuine', 0):,}"] for k, v in s.items()]))
    rows = _read_jsonl(os.path.join(out, "expA", "metrics.jsonl"))
    if rows:
        models = list(dict.fromkeys(r["model"] for r in rows))
        md.append("## Exp A — in-distribution, binary detection (attack vs benign)\n")
        md.append(_table(["Model", "Accuracy", "Precision", "Recall", "F1", "MCC", "ROC-AUC", "PR-AUC", "FPR", "FNR",
                          "Seeds"],
                         [[m] + [_ms([r["binary"].get(k) for r in rows if r["model"] == m])
                                 for k in ("accuracy", "precision", "recall", "f1", "mcc", "roc_auc", "pr_auc", "fpr", "fnr")]
                          + [len([r for r in rows if r["model"] == m])] for m in models]))
        mc = [m for m in models if any("multiclass" in r for r in rows if r["model"] == m)]
        md.append("## Exp A — multi-class identification\n")
        md.append(_table(["Model", "Accuracy", "Macro-F1", "Weighted-F1", "Balanced acc.", "MCC", "Params", "Size (MB)",
                          "Train time (s)"],
                         [[m] + [_ms([r["multiclass"][k] for r in rows if r["model"] == m])
                                 for k in ("accuracy", "macro_f1", "weighted_f1", "balanced_acc", "mcc")]
                          + [next((f"{r['n_params']:,}" for r in rows if r["model"] == m and r.get("n_params") == r.get("n_params")
                                   and r.get("n_params") is not None), "–"),
                             _ms([r["size_bytes"] / 1e6 for r in rows if r["model"] == m and r.get("size_bytes")], 2),
                             _ms([r["fit_time"] for r in rows if r["model"] == m], 0)] for m in mc]))
        cls = list(rows[0]["per_class_detection"].keys())
        md.append("## Exp A — detection rate per attack type (mean over seeds)\n")
        md.append(_table(["Attack"] + models,
                         [[c] + [_ms([r["per_class_detection"].get(c) for r in rows if r["model"] == m], 3) for m in models]
                          for c in cls]))
        md.append("## Exp A — per-class F1 (multi-class)\n")
        cls2 = list(next(r for r in rows if "per_class_f1" in r)["per_class_f1"].keys())
        md.append(_table(["Class"] + mc, [[c] + [_ms([r["per_class_f1"].get(c) for r in rows if r["model"] == m], 3)
                                                 for m in mc] for c in cls2]))
        # literature comparison
        hp = [r for r in rows if r["model"] == "HP-MDS"]
        md.append("## Comparison with existing research (quoted numbers — not like-for-like)\n")
        lit = [list(x) + ["No — different split/subset/classes"] for x in LITERATURE]
        if hp:
            lit.insert(0, ["**HP-MDS (ours)**", "VeReMi Ext., vehicle-grouped 60/20/20", "Binary / 20-class",
                           f"F1 {_ms([r['binary']['f1'] for r in hp])}, macro-F1 {_ms([r['multiclass']['macro_f1'] for r in hp])}",
                           "—"])
        md.append(_table(["Study", "Data / protocol", "Task", "Reported result", "Like-for-like?"], lit))
        if hp:
            md.append("### Per-attack F1 vs E9 LA-DETECTS (same dataset family; E9 values quoted from the paper)\n")
            md.append(_table(["Attack", "E9 F1 (%)", "HP-MDS F1 (%)"],
                             [[c, v, _ms([r["per_class_f1"].get(c, np.nan) * 100 for r in hp], 2)] for c, v in E9_F1.items()]))
    p = os.path.join(out, "stats", "stats.json")
    if os.path.exists(p):
        s = json.load(open(p))
        md.append("## Statistical tests (seed 0 predictions; CIs by vehicle-level bootstrap, 1,000 resamples)\n")
        md.append(_table(["Model", "Binary F1 [95% CI]", "Macro-F1 [95% CI]"],
                         [[m, f"{v['binary_f1']:.4f} [{v['binary_f1_ci'][0]:.4f}, {v['binary_f1_ci'][1]:.4f}]",
                           f"{v['macro_f1']:.4f} [{v['macro_f1_ci'][0]:.4f}, {v['macro_f1_ci'][1]:.4f}]" if "macro_f1" in v else "–"]
                          for m, v in s["bootstrap"].items()]))
        md.append(_table(["HP-MDS vs", "McNemar χ²", "p (Holm)", "Wilcoxon p (Holm), per-attack detection", "Mean Δ detection"],
                         [[m, f"{v['chi2']:.1f}", f"{v['p_holm']:.2e}",
                           f"{s['wilcoxon'].get(m, {}).get('p_holm', float('nan')):.3g}",
                           f"{s['wilcoxon'].get(m, {}).get('mean_diff', float('nan')):+.3f}"] for m, v in s["mcnemar"].items()]))
    rows = _read_jsonl(os.path.join(out, "expD", "metrics.jsonl"))
    if rows:
        md.append("## Exp D — open-set (attack types held out of training)\n")
        hs = sorted(set(r["holdout"] for r in rows))
        for h in hs:
            hr = [r for r in rows if r["holdout"] == h]
            md.append(f"**Hold-out {h + 1}: {', '.join(hr[0]['holdout_classes'])}**\n")
            ms = list(dict.fromkeys(r["model"] for r in hr))
            md.append(_table(["Model", "Unseen recall", "Unseen recall @5% FPR", "AUROC benign-vs-unseen", "Known recall",
                              "FPR", "Known macro-F1", "Unseen → 'unknown'", "Unseen → known class"],
                             [[m] + [_ms([r.get(k) for r in hr if r["model"] == m], 3) for k in
                                     ("unseen_recall", "unseen_recall_at5fpr", "auroc_benign_vs_unseen", "known_recall", "fpr",
                                      "known_macro_f1", "unseen_labelled_unknown", "unseen_misassigned_known")] for m in ms]))
        keys = sorted({k for r in rows for k in r.get("openset_auroc", {})})
        ms = [m for m in dict.fromkeys(r["model"] for r in rows) if any(r.get("openset_auroc") for r in rows if r["model"] == m)]
        md.append("### Open-set rejection AUROC (known attack vs unseen attack), mean over hold-outs and seeds\n")
        md.append(_table(["Model"] + keys, [[m] + [_ms([r["openset_auroc"].get(k) for r in rows if r["model"] == m
                                                        and k in r.get("openset_auroc", {})], 3) for k in keys] for m in ms]))
    p = os.path.join(out, "expE", "noise.json")
    if os.path.exists(p):
        rows = json.load(open(p))
        lv = list(dict.fromkeys(r["level"] for r in rows))
        ms = list(dict.fromkeys(r["model"] for r in rows))
        md.append("## Exp E — noise and packet-loss robustness (binary F1 / FPR, seed-0 models, test split)\n")
        md.append(_table(["Condition"] + ms, [[l] + [next(f"{r['f1']:.3f} / {r['fpr']:.3f}" for r in rows
                                                         if r["level"] == l and r["model"] == m) for m in ms] for l in lv]))
    p = os.path.join(out, "expF", "efficiency.json")
    if os.path.exists(p):
        e = json.load(open(p))
        rows = e["rows"] if isinstance(e, dict) else e
        md.append("## Exp F — efficiency (single CPU thread, one window per call)\n")
        md.append(_table(["Model", "Median latency (ms)", "p95 latency (ms)", "Throughput (windows/s, batched)", "Params",
                          "Size (MB)", "Within 100 ms?"],
                         [[r["model"], f"{r['latency_ms_median']:.3f}", f"{r['latency_ms_p95']:.3f}",
                           f"{r['throughput_windows_per_s']:,.0f}", r["n_params"] if r["n_params"] == r["n_params"] else "–",
                           f"{r['size_mb']:.3f}" if r.get("size_mb") else "–", "yes" if r["within_100ms_budget"] else "no"]
                          for r in rows]))
        if isinstance(e, dict):
            md.append(f"HP-MDS flags {e['hpmds_flag_rate'] * 100:.1f}% of test windows, so Stage 2 runs only on that share.\n")
    rows = _read_jsonl(os.path.join(out, "ablation", "ablation.jsonl"))
    if rows:
        md.append("## Ablation studies (mean ± std over seeds)\n")
        for st in dict.fromkeys(r["study"] for r in rows):
            sr = [r for r in rows if r["study"] == st]
            vs = list(dict.fromkeys(r["variant"] for r in sr))
            md.append(f"**{st}**\n")
            md.append(_table(["Variant", "F1", "Precision", "Recall", "FPR", "ROC-AUC", "Macro-F1"],
                             [[v] + [_ms([r.get(k) for r in sr if r["variant"] == v], 4) for k in
                                     ("f1", "precision", "recall", "fpr", "roc_auc", "macro_f1")] for v in vs]))
    rows = _read_jsonl(os.path.join(out, "expC", "metrics.jsonl"))
    if rows:
        ms = list(dict.fromkeys(r["model"] for r in rows))
        md.append("## Exp C — cross-dataset (train VeReMi Extension → test original VeReMi; shared fields)\n")
        md.append(_table(["Model", "In-dist F1", "Cross F1", "Drop", "Cross precision", "Cross recall", "Cross FPR", "Cross ROC-AUC"],
                         [[m, _ms([r["in_dist"]["f1"] for r in rows if r["model"] == m], 3),
                           _ms([r["cross"]["f1"] for r in rows if r["model"] == m], 3),
                           _ms([r["in_dist"]["f1"] - r["cross"]["f1"] for r in rows if r["model"] == m], 3)]
                          + [_ms([r["cross"].get(k) for r in rows if r["model"] == m], 3) for k in
                             ("precision", "recall", "fpr", "roc_auc")] for m in ms]))
        cls = list(rows[0]["cross_per_class_detection"].keys())
        md.append(_table(["Original-VeReMi attack"] + ms, [[c] + [_ms([r["cross_per_class_detection"].get(c) for r in rows
                                                                       if r["model"] == m], 3) for m in ms] for c in cls]))

    rows = _read_jsonl(os.path.join(out, "expB", "metrics.jsonl"))
    if rows:
        md.append("## Exp B — traffic-density shift (off-peak hours vs rush hours 07-09 & 17-19)\n")
        for d in dict.fromkeys(r["direction"] for r in rows):
            dr = [r for r in rows if r["direction"] == d]
            ms = list(dict.fromkeys(r["model"] for r in dr))
            md.append(f"**Train {d.split('->')[0]} → test {d.split('->')[1]}**\n")
            md.append(_table(["Model", "Source F1", "Target F1", "Drop", "Target precision", "Target recall", "Target FPR",
                              "Target macro-F1"],
                             [[m, _ms([r["source_test"]["f1"] for r in dr if r["model"] == m], 3),
                               _ms([r["target_test"]["f1"] for r in dr if r["model"] == m], 3),
                               _ms([r["source_test"]["f1"] - r["target_test"]["f1"] for r in dr if r["model"] == m], 3)]
                              + [_ms([r["target_test"].get(k) for r in dr if r["model"] == m], 3)
                                 for k in ("precision", "recall", "fpr", "macro_f1")] for m in ms]))
    p = os.path.join(out, "expG", "detection_delay.json")
    if os.path.exists(p):
        d = json.load(open(p))
        md.append("## Timeliness — detection delay per attacker stream (seed-0 models, stride-1 windows)\n")
        md.append(_table(["Model", "Attacker streams detected", "Median delay (msgs)", "p90 delay (msgs)", "Median delay (s)",
                          "p90 delay (s)", "Benign streams with any alarm"],
                         [[m, f"{v['stream_detection_rate']:.3f} ({v['detected_streams']}/{v['attacker_streams']})",
                           f"{v['delay_msgs_median']:.0f}", f"{v['delay_msgs_p90']:.0f}", f"{v['delay_s_median']:.1f}",
                           f"{v['delay_s_p90']:.1f}", f"{v['benign_stream_false_alarm_rate']:.3f} "
                           f"({v['benign_streams_with_alarm']}/{v['benign_streams']})"] for m, v in d.items()]))
        md.append("A window needs 10 messages, so the minimum possible delay is 10 messages.\n")
    p = os.path.join(out, "expG", "error_analysis.json")
    if os.path.exists(p):
        e = json.load(open(p))
        md.append("## Error / failure analysis\n")
        md.append("**HP-MDS failure taxonomy (test windows, seed 0)**\n")
        md.append(_table(["Category", "Windows"], [[k, f"{v:,}"] for k, v in e["hpmds_failure_taxonomy"].items()]))
        for key, title in (("by_speed", "mean speed (m/s)"), ("by_gap", "mean beacon gap (s)")):
            ms = list(e["errors_by_context"].keys())
            bins = list(e["errors_by_context"][ms[0]][key].keys())
            md.append(f"**Missed-attack rate / false-positive rate by {title}**\n")
            md.append(_table(["Bin", "Windows"] + ms,
                             [[b, f"{e['errors_by_context'][ms[0]][key][b]['windows']:,}"] +
                              [("–" if e["errors_by_context"][m][key][b]["fnr"] is None else f"{e['errors_by_context'][m][key][b]['fnr']:.3f}")
                               + " / " + ("–" if e["errors_by_context"][m][key][b]["fpr"] is None else f"{e['errors_by_context'][m][key][b]['fpr']:.4f}")
                               for m in ms] for b in bins]))
        pf = sorted(e["ae_per_feature_recon_error"].items(), key=lambda kv: -kv[1]["ratio"])
        md.append("**Stage-1 autoencoder: per-feature reconstruction error (attack / benign)**\n")
        md.append(_table(["Feature", "Benign", "Attack", "Ratio"],
                         [[k, f"{v['benign']:.4f}", f"{v['attack']:.4f}", f"{v['ratio']:.2f}"] for k, v in pf]))
        md.append("**XGBoost top-20 features (gain importance)**\n")
        md.append(_table(["Feature", "Importance"], [[k, f"{v:.4f}"] for k, v in e["xgboost_top20_importance"].items()]))
    p = os.path.join(out, "expF", "onnx.json")
    if os.path.exists(p):
        rows = json.load(open(p))
        md.append("## ONNX export (onnxruntime, single CPU thread, one window)\n")
        md.append(_table(["Model part", "Median (ms)", "p95 (ms)", "Size (KB)", "Max |diff| vs PyTorch"],
                         [[r["model"], f"{r['onnx_latency_ms_median']:.4f}", f"{r['onnx_latency_ms_p95']:.4f}",
                           f"{r['onnx_size_kb']:.0f}", f"{r['max_abs_diff_vs_torch']:.1e}"] if "error" not in r else
                          [r["model"], "failed", r["error"][:60], "", ""] for r in rows]))

    p = os.path.join(out, "expH", "extra.json")
    if os.path.exists(p):
        h = json.load(open(p))
        if "time_jitter" in h:
            rows = h["time_jitter"]
            sig = list(dict.fromkeys(r["time_sigma_s"] for r in rows))
            ms = list(dict.fromkeys(r["model"] for r in rows))
            md.append("## Exp E (extension) — timestamp jitter (binary F1 / FPR, seed-0 models)\n")
            md.append(_table(["Jitter σ (s)"] + ms, [[s] + [next(f"{r['f1']:.3f} / {r['fpr']:.3f}" for r in rows
                                                              if r["time_sigma_s"] == s and r["model"] == m) for m in ms] for s in sig]))
        if "adversarial" in h:
            rows = h["adversarial"]
            ms = list(dict.fromkeys(r["model"] for r in rows))
            md.append("## Adversarial sanity check (G6) — attack-window detection rate under feature-space perturbation\n")
            md.append("ε is in units of each raw feature's training standard deviation; PGD = 10-step white-box attack "
                      "pushing windows towards 'benign'. Rows 'PGD vs X' are white-box for X and transferred to the "
                      "other models. Feature-space attacks need not be physically realisable — this is a sanity check only.\n")
            keys = list(dict.fromkeys((r["eps"], r["perturbation"]) for r in rows))
            md.append(_table(["ε", "Perturbation"] + ms, [[e, pz] + [next(f"{r['attack_detection_rate']:.3f}" for r in rows
                                                                         if r["eps"] == e and r["perturbation"] == pz and r["model"] == m)
                                                                    for m in ms] for e, pz in keys]))
        if "permutation_importance" in h:
            pi = h["permutation_importance"]
            md.append(f"## Permutation importance (60k test windows; base Stage-2 macro-F1 {pi['base_stage2_macro_f1']:.3f}, "
                      f"base Stage-1 F1 {pi['base_stage1_f1']:.3f})\n")
            md.append(_table(["Feature", "Stage-2 macro-F1 drop", "Stage-1 F1 drop"],
                             [[k, f"{v['stage2_macro_f1_drop']:.4f}", f"{v['stage1_binary_f1_drop']:.4f}"]
                              for k, v in pi["features"].items()]))
        if isinstance(h.get("peak_memory"), list):
            md.append("## Peak extra inference memory (CPU, 1 thread, batch of 8,192 windows)\n")
            md.append(_table(["Model", "Peak extra memory (MB)", "Per window (KB)"],
                             [[r["model"], f"{r['peak_extra_mb_batch8192']:.1f}", f"{r['per_window_kb']:.2f}"] for r in h["peak_memory"]]))
        if "oscr" in h:
            rows = h["oscr"]
            ms = list(dict.fromkeys(r["model"] for r in rows))
            md.append("## Open-set classification rate (OSCR; known-attack correct classification vs unseen-attack acceptance)\n")
            md.append(_table(["Model"] + [f"Hold-out {i + 1}" for i in sorted(set(r["holdout"] for r in rows))] + ["Mean"],
                             [[m] + [f"{r['oscr']:.3f}" for r in rows if r["model"] == m]
                              + [f"{np.mean([r['oscr'] for r in rows if r['model'] == m]):.3f}"] for m in ms]))
    pseudo = os.path.join(os.path.dirname(os.path.abspath(out)), "results_pseudo", "expA", "metrics.jsonl")
    rows = _read_jsonl(pseudo)
    if rows:
        ms = list(dict.fromkeys(r["model"] for r in rows))
        md.append("## Robustness check — windows per pseudonym instead of per vehicle (Exp A setting)\n")
        md.append(_table(["Model", "Binary F1", "MCC", "FPR", "Macro-F1", "Seeds"],
                         [[m, _ms([r["binary"]["f1"] for r in rows if r["model"] == m]),
                           _ms([r["binary"]["mcc"] for r in rows if r["model"] == m]),
                           _ms([r["binary"]["fpr"] for r in rows if r["model"] == m]),
                           _ms([r["multiclass"]["macro_f1"] for r in rows if r["model"] == m and "multiclass" in r]),
                           len([r for r in rows if r["model"] == m])] for m in ms]))
    figs = sorted(os.listdir(os.path.join(out, "figures"))) if os.path.isdir(os.path.join(out, "figures")) else []
    if figs:
        md.append("## Figures\n")
        md += [f"![{f}](figures/{f})\n" for f in figs]
    with open(os.path.join(out, "RESULTS.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))
    print("[report] wrote", os.path.join(out, "RESULTS.md"))
