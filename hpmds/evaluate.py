"""Metrics, threshold selection and statistical tests."""
import numpy as np
from scipy import stats
from sklearn.metrics import (accuracy_score, average_precision_score, balanced_accuracy_score,
                             f1_score, matthews_corrcoef, precision_score, recall_score, roc_auc_score)


def binary_metrics(y_true_attack, y_pred_attack, score=None):
    yt = np.asarray(y_true_attack).astype(int)
    yp = np.asarray(y_pred_attack).astype(int)
    tn = int(((yt == 0) & (yp == 0)).sum())
    fp = int(((yt == 0) & (yp == 1)).sum())
    fn = int(((yt == 1) & (yp == 0)).sum())
    tp = int(((yt == 1) & (yp == 1)).sum())
    m = dict(accuracy=accuracy_score(yt, yp), precision=precision_score(yt, yp, zero_division=0),
             recall=recall_score(yt, yp, zero_division=0), f1=f1_score(yt, yp, zero_division=0),
             mcc=matthews_corrcoef(yt, yp) if len(np.unique(yp)) > 1 or len(np.unique(yt)) > 1 else 0.0,
             balanced_acc=balanced_accuracy_score(yt, yp),
             fpr=fp / max(1, fp + tn), fnr=fn / max(1, fn + tp), tp=tp, fp=fp, tn=tn, fn=fn)
    if score is not None and len(np.unique(yt)) > 1:
        s = np.nan_to_num(np.asarray(score, dtype=float), nan=0.0, posinf=1e12, neginf=-1e12)
        m["roc_auc"] = roc_auc_score(yt, s)
        m["pr_auc"] = average_precision_score(yt, s)
    return m


def multiclass_metrics(y_true, y_pred, labels):
    return dict(accuracy=accuracy_score(y_true, y_pred),
                macro_f1=f1_score(y_true, y_pred, labels=labels, average="macro", zero_division=0),
                weighted_f1=f1_score(y_true, y_pred, labels=labels, average="weighted", zero_division=0),
                balanced_acc=balanced_accuracy_score(y_true, y_pred) if set(np.unique(y_pred)) <= set(labels) else
                float(np.mean([np.mean(y_pred[y_true == c] == c) for c in labels if (y_true == c).any()])),
                mcc=matthews_corrcoef(y_true, y_pred))


def per_class_f1(y_true, y_pred, labels):
    f = f1_score(y_true, y_pred, labels=labels, average=None, zero_division=0)
    return dict(zip([int(c) for c in labels], [float(v) for v in f]))


def per_class_detection(y_true, y_pred_attack):
    """Recall of 'attack' alarms for every attack class (binary detection rate per class)."""
    out = {}
    for c in np.unique(y_true):
        if c == 0:
            continue
        out[int(c)] = float(np.mean(y_pred_attack[y_true == c]))
    return out


def best_threshold(score_val, y_val_attack, benign_pcts=(90, 95, 97, 98, 99, 99.5, 99.9)):
    """Threshold = a percentile of benign validation scores; pick the percentile with best val F1."""
    sb = score_val[y_val_attack == 0]
    best = (-1, None, None)
    for p in benign_pcts:
        thr = np.percentile(sb, p)
        f = f1_score(y_val_attack, score_val > thr, zero_division=0)
        if f > best[0]:
            best = (f, thr, p)
    return best[1], best[2], best[0]


def zfit(score_benign):
    mu, sd = np.median(score_benign), np.std(score_benign) + 1e-9
    return lambda s: (s - mu) / sd


def grouped_bootstrap(metric_fn, groups, n=1000, seed=0):
    """95% CI by resampling *vehicles* (groups) with replacement."""
    rng = np.random.default_rng(seed)
    ug, inv = np.unique(groups, return_inverse=True)
    members = [np.where(inv == i)[0] for i in range(len(ug))]
    vals = []
    for _ in range(n):
        pick = rng.integers(0, len(ug), len(ug))
        idx = np.concatenate([members[i] for i in pick])
        vals.append(metric_fn(idx))
    return float(np.percentile(vals, 2.5)), float(np.percentile(vals, 97.5))


def mcnemar(correct_a, correct_b):
    b = int((correct_a & ~correct_b).sum())
    c = int((~correct_a & correct_b).sum())
    if b + c == 0:
        return 0.0, 1.0
    chi2 = (abs(b - c) - 1) ** 2 / (b + c)
    return float(chi2), float(stats.chi2.sf(chi2, 1))


def wilcoxon(a, b):
    a, b = np.asarray(a), np.asarray(b)
    if np.allclose(a, b):
        return 0.0, 1.0
    r = stats.wilcoxon(a, b, zero_method="wilcox")
    return float(r.statistic), float(r.pvalue)


def holm(pvals):
    p = np.asarray(pvals, dtype=float)
    order = np.argsort(p)
    adj = np.empty_like(p)
    running = 0.0
    m = len(p)
    for k, i in enumerate(order):
        running = max(running, (m - k) * p[i])
        adj[i] = min(1.0, running)
    return adj
