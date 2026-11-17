"""HP-MDS Phase 3 experiment runner.

Examples
  python run.py --csv data/mixalldata_clean.csv --exp prepare
  python run.py --csv data/mixalldata_clean.csv --exp A E F stats          # terminal 1
  python run.py --csv data/mixalldata_clean.csv --exp D                    # terminal 2
  python run.py --csv data/mixalldata_clean.csv --exp ablation             # terminal 1 (after A)
  python run.py --csv data/mixalldata_clean.csv --exp C --veremi-dir data/veremi_original   # terminal 2
  python run.py --exp figures report
Add --quick for a fast smoke test on 5% of vehicles.
"""
import argparse
import copy
import json
import os
import sys
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hpmds import data as Dm                      # noqa: E402
from hpmds import evaluate as E                   # noqa: E402
from hpmds import methods as Me                   # noqa: E402
from hpmds import models as Mo                    # noqa: E402

LOGF = None


def log(*a):
    s = time.strftime("%H:%M:%S ") + " ".join(str(x) for x in a)
    print(s, flush=True)
    if LOGF:
        with open(LOGF, "a", encoding="utf-8") as f:
            f.write(s + "\n")


def jdump(obj, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1, default=_json_default)


def jappend(obj, path):
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, default=_json_default) + "\n")


def jread(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    return str(o)


# ------------------------------------------------------------------------------------------ data
class Data:
    def __init__(self, args):
        self.args = args
        self.tag = ("quick" if args.quick else "full") + ("_pseudo" if args.stream_by == "pseudo" else "")
        self._win = {}
        self._msgs = None
        self._feat = None

    def msgs(self):
        if self._msgs is None:
            p = Dm.cache_path(self.args.cache, f"msgs_{self.tag}.pkl")
            if os.path.exists(p):
                import pandas as pd
                self._msgs = pd.read_pickle(p)
                log(f"[data] loaded cached messages {p} ({len(self._msgs):,})")
            else:
                if not self.args.csv:
                    raise SystemExit("--csv is required the first time (no cache yet)")
                m = Dm.load_veremi_csv(self.args.csv, stream_by=self.args.stream_by)
                if self.args.quick:
                    rng = np.random.default_rng(0)
                    vs = m["sender"].unique()
                    keep = rng.choice(vs, max(50, int(len(vs) * self.args.quick_frac)), replace=False)
                    m = m[m["sender"].isin(keep)].reset_index(drop=True)
                    log(f"[data] quick mode: {len(keep)} vehicles, {len(m):,} messages")
                m["split"] = Dm.grouped_split(m, seed=0)
                m.to_pickle(p)
                self._msgs = m
        return self._msgs

    def feats(self):
        if self._feat is None:
            p = Dm.cache_path(self.args.cache, f"feat_{self.tag}.pkl")
            if os.path.exists(p):
                import pandas as pd
                self._feat = pd.read_pickle(p)
            else:
                t0 = time.time()
                self._feat = Dm.add_message_features(self.msgs())
                self._feat.to_pickle(p)
                log(f"[data] message features computed in {time.time() - t0:.0f}s")
        return self._feat

    def windows(self, w=None, feat=None, key=None):
        w = w or self.args.w
        key = key or f"{self.tag}_w{w}"
        if key in self._win:
            return self._win[key]
        p = Dm.cache_path(self.args.cache, f"win_{key}_s{self.args.train_stride}-{self.args.test_stride}.npz")
        if os.path.exists(p) and feat is None:
            z = np.load(p)
            D = {k: z[k] for k in z.files}
        else:
            t0 = time.time()
            f = self.feats() if feat is None else feat
            D = {}
            for sp, st in (("tr", self.args.train_stride), ("va", self.args.test_stride), ("te", self.args.test_stride)):
                name = {"tr": "train", "va": "val", "te": "test"}[sp]
                X, y, g, _ = Dm.make_windows(f, w=w, stride=st, split=name)
                D["X" + sp], D["y" + sp], D["g" + sp] = X, y, g
            if feat is None:
                np.savez(p, **D)
            log(f"[data] windows w={w} built in {time.time() - t0:.0f}s: "
                f"train {len(D['ytr']):,} val {len(D['yva']):,} test {len(D['yte']):,}")
        self._win[key] = D
        return D


def describe(D, out):
    s = {}
    for sp in ("tr", "va", "te"):
        y = D["y" + sp]
        s[sp] = dict(windows=int(len(y)), vehicles=int(len(np.unique(D["g" + sp]))),
                     classes={Dm.CLASS_NAMES.get(int(c), int(c)): int((y == c).sum()) for c in np.unique(y)})
    jdump(s, os.path.join(out, "dataset_summary.json"))
    return s


# ------------------------------------------------------------------------------------- evaluation
def evaluate_method(meth, D, pred=None):
    yte = D["yte"]
    pr = pred if pred is not None else meth.predict(D["Xte"])
    yb = (yte > 0).astype(int)
    row = dict(binary=E.binary_metrics(yb, pr["attack"], pr.get("score")),
               per_class_detection={Dm.CLASS_NAMES.get(k, k): v for k, v in
                                    E.per_class_detection(yte, pr["attack"]).items()})
    if "label" in pr and meth.multiclass:
        labels = sorted(int(c) for c in np.unique(D["ytr"]))
        row["multiclass"] = E.multiclass_metrics(yte, pr["label"], labels)
        row["per_class_f1"] = {Dm.CLASS_NAMES.get(k, k): v for k, v in E.per_class_f1(yte, pr["label"], labels).items()}
        if (pr["label"] == -1).any():
            row["unknown_rate"] = float((pr["label"] == -1).mean())
    return row, pr


SEED0 = {}


def fit_seed0(D, names, args, tag="A"):
    """Fit (or reuse) the seed-0 models of Exp A for E, F and stats."""
    out = {}
    for n in names:
        k = (tag, n)
        if k not in SEED0:
            log(f"  fitting seed-0 {n} for downstream experiments")
            SEED0[k] = Me.make(n, args.epochs, log).fit(D, seed=0)
        out[n] = SEED0[k]
    return out


def exp_A(args, data):
    out = os.path.join(args.out, "expA")
    os.makedirs(out, exist_ok=True)
    D = data.windows()
    describe(D, args.out)
    mpath = os.path.join(out, "metrics.jsonl")
    done = {(r["model"], r["seed"]) for r in jread(mpath)}
    names = Me.CORE + ([] if args.no_secondary else Me.SECONDARY)
    preds0 = dict(y=D["yte"], g=D["gte"])
    extras = {}
    for seed in range(args.seeds):
        for n in names:
            if n in Me.SECONDARY and seed > 0:
                continue
            if (n, seed) in done and not (seed == 0 and not os.path.exists(os.path.join(out, "preds_seed0.npz"))):
                log(f"[A] skip {n} seed {seed} (done)")
                continue
            log(f"[A] {n} seed {seed}")
            meth = Me.make(n, args.epochs, log).fit(D, seed=seed)
            row, pr = evaluate_method(meth, D)
            row.update(model=n, seed=seed, fit_time=getattr(meth, "fit_time", None),
                       n_params=meth.n_params(), size_bytes=meth.size_bytes())
            if (n, seed) not in done:
                jappend(row, mpath)
            b = row["binary"]
            log(f"    F1 {b['f1']:.4f} MCC {b['mcc']:.4f} FPR {b['fpr']:.4f}"
                + (f" macroF1 {row['multiclass']['macro_f1']:.4f}" if "multiclass" in row else ""))
            if seed == 0:
                SEED0[("A", n)] = meth
                for k in ("score", "attack", "label", "s_ae", "s_phys", "maxp"):
                    if k in pr:
                        preds0[f"{n}|{k}"] = np.asarray(pr[k])
                if n == "HP-MDS":
                    extras = dict(alpha=meth.alpha, thr=meth.thr, pct=meth.pct, tau=getattr(meth, "tau", None),
                                  alpha_curve_val=meth.alpha_curve, hist_ae=meth.hist_ae,
                                  hist_clf=getattr(meth, "hist_clf", None), classes=meth.classes)
                    jdump(extras, os.path.join(out, "hpmds_seed0_details.json"))
                if hasattr(meth, "hist"):
                    jdump(meth.hist, os.path.join(out, f"hist_{n}.json"))
                np.savez_compressed(os.path.join(out, "preds_seed0.npz"), **preds0)


def exp_D(args, data):
    """Open-set: hold out attack types completely from training and validation."""
    out = os.path.join(args.out, "expD")
    os.makedirs(out, exist_ok=True)
    D = data.windows()
    present = set(int(c) for c in np.unique(D["ytr"]))
    holdouts = [[c for c in h if c in present] for h in ([2, 6, 11, 16], [3, 8, 12, 15], [1, 9, 14, 17])]
    holdouts = [h for h in holdouts if h]
    mpath = os.path.join(out, "metrics.jsonl")
    done = {(r["model"], r["seed"], r["holdout"]) for r in jread(mpath)}
    names = ["HP-MDS", "RandomForest", "XGBoost", "Bi-LSTM", "LSTM-AE", "IsolationForest", "Physics-only"]
    for hi, H in enumerate(holdouts):
        ktr, kva = ~np.isin(D["ytr"], H), ~np.isin(D["yva"], H)
        DK = dict(Xtr=D["Xtr"][ktr], ytr=D["ytr"][ktr], gtr=D["gtr"][ktr],
                  Xva=D["Xva"][kva], yva=D["yva"][kva], gva=D["gva"][kva], Xte=D["Xte"], yte=D["yte"], gte=D["gte"])
        yte = D["yte"]
        unseen, known_att, ben = np.isin(yte, H), (yte > 0) & ~np.isin(yte, H), yte == 0
        for seed in range(args.open_seeds):
            for n in names:
                if (n, seed, hi) in done:
                    continue
                log(f"[D] holdout {hi} {[Dm.CLASS_NAMES[c] for c in H]} {n} seed {seed}")
                meth = Me.make(n, args.epochs, log).fit(DK, seed=seed)
                pr = meth.predict(D["Xte"])
                pv = meth.predict(DK["Xva"])
                a, s = pr["attack"], pr["score"]
                thr5 = np.percentile(pv["score"][DK["yva"] == 0], 95)
                row = dict(model=n, seed=seed, holdout=hi, holdout_classes=[Dm.CLASS_NAMES[c] for c in H],
                           unseen_recall=float(a[unseen].mean()), known_recall=float(a[known_att].mean()),
                           fpr=float(a[ben].mean()),
                           unseen_recall_at5fpr=float((s[unseen] > thr5).mean()),
                           auroc_benign_vs_unseen=float(E.roc_auc_score(np.r_[np.zeros(ben.sum()), np.ones(unseen.sum())],
                                                                        np.r_[s[ben], s[unseen]])),
                           per_unseen_class_recall={Dm.CLASS_NAMES[c]: float(a[yte == c].mean()) for c in H})
                if "label" in pr and meth.multiclass:
                    lab = pr["label"]
                    known_lbls = sorted(int(c) for c in np.unique(DK["ytr"]))
                    kn = ~unseen
                    row["known_macro_f1"] = float(E.f1_score(yte[kn], lab[kn], labels=known_lbls, average="macro",
                                                             zero_division=0))
                    row["unseen_labelled_unknown"] = float((lab[unseen] == -1).mean())
                    row["unseen_labelled_benign"] = float((lab[unseen] == 0).mean())
                    row["unseen_misassigned_known"] = float((lab[unseen] > 0).mean())
                    # open-set rejection AUROC: known attacks (0) vs unseen attacks (1) among attack windows
                    att = unseen | known_att
                    yo = unseen[att].astype(int)
                    oset = {}
                    if "maxp" in pr:
                        oset["MSP"] = E.roc_auc_score(yo, (1 - pr["maxp"])[att])
                    if "energy" in pr:
                        oset["Energy"] = E.roc_auc_score(yo, pr["energy"][att])
                    if n == "HP-MDS":
                        from scipy.stats import rankdata
                        oset["Fused-score"] = E.roc_auc_score(yo, pr["score"][att])
                        oset["MSP+Fused (rank avg)"] = E.roc_auc_score(
                            yo, rankdata((1 - pr["maxp"])[att]) + rankdata(pr["score"][att]))
                    row["openset_auroc"] = oset
                jappend(row, mpath)
                log(f"    unseen recall {row['unseen_recall']:.3f} known recall {row['known_recall']:.3f} "
                    f"FPR {row['fpr']:.4f}" + (f" unseen->unknown {row.get('unseen_labelled_unknown', 0):.3f}"
                                              if n == "HP-MDS" else ""))


NOISE_LEVELS = [("clean", 0, 0, 0), ("pos1_spd0.2", 1, 0.2, 0), ("pos3_spd0.5", 3, 0.5, 0),
                ("pos5_spd1", 5, 1.0, 0), ("pos10_spd2", 10, 2.0, 0), ("drop10%", 0, 0, 0.1),
                ("drop30%", 0, 0, 0.3), ("pos3_spd0.5+drop10%", 3, 0.5, 0.1)]


def exp_E(args, data):
    out = os.path.join(args.out, "expE")
    os.makedirs(out, exist_ok=True)
    D = data.windows()
    names = ["HP-MDS", "RandomForest", "XGBoost", "Bi-LSTM", "LSTM-AE", "Physics-only"]
    ms = fit_seed0(D, names, args)
    msgs = data.msgs()
    test_msgs = msgs[msgs["split"] == "test"]
    rows = []
    for lvl, ps, ss, dr in NOISE_LEVELS:
        nm = Dm.add_noise(test_msgs, ps, ss, dr, seed=1)
        f = Dm.add_message_features(nm)
        X, y, g, _ = Dm.make_windows(f, w=args.w, stride=args.test_stride)
        yb = (y > 0).astype(int)
        for n, meth in ms.items():
            pr = meth.predict(X)
            r = E.binary_metrics(yb, pr["attack"], pr.get("score"))
            row = dict(level=lvl, pos_sigma=ps, spd_sigma=ss, drop=dr, model=n, **r)
            rows.append(row)
            log(f"[E] {lvl:22s} {n:15s} F1 {r['f1']:.4f} FPR {r['fpr']:.4f} recall {r['recall']:.4f}")
        jdump(rows, os.path.join(out, "noise.json"))


def exp_F(args, data):
    import torch
    out = os.path.join(args.out, "expF")
    os.makedirs(out, exist_ok=True)
    D = data.windows()
    names = Me.CORE
    ms = fit_seed0(D, names, args)
    torch.set_num_threads(1)
    X = D["Xte"]
    rng = np.random.default_rng(0)
    idx = rng.choice(len(X), min(400, len(X)), replace=False)
    rows = []
    for n, meth0 in ms.items():
        meth = copy.deepcopy(meth0)
        for attr in ("ae", "clf", "model"):
            obj = getattr(meth, attr, None)
            if isinstance(obj, torch.nn.Module):
                obj.cpu()
        mdl = getattr(meth, "model", None)
        if mdl is not None and hasattr(mdl, "n_jobs"):
            try:
                mdl.set_params(n_jobs=1)
            except Exception:
                pass
        if n == "XGBoost":
            try:
                meth.model.set_params(device="cpu", n_jobs=1)
            except Exception:
                pass
        variants = [(n, meth)]
        if n == "HP-MDS":
            s1 = copy.deepcopy(meth)
            s1.use_stage2 = False
            variants.append(("HP-MDS (Stage 1 only)", s1))
        for vn, vm in variants:
            for i in idx[:20]:
                vm.predict(X[i:i + 1])  # warm-up
            ts = []
            for i in idx:
                t0 = time.perf_counter()
                vm.predict(X[i:i + 1])
                ts.append((time.perf_counter() - t0) * 1000)
            nb = min(20000, len(X))
            t0 = time.perf_counter()
            vm.predict(X[:nb])
            thr = nb / (time.perf_counter() - t0)
            row = dict(model=vn, latency_ms_median=float(np.median(ts)), latency_ms_p95=float(np.percentile(ts, 95)),
                       throughput_windows_per_s=float(thr), n_params=vm.n_params() if vn == n else Mo.n_params(vm.ae),
                       size_mb=(vm.size_bytes() / 1e6) if vn == n else None,
                       within_100ms_budget=bool(np.percentile(ts, 95) < 100))
            rows.append(row)
            log(f"[F] {vn:24s} median {row['latency_ms_median']:.3f} ms p95 {row['latency_ms_p95']:.3f} ms "
                f"thr {thr:,.0f}/s params {row['n_params']}")
        jdump(rows, os.path.join(out, "efficiency.json"))
    # HP-MDS average cost: Stage 2 only on flagged windows
    hp = ms["HP-MDS"]
    flagged = float(hp.predict(D["Xte"])["flag"].mean())
    jdump(dict(rows=rows, hpmds_flag_rate=flagged, cpu_threads=1, device=str(Mo.DEVICE)),
          os.path.join(out, "efficiency.json"))
    torch.set_num_threads(os.cpu_count() or 4)


def exp_stats(args, data):
    out = os.path.join(args.out, "stats")
    os.makedirs(out, exist_ok=True)
    p = np.load(os.path.join(args.out, "expA", "preds_seed0.npz"))
    y, g = p["y"], p["g"]
    yb = (y > 0).astype(int)
    models = sorted({k.split("|")[0] for k in p.files if "|" in k})
    labels = sorted(int(c) for c in np.unique(y))
    ug, inv = np.unique(g, return_inverse=True)
    res = dict(bootstrap={}, mcnemar={}, wilcoxon={})
    rng = np.random.default_rng(0)
    B = 1000
    W = rng.multinomial(len(ug), np.ones(len(ug)) / len(ug), size=B).astype(float)  # (B, G) group weights
    for m in models:
        a = p[f"{m}|attack"].astype(int)
        tp = np.bincount(inv, (a == 1) & (yb == 1), len(ug))
        fp = np.bincount(inv, (a == 1) & (yb == 0), len(ug))
        fn = np.bincount(inv, (a == 0) & (yb == 1), len(ug))
        TP, FP, FN = W @ tp, W @ fp, W @ fn
        f1 = 2 * TP / np.maximum(2 * TP + FP + FN, 1e-9)
        r = dict(binary_f1=float(E.f1_score(yb, a)), binary_f1_ci=[float(np.percentile(f1, 2.5)), float(np.percentile(f1, 97.5))])
        if f"{m}|label" in p.files:
            lab = p[f"{m}|label"]
            K = []
            for c in labels:
                tpc = np.bincount(inv, (lab == c) & (y == c), len(ug))
                fpc = np.bincount(inv, (lab == c) & (y != c), len(ug))
                fnc = np.bincount(inv, (lab != c) & (y == c), len(ug))
                T, Fp, Fn = W @ tpc, W @ fpc, W @ fnc
                K.append(2 * T / np.maximum(2 * T + Fp + Fn, 1e-9))
            mf = np.mean(K, axis=0)
            r["macro_f1"] = float(E.f1_score(y, lab, labels=labels, average="macro", zero_division=0))
            r["macro_f1_ci"] = [float(np.percentile(mf, 2.5)), float(np.percentile(mf, 97.5))]
        res["bootstrap"][m] = r
    # McNemar (binary correctness), HP-MDS vs each baseline
    ref = "HP-MDS"
    cref = p[f"{ref}|attack"].astype(int) == yb
    others = [m for m in models if m != ref]
    pv = []
    for m in others:
        chi, pval = E.mcnemar(cref, p[f"{m}|attack"].astype(int) == yb)
        res["mcnemar"][m] = dict(chi2=chi, p=pval)
        pv.append(pval)
    for m, adj in zip(others, E.holm(pv)):
        res["mcnemar"][m]["p_holm"] = float(adj)
    # Wilcoxon over attack classes: per-class detection rate averaged over seeds (from metrics.jsonl)
    rows = jread(os.path.join(args.out, "expA", "metrics.jsonl"))
    per = {}
    for r in rows:
        per.setdefault(r["model"], []).append(r["per_class_detection"])
    mean_det = {m: {c: np.mean([d[c] for d in v if c in d]) for c in v[0]} for m, v in per.items()}
    pv = []
    for m in others:
        if m not in mean_det or ref not in mean_det:
            continue
        cls = [c for c in mean_det[ref] if c in mean_det[m]]
        st, pval = E.wilcoxon([mean_det[ref][c] for c in cls], [mean_det[m][c] for c in cls])
        res["wilcoxon"][m] = dict(stat=st, p=pval, n_classes=len(cls),
                                  mean_diff=float(np.mean([mean_det[ref][c] - mean_det[m][c] for c in cls])))
        pv.append(pval)
    for m, adj in zip([m for m in others if m in res["wilcoxon"]], E.holm(pv)):
        res["wilcoxon"][m]["p_holm"] = float(adj)
    jdump(res, os.path.join(out, "stats.json"))
    log("[stats] written", os.path.join(out, "stats.json"))


def _hp_eval(hp, D, alpha, pr=None):
    """Evaluate HP-MDS Stage-1 score with a fixed alpha (threshold re-tuned on validation)."""
    vs = hp.val_scores
    sv = alpha * hp.zA(vs["sae"]) + (1 - alpha) * hp.zP(vs["sph"])
    thr, pct, _ = E.best_threshold(sv, (vs["y"] > 0).astype(int))
    pr = pr if pr is not None else hp.predict(D["Xte"])
    st = alpha * hp.zA(pr["s_ae"]) + (1 - alpha) * hp.zP(pr["s_phys"])
    return E.binary_metrics((D["yte"] > 0).astype(int), (st > thr).astype(int), st), st


def exp_ablation(args, data):
    out = os.path.join(args.out, "ablation")
    os.makedirs(out, exist_ok=True)
    apath = os.path.join(out, "ablation.jsonl")
    done = {(r["study"], r["variant"], r["seed"]) for r in jread(apath)}
    D = data.windows()
    yb = (D["yte"] > 0).astype(int)
    labels = sorted(int(c) for c in np.unique(D["ytr"]))

    def rec(study, variant, seed, b, mc=None, extra=None):
        row = dict(study=study, variant=variant, seed=seed, **{k: b[k] for k in
                   ("f1", "mcc", "precision", "recall", "fpr") if k in b}, roc_auc=b.get("roc_auc"))
        if mc is not None:
            row["macro_f1"] = mc
        if extra:
            row.update(extra)
        jappend(row, apath)
        log(f"[abl] {study:12s} {variant:32s} seed {seed} F1 {b['f1']:.4f}" + (f" macroF1 {mc:.4f}" if mc else ""))

    def mf1(lab):
        return float(E.f1_score(D["yte"], lab, labels=labels, average="macro", zero_division=0))

    for seed in range(args.abl_seeds):
        # 1) components (one fitted model, several read-outs)
        if ("components", "Full HP-MDS", seed) not in done:
            hp = Me.HPMDS(epochs=args.epochs, log=log).fit(D, seed=seed)
            pr = hp.predict(D["Xte"])
            b_ae, _ = _hp_eval(hp, D, 1.0, pr)
            b_ph, _ = _hp_eval(hp, D, 0.0, pr)
            b_fu = E.binary_metrics(yb, pr["flag"], pr["score"])
            rec("components", "Stage1: AE score only", seed, b_ae)
            rec("components", "Stage1: physics score only", seed, b_ph)
            rec("components", f"Stage1: fused (alpha={hp.alpha})", seed, b_fu)
            closed = np.where(pr["flag"] == 0, 0, hp.classes[np.argmax(Mo.softmax(Mo.predict_logits(
                hp.clf, hp.sc2.transform(D["Xte"]))), 1)])
            rec("components", "Stage1+Stage2, no open-set", seed, E.binary_metrics(yb, (closed != 0).astype(int)),
                mf1(closed))
            s2only = hp.classes[np.argmax(Mo.predict_logits(hp.clf, hp.sc2.transform(D["Xte"])), 1)]
            rec("components", "Stage2 only (closed-set clf)", seed, E.binary_metrics(yb, (s2only != 0).astype(int)),
                mf1(s2only))
            rec("components", "Full HP-MDS", seed, E.binary_metrics(yb, pr["attack"], pr["score"]), mf1(pr["label"]),
                dict(unknown_rate=float((pr["label"] == -1).mean())))
            # Extension: alarm if Stage 1 flags OR Stage 2 confidently predicts a known attack
            # (confidence cut-off chosen on validation F1 only)
            def s2(X):
                P = Mo.softmax(Mo.predict_logits(hp.clf, hp.sc2.transform(X)))
                return hp.classes[P.argmax(1)], P.max(1)
            pv = hp.predict(D["Xva"])
            av, mv = s2(D["Xva"])
            ybv = (D["yva"] > 0).astype(int)
            best_t, best_f = 1.01, -1
            for t in (0.5, 0.6, 0.7, 0.8, 0.9, 0.95, 0.99):
                f = E.f1_score(ybv, (pv["flag"].astype(bool) | ((av != 0) & (mv >= t))).astype(int))
                if f > best_f:
                    best_f, best_t = f, t
            at, mt = s2(D["Xte"])
            extra_alarm = (at != 0) & (mt >= best_t)
            ulab = np.where(pr["flag"].astype(bool), pr["label"], np.where(extra_alarm, at, 0))
            rec("components", "HP-MDS + confident Stage2 alarms (union)", seed,
                E.binary_metrics(yb, (ulab != 0).astype(int)), mf1(ulab), dict(conf_cutoff=best_t))
            if seed == 0:
                # sweeps on the seed-0 model
                sweep = dict(alpha=[], pct=[])
                for a in np.round(np.arange(0, 1.01, 0.1), 2):
                    b, _ = _hp_eval(hp, D, float(a), pr)
                    sweep["alpha"].append(dict(alpha=float(a), test_f1=b["f1"], test_auc=b.get("roc_auc"),
                                               val_f1=dict(hp.alpha_curve).get(float(a))))
                sb = hp.val_scores
                sv = hp._fused(sb["sae"], sb["sph"])
                for pct in (80, 90, 95, 97, 98, 99, 99.5, 99.9):
                    thr = np.percentile(sv[sb["y"] == 0], pct)
                    b = E.binary_metrics(yb, (pr["score"] > thr).astype(int))
                    sweep["pct"].append(dict(pct=pct, f1=b["f1"], fpr=b["fpr"], recall=b["recall"], precision=b["precision"]))
                jdump(sweep, os.path.join(out, "sweeps.json"))
                np.savez_compressed(os.path.join(out, "scores_seed0.npz"), y=D["yte"], s_ae=pr["s_ae"],
                                    s_phys=pr["s_phys"], fused=pr["score"])
            del hp
        # 2) feature sets for the Stage-1 autoencoder
        for fs in ("A", "B", "C"):
            if ("features", f"AE on FV-{fs}", seed) in done:
                continue
            hp = Me.HPMDS(fs_ae=fs, use_stage2=False, epochs=args.epochs).fit(D, seed=seed)
            pr = hp.predict(D["Xte"])
            b_ae, _ = _hp_eval(hp, D, 1.0, pr)
            rec("features", f"AE on FV-{fs}", seed, b_ae)
            rec("features", f"AE on FV-{fs} + physics fusion", seed, E.binary_metrics(yb, pr["flag"], pr["score"]))
        # 3) latent size
        for lat in (8, 32):
            if ("latent", f"latent={lat}", seed) in done:
                continue
            hp = Me.HPMDS(latent=lat, use_stage2=False, epochs=args.epochs).fit(D, seed=seed)
            pr = hp.predict(D["Xte"])
            rec("latent", f"latent={lat}", seed, E.binary_metrics(yb, pr["flag"], pr["score"]),
                extra=dict(n_params=hp.n_params()))
        # 4) imbalance handling in Stage 2
        for mode in ("none", "smote"):
            if ("imbalance", f"Stage2 {mode}", seed) in done:
                continue
            hp = Me.HPMDS(imbalance=mode, epochs=args.epochs).fit(D, seed=seed)
            pr = hp.predict(D["Xte"])
            rec("imbalance", f"Stage2 {mode}", seed, E.binary_metrics(yb, pr["attack"], pr["score"]), mf1(pr["label"]))
        # 5) physics features for a strong baseline
        for fs in ("B", "C"):
            if ("xgb_phys", f"XGBoost FV-{fs}", seed) in done:
                continue
            xm = Me.XGB(fs=fs).fit(D, seed=seed)
            pr = xm.predict(D["Xte"])
            rec("xgb_phys", f"XGBoost FV-{fs}", seed, E.binary_metrics(yb, pr["attack"], pr["score"]), mf1(pr["label"]))
    # 6) window size (rebuild windows)
    for w in (5, 15, 20):
        Dw = data.windows(w=w)
        ybw = (Dw["yte"] > 0).astype(int)
        for seed in range(args.abl_seeds):
            if ("window", f"w={w}", seed) in done:
                continue
            hp = Me.HPMDS(epochs=args.epochs).fit(Dw, seed=seed)
            pr = hp.predict(Dw["Xte"])
            rec("window", f"w={w}", seed, E.binary_metrics(ybw, pr["attack"], pr["score"]),
                float(E.f1_score(Dw["yte"], pr["label"], labels=labels, average="macro", zero_division=0)))
        data._win.pop(f"{data.tag}_w{w}", None)


RUSH_HOURS = {7, 8, 9, 17, 18, 19}


def _density_of_vehicles(data):
    m = data.msgs()
    h = (m.groupby("sender")["t"].min() // 3600 % 24).astype(int)
    return {int(k): ("rush" if v in RUSH_HOURS else "offpeak") for k, v in h.items()}


def exp_B(args, data):
    """Density shift: train on off-peak-hour vehicles, test on rush-hour vehicles (and the reverse)."""
    out = os.path.join(args.out, "expB")
    os.makedirs(out, exist_ok=True)
    D = data.windows()
    dens = _density_of_vehicles(data)
    dv = {sp: np.array([dens[int(x)] for x in D["g" + sp]]) for sp in ("tr", "va", "te")}
    mpath = os.path.join(out, "metrics.jsonl")
    done = {(r["model"], r["seed"], r["direction"]) for r in jread(mpath)}
    names = ["HP-MDS", "RandomForest", "XGBoost", "Bi-LSTM", "LSTM-AE", "IsolationForest", "Physics-only"]
    for src, tgt in (("offpeak", "rush"), ("rush", "offpeak")):
        direction = f"{src}->{tgt}"
        DS = dict(Xtr=D["Xtr"][dv["tr"] == src], ytr=D["ytr"][dv["tr"] == src], gtr=D["gtr"][dv["tr"] == src],
                  Xva=D["Xva"][dv["va"] == src], yva=D["yva"][dv["va"] == src], gva=D["gva"][dv["va"] == src])
        tin, tout = dv["te"] == src, dv["te"] == tgt
        labels = sorted(int(c) for c in np.unique(DS["ytr"]))
        for seed in range(args.b_seeds):
            for n in names:
                if (n, seed, direction) in done:
                    continue
                log(f"[B] {direction} {n} seed {seed}")
                meth = Me.make(n, args.epochs, log).fit(DS, seed=seed)
                pr = meth.predict(D["Xte"])
                row = dict(model=n, seed=seed, direction=direction, n_train=int(len(DS["ytr"])))
                for nm, msk in (("source_test", tin), ("target_test", tout)):
                    yt = D["yte"][msk]
                    row[nm] = E.binary_metrics((yt > 0).astype(int), pr["attack"][msk], pr["score"][msk])
                    if "label" in pr and meth.multiclass:
                        row[nm]["macro_f1"] = float(E.f1_score(yt, pr["label"][msk], labels=labels, average="macro",
                                                               zero_division=0))
                jappend(row, mpath)
                log(f"    source F1 {row['source_test']['f1']:.4f} -> target F1 {row['target_test']['f1']:.4f} "
                    f"(target FPR {row['target_test']['fpr']:.4f})")


def exp_G(args, data):
    """Timeliness (detection delay per attacker vehicle) and error / failure analysis."""
    out = os.path.join(args.out, "expG")
    os.makedirs(out, exist_ok=True)
    D = data.windows()
    names = ["HP-MDS", "RandomForest", "XGBoost", "Bi-LSTM", "Physics-only"]
    ms = fit_seed0(D, names, args)
    f = data.feats()
    w = args.w
    X1, y1, g1, st1 = Dm.make_windows(f, w=w, stride=1, split="test")
    tt = f["t"].values
    stream = f["stream"].values
    first_idx = {}
    sidx = np.where(f["split"].values == "test")[0]
    s_streams = stream[sidx]
    newst = np.r_[True, s_streams[1:] != s_streams[:-1]]
    for i in sidx[newst]:
        first_idx[int(stream[i])] = int(i)
    win_stream = stream[st1]
    delay = {}
    for n, meth in ms.items():
        a = meth.predict(X1)["attack"].astype(bool)
        rows_att, n_att, n_det, ben_veh, ben_alarm = [], 0, 0, 0, 0
        order = np.argsort(st1, kind="stable")
        ws, wa, wy = win_stream[order], a[order], y1[order]
        wst = st1[order]
        bounds = np.r_[0, np.where(ws[1:] != ws[:-1])[0] + 1, len(ws)]
        for b0, b1 in zip(bounds[:-1], bounds[1:]):
            s = int(ws[b0])
            lab = int(wy[b0:b1].max())
            hit = np.where(wa[b0:b1])[0]
            if lab == 0:
                ben_veh += 1
                ben_alarm += int(len(hit) > 0)
                continue
            n_att += 1
            if len(hit):
                n_det += 1
                end = int(wst[b0 + hit[0]]) + w - 1
                rows_att.append(dict(cls=lab, msgs=end - first_idx[s] + 1, secs=float(tt[end] - tt[first_idx[s]])))
        msgs = np.array([r["msgs"] for r in rows_att]) if rows_att else np.array([np.nan])
        secs = np.array([r["secs"] for r in rows_att]) if rows_att else np.array([np.nan])
        per_cls = {}
        for r in rows_att:
            per_cls.setdefault(Dm.CLASS_NAMES[r["cls"]], []).append(r["msgs"])
        delay[n] = dict(attacker_streams=n_att, detected_streams=n_det, stream_detection_rate=n_det / max(1, n_att),
                        benign_streams=ben_veh, benign_streams_with_alarm=ben_alarm,
                        benign_stream_false_alarm_rate=ben_alarm / max(1, ben_veh),
                        delay_msgs_median=float(np.nanmedian(msgs)), delay_msgs_p90=float(np.nanpercentile(msgs, 90)),
                        delay_s_median=float(np.nanmedian(secs)), delay_s_p90=float(np.nanpercentile(secs, 90)),
                        delay_msgs_by_attack={k: float(np.median(v)) for k, v in per_cls.items()},
                        delay_msgs_all=msgs.tolist())
        log(f"[G] {n:14s} stream detection {delay[n]['stream_detection_rate']:.3f} median delay "
            f"{delay[n]['delay_msgs_median']:.0f} msgs / {delay[n]['delay_s_median']:.1f} s; benign streams with alarm "
            f"{delay[n]['benign_stream_false_alarm_rate']:.3f}")
    jdump(delay, os.path.join(out, "detection_delay.json"))
    # ---- error analysis on the standard test windows
    yte = D["yte"]
    yb = yte > 0
    iv, idt = Dm.FV_C.index("v"), Dm.FV_C.index("dt")
    vmean = D["Xte"][:, :, iv].mean(1)
    dtmean = D["Xte"][:, :, idt].mean(1)
    vb = np.digitize(vmean, [2, 8, 14, 20, 30])
    vnames = ["<2", "2-8", "8-14", "14-20", "20-30", ">30"]
    dtb = np.digitize(dtmean, [0.3, 0.9, 1.1, 2.0])
    dtnames = ["<0.3", "0.3-0.9", "0.9-1.1", "1.1-2", ">2"]
    err = {}
    for n, meth in ms.items():
        a = meth.predict(D["Xte"])["attack"].astype(bool)
        e = dict(by_speed={}, by_gap={})
        for key, bins, bn in (("by_speed", vb, vnames), ("by_gap", dtb, dtnames)):
            for i, nm in enumerate(bn):
                sel = bins == i
                e[key][nm] = dict(windows=int(sel.sum()),
                                  fnr=float((~a[sel & yb]).mean()) if (sel & yb).any() else None,
                                  fpr=float(a[sel & ~yb].mean()) if (sel & ~yb).any() else None)
        err[n] = e
    # failure taxonomy for HP-MDS
    hp = ms["HP-MDS"]
    pr = hp.predict(D["Xte"])
    lab = pr["label"]
    small_offset = {2, 6, 9, 12}  # ConstPosOffset, ConstSpeedOffset, EventualStop, DelayedMessages
    tax = {
        "(a) missed subtle/small-offset attacks": int(((lab == 0) & np.isin(yte, list(small_offset))).sum()),
        "(a') missed other attacks": int(((lab == 0) & yb & ~np.isin(yte, list(small_offset))).sum()),
        "(b) benign windows flagged": int(((lab != 0) & ~yb).sum()),
        "(c) benign flagged with irregular beacon gap (>1.1 s)": int(((lab != 0) & ~yb & (dtmean > 1.1)).sum()),
        "(d) attack assigned the wrong attack class": int(((lab > 0) & yb & (lab != yte)).sum()),
        "(e) attack labelled 'unknown'": int(((lab == -1) & yb).sum()),
        "correct": int((lab == yte).sum()), "total": int(len(yte)),
    }
    # per-feature reconstruction error of the Stage-1 autoencoder (benign vs attack)
    import torch
    Xs = hp.sc1.transform(D["Xte"][:, :, hp.fi])
    rec_err = []
    with torch.no_grad():
        for i in range(0, len(Xs), 8192):
            xb = torch.from_numpy(Xs[i:i + 8192]).to(Mo.DEVICE)
            rec_err.append(((hp.ae(xb) - xb) ** 2).mean(1).cpu().numpy())
    rec_err = np.concatenate(rec_err)
    feats = [Dm.FV_C[i] for i in hp.fi]
    per_feat = {feats[j]: dict(benign=float(rec_err[~yb, j].mean()), attack=float(rec_err[yb, j].mean()),
                               ratio=float(rec_err[yb, j].mean() / max(rec_err[~yb, j].mean(), 1e-9)))
                for j in range(len(feats))}
    # XGBoost feature importance (gain)
    xg = ms["XGBoost"]
    fnames = [f"{s}({Dm.FV_C[i]})" for s in ("mean", "std", "min", "max") for i in Dm.feature_index(xg.fs)]
    imp = dict(sorted(zip(fnames, map(float, xg.model.feature_importances_)), key=lambda kv: -kv[1])[:20])
    jdump(dict(errors_by_context=err, hpmds_failure_taxonomy=tax, ae_per_feature_recon_error=per_feat,
               xgboost_top20_importance=imp), os.path.join(out, "error_analysis.json"))
    log("[G] error analysis written")


def exp_onnx(args, data):
    """Export the neural models to ONNX and time them with onnxruntime on one CPU thread."""
    import torch
    out = os.path.join(args.out, "expF")
    os.makedirs(out, exist_ok=True)
    try:
        import onnxruntime as ort
    except ImportError:
        log("[onnx] onnxruntime not installed - skipped")
        return
    D = data.windows()
    ms = fit_seed0(D, ["HP-MDS", "Bi-LSTM", "LSTM-AE"], args)
    hp = ms["HP-MDS"]

    class Score(torch.nn.Module):
        def __init__(self, m):
            super().__init__()
            self.m = m

        def forward(self, x):
            return ((self.m(x) - x) ** 2).mean(dim=(1, 2))

    w = args.w
    items = [("HP-MDS Stage1 AE", Score(copy.deepcopy(hp.ae).cpu().eval()), len(hp.fi)),
             ("HP-MDS Stage2 classifier", copy.deepcopy(hp.clf).cpu().eval(), len(Dm.FV_C)),
             ("Bi-LSTM", copy.deepcopy(ms["Bi-LSTM"].model).cpu().eval(), len(ms["Bi-LSTM"].fi)),
             ("LSTM-AE", Score(copy.deepcopy(ms["LSTM-AE"].model).cpu().eval()), len(ms["LSTM-AE"].fi))]
    rows = []
    so = ort.SessionOptions()
    so.intra_op_num_threads = 1
    so.inter_op_num_threads = 1
    for name, mod, f in items:
        path = os.path.join(out, name.replace(" ", "_") + ".onnx")
        x = torch.randn(1, w, f)
        try:
            try:
                torch.onnx.export(mod, (x,), path, input_names=["x"], output_names=["y"],
                                  dynamic_axes={"x": {0: "b"}}, opset_version=17, dynamo=False)
            except TypeError:
                torch.onnx.export(mod, (x,), path, input_names=["x"], output_names=["y"],
                                  dynamic_axes={"x": {0: "b"}}, opset_version=17)
            sess = ort.InferenceSession(path, so, providers=["CPUExecutionProvider"])
            xn = x.numpy()
            for _ in range(50):
                sess.run(None, {"x": xn})
            ts = []
            for _ in range(1000):
                t0 = time.perf_counter()
                sess.run(None, {"x": xn})
                ts.append((time.perf_counter() - t0) * 1000)
            with torch.no_grad():
                ref = mod(x).numpy()
            diff = float(np.abs(sess.run(None, {"x": xn})[0] - ref).max())
            rows.append(dict(model=name, onnx_latency_ms_median=float(np.median(ts)),
                             onnx_latency_ms_p95=float(np.percentile(ts, 95)),
                             onnx_size_kb=os.path.getsize(path) / 1024, max_abs_diff_vs_torch=diff))
            log(f"[onnx] {name:26s} median {np.median(ts):.4f} ms p95 {np.percentile(ts, 95):.4f} ms "
                f"size {os.path.getsize(path) / 1024:.0f} KB diff {diff:.2e}")
        except Exception as e:
            rows.append(dict(model=name, error=str(e)[:300]))
            log(f"[onnx] {name} failed: {e}")
    jdump(rows, os.path.join(out, "onnx.json"))


def _torch_scale(sc, X):
    """Differentiable version of data.Scaler.transform (X: torch tensor of raw features)."""
    import torch
    Z = X
    if sc.log.any():
        lg = torch.as_tensor(sc.log, device=X.device)
        Z = torch.where(lg, torch.sign(X) * torch.log1p(torch.abs(X)), X)
    mu = torch.as_tensor(sc.mu, dtype=X.dtype, device=X.device)
    sd = torch.as_tensor(sc.sd, dtype=X.dtype, device=X.device)
    return torch.clamp((Z - mu) / sd, -10, 10)


def exp_H(args, data):
    """Extra checks promised in Phase 2: time jitter (Exp E), adversarial sanity check (G6), Stage-2 permutation
    importance (Step 11), peak inference memory (Step 8) and open-set classification rate (OSCR, Step 8)."""
    import torch
    out = os.path.join(args.out, "expH")
    os.makedirs(out, exist_ok=True)
    D = data.windows()
    names = ["HP-MDS", "RandomForest", "XGBoost", "Bi-LSTM", "LSTM-AE", "Physics-only"]
    ms = fit_seed0(D, names, args)
    rng = np.random.default_rng(0)
    res = {}
    # ---- 1) time jitter on the test messages
    msgs = data.msgs()
    test_msgs = msgs[msgs["split"] == "test"]
    rows = []
    for sig in (0.0, 0.02, 0.05, 0.1, 0.2):
        nm = test_msgs.copy()
        if sig > 0:
            nm["t"] = nm["t"] + np.random.default_rng(2).normal(0, sig, len(nm))
        f = Dm.add_message_features(nm)
        X, y, g, _ = Dm.make_windows(f, w=args.w, stride=args.test_stride)
        yb = (y > 0).astype(int)
        for n, meth in ms.items():
            r = E.binary_metrics(yb, meth.predict(X)["attack"])
            rows.append(dict(time_sigma_s=sig, model=n, f1=r["f1"], fpr=r["fpr"], recall=r["recall"]))
            log(f"[H] jitter {sig:.2f}s {n:14s} F1 {r['f1']:.4f} FPR {r['fpr']:.4f}")
    res["time_jitter"] = rows
    jdump(res, os.path.join(out, "extra.json"))
    # ---- 2) adversarial sanity check (feature-space, white-box PGD; trees get transferred examples)
    att = np.where(D["yte"] > 0)[0]
    att = att[rng.choice(len(att), min(20000, len(att)), replace=False)]
    Xa = D["Xte"][att]
    sig_raw = D["Xtr"].reshape(-1, D["Xtr"].shape[-1]).std(0) + 1e-6
    hp, bl = ms["HP-MDS"], ms["Bi-LSTM"]
    dev = Mo.DEVICE
    phys_idx = [Dm.FV_C.index(p) for p in Dm.PHYS]
    med = torch.tensor(hp.phys.med, dtype=torch.float32, device=dev)
    scl = torch.tensor(hp.phys.scale, dtype=torch.float32, device=dev)
    sae_b = hp.val_scores["sae"][hp.val_scores["y"] == 0]
    sph_b = hp.val_scores["sph"][hp.val_scores["y"] == 0]
    muA, sdA = float(np.median(sae_b)), float(np.std(sae_b) + 1e-9)
    muP, sdP = float(np.median(sph_b)), float(np.std(sph_b) + 1e-9)
    benign_idx_hp = int(np.where(hp.classes == 0)[0][0])
    benign_idx_bl = int(np.where(bl.classes == 0)[0][0])
    fi1 = torch.tensor(hp.fi, device=dev)
    fib = torch.tensor(bl.fi, device=dev)

    def hp_obj(Xr):
        a = _torch_scale(hp.sc1, Xr[:, :, fi1])
        sae = ((hp.ae(a) - a) ** 2).mean(dim=(1, 2))
        Zp = torch.clamp((Xr[:, :, phys_idx] - med) / scl, 0, 20).amax(1).mean(1)
        fused = hp.alpha * (sae - muA) / sdA + (1 - hp.alpha) * (Zp - muP) / sdP
        logits = hp.clf(_torch_scale(hp.sc2, Xr))
        ce = torch.nn.functional.cross_entropy(logits, torch.full((len(Xr),), benign_idx_hp, device=dev))
        return fused.sum() + ce * len(Xr)

    def bl_obj(Xr):
        logits = bl.model(_torch_scale(bl.sc, Xr[:, :, fib]))
        return torch.nn.functional.cross_entropy(logits, torch.full((len(Xr),), benign_idx_bl, device=dev)) * len(Xr)

    def pgd(obj, X, eps, steps=10):
        sig = torch.tensor(sig_raw, dtype=torch.float32, device=dev)
        x0 = torch.from_numpy(X).to(dev)
        xa = x0.clone()
        for _ in range(steps):
            xa.requires_grad_(True)
            for mod in (hp.ae, hp.clf, bl.model):
                mod.train(False)
            with torch.backends.cudnn.flags(enabled=False):
                loss = obj(xa)
            g, = torch.autograd.grad(loss, xa)
            xa = xa.detach() - (eps / 4) * sig * torch.sign(g)
            xa = torch.max(torch.min(xa, x0 + eps * sig), x0 - eps * sig)
        return xa.detach().cpu().numpy()

    rows = []
    for eps in (0.0, 0.01, 0.05, 0.1, 0.25, 0.5):
        if eps == 0:
            sets = {"clean": Xa}
        else:
            sets = {"random noise": (Xa + eps * sig_raw * rng.choice([-1, 1], size=Xa.shape)).astype(np.float32)}
            adv_hp, adv_bl = [], []
            for i in range(0, len(Xa), 2048):
                adv_hp.append(pgd(hp_obj, Xa[i:i + 2048], eps))
                adv_bl.append(pgd(bl_obj, Xa[i:i + 2048], eps))
            sets["PGD vs HP-MDS"] = np.concatenate(adv_hp)
            sets["PGD vs Bi-LSTM"] = np.concatenate(adv_bl)
        for sname, Xs in sets.items():
            for n, meth in ms.items():
                det = float(meth.predict(Xs)["attack"].mean())
                kind = ("white-box" if (sname == "PGD vs HP-MDS" and n == "HP-MDS") or
                        (sname == "PGD vs Bi-LSTM" and n == "Bi-LSTM") else
                        ("transfer" if sname.startswith("PGD") else sname))
                rows.append(dict(eps=eps, perturbation=sname, model=n, kind=kind, attack_detection_rate=det))
            log(f"[H] eps {eps:<5} {sname:15s} " + " ".join(
                f"{r['model']}={r['attack_detection_rate']:.3f}" for r in rows[-len(ms):]))
    res["adversarial"] = rows
    jdump(res, os.path.join(out, "extra.json"))
    # ---- 3) permutation importance (Stage 2 macro-F1 and Stage 1 binary F1)
    sub = rng.choice(len(D["yte"]), min(60000, len(D["yte"])), replace=False)
    Xs, ys = D["Xte"][sub], D["yte"][sub]
    labels = sorted(int(c) for c in np.unique(D["ytr"]))

    def s2_f1(X):
        P = Mo.predict_logits(hp.clf, hp.sc2.transform(X))
        return float(E.f1_score(ys, hp.classes[P.argmax(1)], labels=labels, average="macro", zero_division=0))

    def s1_f1(X):
        return float(E.f1_score((ys > 0).astype(int), hp.predict(X)["flag"]))
    b2, b1 = s2_f1(Xs), s1_f1(Xs)
    imp = {}
    for j, fn in enumerate(Dm.FV_C):
        Xp = Xs.copy()
        Xp[:, :, j] = Xp[rng.permutation(len(Xp)), :, j]
        imp[fn] = dict(stage2_macro_f1_drop=b2 - s2_f1(Xp), stage1_binary_f1_drop=b1 - s1_f1(Xp))
    res["permutation_importance"] = dict(base_stage2_macro_f1=b2, base_stage1_f1=b1,
                                         features=dict(sorted(imp.items(), key=lambda kv: -kv[1]["stage2_macro_f1_drop"])))
    log("[H] permutation importance done")
    jdump(res, os.path.join(out, "extra.json"))
    # ---- 4) peak inference memory (CPU, 1 thread, batch of 1,024 windows)
    try:
        import psutil
        import threading
        proc = psutil.Process()
        torch.set_num_threads(1)
        import tracemalloc
        Xb = D["Xte"][:8192]
        mem = []
        for n, meth0 in ms.items():
            meth = copy.deepcopy(meth0)
            for attr in ("ae", "clf", "model"):
                o = getattr(meth, attr, None)
                if isinstance(o, torch.nn.Module):
                    o.cpu()
            if n == "XGBoost":
                try:
                    meth.model.set_params(device="cpu", n_jobs=1)
                except Exception:
                    pass
            elif hasattr(getattr(meth, "model", None), "n_jobs"):
                meth.model.set_params(n_jobs=1)
            meth.predict(Xb[:8])
            import gc
            gc.collect()
            base = proc.memory_info().rss
            peak = [base]
            stop = [False]

            def mon():
                while not stop[0]:
                    peak[0] = max(peak[0], proc.memory_info().rss)
                    time.sleep(0.0005)
            th = threading.Thread(target=mon)
            th.start()
            tracemalloc.start()
            for _ in range(3):
                meth.predict(Xb)
            _, tm_peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            stop[0] = True
            th.join()
            extra = max(peak[0] - base, tm_peak) / 1e6
            mem.append(dict(model=n, peak_extra_mb_batch8192=extra, per_window_kb=extra * 1000 / len(Xb)))
            log(f"[H] memory {n:14s} +{extra:.1f} MB for 8,192 windows")
            del meth
        res["peak_memory"] = mem
        torch.set_num_threads(os.cpu_count() or 4)
    except ImportError:
        res["peak_memory"] = "psutil not installed"
    jdump(res, os.path.join(out, "extra.json"))
    # ---- 5) OSCR on the open-set hold-outs (seed 0)
    present = set(int(c) for c in np.unique(D["ytr"]))
    holdouts = [[c for c in h if c in present] for h in ([2, 6, 11, 16], [3, 8, 12, 15], [1, 9, 14, 17])]
    oscr = []
    for hi, H in enumerate(holdouts):
        ktr, kva = ~np.isin(D["ytr"], H), ~np.isin(D["yva"], H)
        DK = dict(Xtr=D["Xtr"][ktr], ytr=D["ytr"][ktr], gtr=D["gtr"][ktr], Xva=D["Xva"][kva], yva=D["yva"][kva],
                  gva=D["gva"][kva])
        yte = D["yte"]
        known_att, unseen = (yte > 0) & ~np.isin(yte, H), np.isin(yte, H)
        for n in ("HP-MDS", "RandomForest", "XGBoost", "Bi-LSTM"):
            meth = Me.make(n, args.epochs, log).fit(DK, seed=0)
            pr = meth.predict(D["Xte"])
            if n == "HP-MDS":
                P = Mo.softmax(Mo.predict_logits(meth.clf, meth.sc2.transform(D["Xte"])))
                lab, conf = meth.classes[P.argmax(1)], P.max(1)
            else:
                lab, conf = pr["label"], pr["maxp"]
            # OSCR: correct-classification rate on known attacks vs false-positive rate on unseen attacks
            thr = np.unique(np.r_[conf[known_att], conf[unseen]])[::-1]
            thr = thr[np.linspace(0, len(thr) - 1, min(400, len(thr))).astype(int)]
            ccr = [float(((conf[known_att] >= t) & (lab[known_att] == yte[known_att])).mean()) for t in thr]
            fpr = [float((conf[unseen] >= t).mean()) for t in thr]
            _trap = getattr(np, 'trapezoid', None) or getattr(np, 'trapz')
            area = float(_trap(np.r_[0, ccr, ccr[-1]], np.r_[0, fpr, 1.0]))
            oscr.append(dict(holdout=hi, model=n, oscr=area))
            log(f"[H] OSCR holdout {hi} {n:14s} {area:.4f}")
    res["oscr"] = oscr
    jdump(res, os.path.join(out, "extra.json"))
    log("[H] all extra checks done")


def exp_C(args, data):
    """Cross-dataset: train on VeReMi Extension (shared fields) -> test on original VeReMi."""
    from hpmds.veremi_original import load_original_veremi, to_shared_fields
    out = os.path.join(args.out, "expC")
    os.makedirs(out, exist_ok=True)
    if not args.veremi_dir:
        raise SystemExit("--veremi-dir is required for Exp C")
    fe = Dm.add_message_features(to_shared_fields(data.msgs()))
    DS = data.windows(feat=fe, key=f"{data.tag}_shared")
    p = Dm.cache_path(args.cache, f"veremi_orig_{args.veremi_max_scen}.pkl")
    if os.path.exists(p):
        import pandas as pd
        om = pd.read_pickle(p)
    else:
        om = load_original_veremi(args.veremi_dir, max_scenarios=args.veremi_max_scen)
        om.to_pickle(p)
    of = Dm.add_message_features(om)
    Xo, yo, go, _ = Dm.make_windows(of, w=args.w, stride=args.test_stride)
    log(f"[C] original VeReMi windows: {len(yo):,}; classes {dict(zip(*np.unique(yo, return_counts=True)))}")
    names = ["HP-MDS", "RandomForest", "XGBoost", "Bi-LSTM", "LSTM-AE", "IsolationForest", "Physics-only"]
    mpath = os.path.join(out, "metrics.jsonl")
    done = {(r["model"], r["seed"]) for r in jread(mpath)}
    for seed in range(args.c_seeds):
        for n in names:
            if (n, seed) in done:
                continue
            log(f"[C] {n} seed {seed}")
            meth = Me.make(n, args.epochs, log).fit(DS, seed=seed)
            pin = meth.predict(DS["Xte"])
            pout = meth.predict(Xo)
            b_in = E.binary_metrics((DS["yte"] > 0).astype(int), pin["attack"], pin["score"])
            b_out = E.binary_metrics((yo > 0).astype(int), pout["attack"], pout["score"])
            row = dict(model=n, seed=seed, in_dist=b_in, cross=b_out,
                       cross_per_class_detection={Dm.CLASS_NAMES[int(k)]: v for k, v in
                                                  E.per_class_detection(yo, pout["attack"]).items()})
            jappend(row, mpath)
            log(f"    in-dist F1 {b_in['f1']:.4f} -> cross F1 {b_out['f1']:.4f} (FPR {b_out['fpr']:.4f})")


def main():
    global LOGF
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv")
    ap.add_argument("--veremi-dir")
    ap.add_argument("--veremi-max-scen", type=int, default=60)
    ap.add_argument("--exp", nargs="+", required=True,
                    choices=["prepare", "A", "B", "D", "E", "F", "G", "H", "onnx", "stats", "ablation", "C", "figures", "report"])
    ap.add_argument("--quick", action="store_true")
    ap.add_argument("--quick-frac", type=float, default=0.05)
    ap.add_argument("--stream-by", default="sender", choices=["sender", "pseudo"])
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--open-seeds", type=int, default=3)
    ap.add_argument("--abl-seeds", type=int, default=3)
    ap.add_argument("--c-seeds", type=int, default=2)
    ap.add_argument("--b-seeds", type=int, default=3)
    ap.add_argument("--epochs", type=int, default=20)
    ap.add_argument("--w", type=int, default=10)
    ap.add_argument("--train-stride", type=int, default=5)
    ap.add_argument("--test-stride", type=int, default=2)
    ap.add_argument("--no-secondary", action="store_true")
    ap.add_argument("--out", default=None)
    ap.add_argument("--cache", default="cache")
    args = ap.parse_args()
    if args.out is None:
        args.out = "results_quick" if args.quick else "results"
    if args.quick:
        args.epochs = min(args.epochs, 4)
        args.seeds = min(args.seeds, 2)
        args.open_seeds = min(args.open_seeds, 1)
        args.abl_seeds = min(args.abl_seeds, 1)
        args.c_seeds = min(args.c_seeds, 1)
        args.b_seeds = min(args.b_seeds, 1)
    os.makedirs(args.out, exist_ok=True)
    LOGF = os.path.join(args.out, f"log_{'_'.join(args.exp)}.txt")
    log(f"device: {Mo.DEVICE}; args: {vars(args)}")
    data = Data(args)
    for e in args.exp:
        t0 = time.time()
        if e == "prepare":
            describe(data.windows(), args.out)
        elif e == "A":
            exp_A(args, data)
        elif e == "D":
            exp_D(args, data)
        elif e == "E":
            exp_E(args, data)
        elif e == "F":
            exp_F(args, data)
        elif e == "stats":
            exp_stats(args, data)
        elif e == "ablation":
            exp_ablation(args, data)
        elif e == "B":
            exp_B(args, data)
        elif e == "G":
            exp_G(args, data)
        elif e == "H":
            exp_H(args, data)
        elif e == "onnx":
            exp_onnx(args, data)
        elif e == "C":
            exp_C(args, data)
        elif e == "figures":
            from hpmds import plots
            plots.make_all(args.out)
        elif e == "report":
            from hpmds import report
            report.write(args.out)
        log(f"=== {e} finished in {(time.time() - t0) / 60:.1f} min")


if __name__ == "__main__":
    main()
