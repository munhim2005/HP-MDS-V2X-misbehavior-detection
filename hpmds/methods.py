"""Uniform wrappers: every method has fit(D, seed) and predict(X_raw_windows) -> dict with
'score' (higher = more anomalous), 'attack' (0/1) and, for classifiers, 'label' (class id, -1 = unknown)."""
import pickle
import time

import numpy as np
from sklearn.ensemble import (BaggingClassifier, ExtraTreesClassifier, IsolationForest,
                              RandomForestClassifier, StackingClassifier)
from sklearn.linear_model import LogisticRegression
from sklearn.neighbors import KNeighborsClassifier

from . import models as M
from .data import FV_C, PhysicsScore, Scaler, feature_index, summary_features
from .evaluate import best_threshold, zfit

try:
    from xgboost import XGBClassifier
except Exception:  # pragma: no cover
    XGBClassifier = None


def _enc(classes, y):
    return np.searchsorted(classes, y)


def _class_weights(y):
    c = np.bincount(y)
    w = 1.0 / np.sqrt(np.maximum(c, 1))
    return (w / w[y].mean())[y]


def _subsample(n, k, seed, y=None):
    rng = np.random.default_rng(seed)
    if n <= k:
        return np.arange(n)
    if y is None:
        return np.sort(rng.choice(n, k, replace=False))
    # stratified
    out = []
    for c in np.unique(y):
        idx = np.where(y == c)[0]
        take = max(1, int(round(k * len(idx) / n)))
        out.append(rng.choice(idx, min(take, len(idx)), replace=False))
    return np.sort(np.concatenate(out))


class Method:
    name = "method"
    multiclass = False

    def size_bytes(self):
        try:
            return len(pickle.dumps(self._core()))
        except Exception:
            return float("nan")

    def n_params(self):
        return float("nan")

    def _core(self):
        return None


# ----------------------------------------------------------------------------------------- HP-MDS
class HPMDS(Method):
    name = "HP-MDS"
    multiclass = True

    def __init__(self, fs_ae="C", latent=16, alpha=None, use_stage2=True, open_set=True,
                 imbalance="weights", epochs=20, log=print):
        self.fs_ae, self.latent, self.alpha_fixed = fs_ae, latent, alpha
        self.use_stage2, self.open_set, self.imbalance = use_stage2, open_set, imbalance
        self.epochs, self.log = epochs, log

    def fit(self, D, seed=0):
        t0 = time.time()
        Xtr, ytr, Xva, yva = D["Xtr"], D["ytr"], D["Xva"], D["yva"]
        w = Xtr.shape[1]
        self.fi = feature_index(self.fs_ae)
        self.sc1 = Scaler([FV_C[i] for i in self.fi]).fit(Xtr[ytr == 0][:, :, self.fi])
        A_tr = self.sc1.transform(Xtr[ytr == 0][:, :, self.fi])
        A_va_all = self.sc1.transform(Xva[:, :, self.fi])
        self.ae = M.ConvGRUAE(len(self.fi), w, latent=self.latent)
        self.ae, self.hist_ae, _ = M.train_ae(self.ae, A_tr, A_va_all[yva == 0], epochs=self.epochs, seed=seed)
        self.phys = PhysicsScore().fit(Xtr[ytr == 0])
        sae, sph = M.predict_score(self.ae, A_va_all), self.phys.score(Xva)
        self.zA, self.zP = zfit(sae[yva == 0]), zfit(sph[yva == 0])
        yv = (yva > 0).astype(int)
        self.alpha_curve = []
        best = (-1, None, None, None)
        alphas = [self.alpha_fixed] if self.alpha_fixed is not None else np.round(np.arange(0, 1.01, 0.1), 2)
        for a in alphas:
            s = a * self.zA(sae) + (1 - a) * self.zP(sph)
            thr, pct, f = best_threshold(s, yv)
            self.alpha_curve.append((float(a), float(f)))
            if f > best[0]:
                best = (f, a, thr, pct)
        _, self.alpha, self.thr, self.pct = best
        self.val_scores = dict(sae=sae, sph=sph, y=yva)
        # Stage 2 (all FV-C features, benign + known attacks)
        if self.use_stage2:
            self.classes = np.unique(ytr)
            self.sc2 = Scaler(FV_C).fit(Xtr)
            B_tr, B_va = self.sc2.transform(Xtr), self.sc2.transform(Xva)
            yt, yv2 = _enc(self.classes, ytr), _enc(self.classes, np.where(np.isin(yva, self.classes), yva, 0))
            B_tr, yt = _rebalance(B_tr, yt, self.imbalance, seed)
            self.clf = M.ConvGRUClassifier(len(FV_C), len(self.classes))
            self.clf, self.hist_clf, _ = M.train_clf(self.clf, B_tr, yt, B_va, yv2, len(self.classes),
                                                     epochs=self.epochs, seed=seed,
                                                     class_weights=self.imbalance == "weights")
            # open-set threshold: keep 95% of flagged known-attack validation windows
            p = M.softmax(M.predict_logits(self.clf, B_va))
            flag = self._fused(sae, sph) > self.thr
            sel = flag & (yva > 0)
            self.tau = float(np.percentile(p.max(1)[sel], 5)) if sel.sum() > 10 else 0.0
        self.fit_time = time.time() - t0
        return self

    def _fused(self, sae, sph):
        return self.alpha * self.zA(sae) + (1 - self.alpha) * self.zP(sph)

    def predict(self, X):
        sae = M.predict_score(self.ae, self.sc1.transform(X[:, :, self.fi]))
        sph = self.phys.score(X)
        fused = self._fused(sae, sph)
        flag = fused > self.thr
        out = dict(score=fused, s_ae=sae, s_phys=sph, flag=flag.astype(int))
        if not self.use_stage2:
            out["attack"] = flag.astype(int)
            out["label"] = np.where(flag, -1, 0)
            return out
        logits = M.predict_logits(self.clf, self.sc2.transform(X))
        p = M.softmax(logits)
        maxp, arg = p.max(1), self.classes[p.argmax(1)]
        tau = self.tau if self.open_set else 0.0
        label = np.where(~flag, 0, np.where(maxp < tau, -1, arg))
        out.update(label=label, attack=(label != 0).astype(int), maxp=maxp, energy=M.energy(logits),
                   p_benign=p[:, 0] if self.classes[0] == 0 else np.zeros(len(p)))
        return out

    def n_params(self):
        return M.n_params(self.ae) + (M.n_params(self.clf) if self.use_stage2 else 0)

    def size_bytes(self):
        import io
        import torch
        b = io.BytesIO()
        torch.save({"ae": self.ae.state_dict(), "clf": self.clf.state_dict() if self.use_stage2 else None}, b)
        return b.tell()


def _rebalance(X, y, mode, seed):
    if mode in ("weights", "none"):
        return X, y
    rng = np.random.default_rng(seed)
    counts = np.bincount(y)
    target = int(np.median(counts[counts > 0]))
    if mode == "smote":
        try:
            from imblearn.over_sampling import SMOTE
            strat = {int(c): target for c in np.where((counts > 5) & (counts < target))[0]}
            if strat:
                sh = X.shape
                Xr, yr = SMOTE(sampling_strategy=strat, k_neighbors=5, random_state=seed).fit_resample(
                    X.reshape(len(X), -1), y)
                return Xr.reshape(-1, sh[1], sh[2]).astype(np.float32), yr
            return X, y
        except ImportError:
            mode = "oversample"
    if mode == "oversample":
        extra = [rng.choice(np.where(y == c)[0], target - counts[c]) for c in np.where((counts > 0) & (counts < target))[0]]
        if extra:
            e = np.concatenate(extra)
            return np.concatenate([X, X[e]]), np.concatenate([y, y[e]])
    return X, y


# ------------------------------------------------------------------------------------- baselines
class _TreeBase(Method):
    multiclass = True
    fs = "B"

    def _X(self, X):
        return summary_features(X[:, :, feature_index(self.fs)])

    def _post(self, p):
        label = self.classes[p.argmax(1)]
        pb = p[:, 0] if self.classes[0] == 0 else np.zeros(len(p))
        return dict(score=1 - pb, attack=(label != 0).astype(int), label=label, maxp=p.max(1), p_benign=pb)

    def predict(self, X):
        return self._post(self.model.predict_proba(self._X(X)))

    def _core(self):
        return self.model


class RF(_TreeBase):
    name = "RandomForest"

    def fit(self, D, seed=0):
        t0 = time.time()
        self.classes = np.unique(D["ytr"])
        self.model = RandomForestClassifier(n_estimators=100, max_depth=25, min_samples_leaf=2, n_jobs=-1,
                                            class_weight="balanced_subsample", random_state=seed)
        self.model.fit(self._X(D["Xtr"]), _enc(self.classes, D["ytr"]))
        self.fit_time = time.time() - t0
        return self


class XGB(_TreeBase):
    name = "XGBoost"

    def __init__(self, fs="B"):
        self.fs = fs
        if fs != "B":
            self.name = f"XGBoost(FV-{fs})"

    def fit(self, D, seed=0):
        t0 = time.time()
        self.classes = np.unique(D["ytr"])
        Xtr, ytr = self._X(D["Xtr"]), _enc(self.classes, D["ytr"])
        va_ok = np.isin(D["yva"], self.classes)
        Xva, yva = self._X(D["Xva"][va_ok]), _enc(self.classes, D["yva"][va_ok])
        kw = dict(n_estimators=400, max_depth=8, learning_rate=0.1, subsample=0.8, colsample_bytree=0.8,
                  tree_method="hist", random_state=seed, early_stopping_rounds=20, eval_metric="mlogloss")
        try:
            import torch
            if torch.cuda.is_available():
                kw["device"] = "cuda"
        except Exception:
            pass
        if len(self.classes) == 2:
            kw["eval_metric"] = "logloss"
        self.model = XGBClassifier(**kw)
        self.model.fit(Xtr, ytr, sample_weight=_class_weights(ytr), eval_set=[(Xva, yva)], verbose=False)
        self.fit_time = time.time() - t0
        return self

    def predict(self, X):
        p = self.model.predict_proba(self._X(X))
        if p.ndim == 1:
            p = np.stack([1 - p, p], 1)
        return self._post(p)


class KNNBag(_TreeBase):
    name = "KNN-Bagging*"
    max_train = 40000

    def fit(self, D, seed=0):
        t0 = time.time()
        self.classes = np.unique(D["ytr"])
        idx = _subsample(len(D["ytr"]), self.max_train, seed, D["ytr"])
        self.mu = None
        X = self._X(D["Xtr"][idx])
        self.mu, self.sd = X.mean(0), X.std(0) + 1e-6
        self.model = BaggingClassifier(KNeighborsClassifier(n_neighbors=3, weights="distance"),
                                       n_estimators=10, max_samples=0.5, n_jobs=-1, random_state=seed)
        self.model.fit((X - self.mu) / self.sd, _enc(self.classes, D["ytr"][idx]))
        self.fit_time = time.time() - t0
        return self

    def _X(self, X):
        Z = summary_features(X[:, :, feature_index(self.fs)])
        return Z if self.mu is None else (Z - self.mu) / self.sd


class Stacked(_TreeBase):
    name = "Stacked*"
    max_train = 60000

    def fit(self, D, seed=0):
        t0 = time.time()
        self.classes = np.unique(D["ytr"])
        idx = _subsample(len(D["ytr"]), self.max_train, seed, D["ytr"])
        self.mu = None
        X = self._X(D["Xtr"][idx])
        self.mu, self.sd = X.mean(0), X.std(0) + 1e-6
        est = [("knn", KNeighborsClassifier(n_neighbors=5)),
               ("et", ExtraTreesClassifier(n_estimators=100, n_jobs=-1, random_state=seed)),
               ("rf", RandomForestClassifier(n_estimators=100, n_jobs=-1, random_state=seed))]
        if XGBClassifier is not None:
            est.append(("xgb", XGBClassifier(n_estimators=200, max_depth=6, tree_method="hist", random_state=seed)))
        self.model = StackingClassifier(est, final_estimator=LogisticRegression(max_iter=1000), cv=3, n_jobs=1)
        self.model.fit((X - self.mu) / self.sd, _enc(self.classes, D["ytr"][idx]))
        self.fit_time = time.time() - t0
        return self

    def _X(self, X):
        Z = summary_features(X[:, :, feature_index(self.fs)])
        return Z if self.mu is None else (Z - self.mu) / self.sd


class BiLSTM(Method):
    name = "Bi-LSTM"
    multiclass = True

    def __init__(self, epochs=20):
        self.epochs = epochs

    def fit(self, D, seed=0):
        t0 = time.time()
        self.fi = feature_index("B")
        self.classes = np.unique(D["ytr"])
        self.sc = Scaler([FV_C[i] for i in self.fi]).fit(D["Xtr"][:, :, self.fi])
        Xtr, Xva = self.sc.transform(D["Xtr"][:, :, self.fi]), self.sc.transform(D["Xva"][:, :, self.fi])
        yv = np.where(np.isin(D["yva"], self.classes), D["yva"], 0)
        self.model = M.BiLSTMClassifier(len(self.fi), len(self.classes))
        self.model, self.hist, _ = M.train_clf(self.model, Xtr, _enc(self.classes, D["ytr"]), Xva,
                                               _enc(self.classes, yv), len(self.classes), epochs=self.epochs, seed=seed)
        self.fit_time = time.time() - t0
        return self

    def predict(self, X):
        logits = M.predict_logits(self.model, self.sc.transform(X[:, :, self.fi]))
        p = M.softmax(logits)
        label = self.classes[p.argmax(1)]
        pb = p[:, 0] if self.classes[0] == 0 else np.zeros(len(p))
        return dict(score=1 - pb, attack=(label != 0).astype(int), label=label, maxp=p.max(1),
                    energy=M.energy(logits), p_benign=pb)

    def n_params(self):
        return M.n_params(self.model)

    def _core(self):
        return self.model.state_dict()


class LSTMAEBaseline(Method):
    name = "LSTM-AE"

    def __init__(self, epochs=20):
        self.epochs = epochs

    def fit(self, D, seed=0):
        t0 = time.time()
        self.fi = feature_index("A")
        ben = D["ytr"] == 0
        self.sc = Scaler([FV_C[i] for i in self.fi]).fit(D["Xtr"][ben][:, :, self.fi])
        Xtr = self.sc.transform(D["Xtr"][ben][:, :, self.fi])
        Xva = self.sc.transform(D["Xva"][:, :, self.fi])
        self.model = M.LSTMAE(len(self.fi), Xtr.shape[1])
        self.model, self.hist, _ = M.train_ae(self.model, Xtr, Xva[D["yva"] == 0], epochs=self.epochs, seed=seed)
        s = M.predict_score(self.model, Xva)
        self.thr, self.pct, _ = best_threshold(s, (D["yva"] > 0).astype(int))
        self.fit_time = time.time() - t0
        return self

    def predict(self, X):
        s = M.predict_score(self.model, self.sc.transform(X[:, :, self.fi]))
        return dict(score=s, attack=(s > self.thr).astype(int))

    def n_params(self):
        return M.n_params(self.model)

    def _core(self):
        return self.model.state_dict()


class IForest(Method):
    name = "IsolationForest"

    def fit(self, D, seed=0):
        t0 = time.time()
        self.fi = feature_index("B")
        ben = np.where(D["ytr"] == 0)[0]
        ben = ben[_subsample(len(ben), 100000, seed)]
        self.model = IsolationForest(n_estimators=200, n_jobs=-1, random_state=seed)
        self.model.fit(summary_features(D["Xtr"][ben][:, :, self.fi]))
        s = -self.model.score_samples(summary_features(D["Xva"][:, :, self.fi]))
        self.thr, self.pct, _ = best_threshold(s, (D["yva"] > 0).astype(int))
        self.fit_time = time.time() - t0
        return self

    def predict(self, X):
        s = -self.model.score_samples(summary_features(X[:, :, self.fi]))
        return dict(score=s, attack=(s > self.thr).astype(int))

    def _core(self):
        return self.model


class PhysicsOnly(Method):
    """E9-style training-free plausibility detector (no learned component)."""
    name = "Physics-only"

    def fit(self, D, seed=0):
        t0 = time.time()
        self.phys = PhysicsScore().fit(D["Xtr"][D["ytr"] == 0])
        s = self.phys.score(D["Xva"])
        self.thr, self.pct, _ = best_threshold(s, (D["yva"] > 0).astype(int))
        self.fit_time = time.time() - t0
        return self

    def predict(self, X):
        s = self.phys.score(X)
        return dict(score=s, attack=(s > self.thr).astype(int))

    def n_params(self):
        return 0

    def _core(self):
        return self.phys


CORE = ["HP-MDS", "RandomForest", "XGBoost", "Bi-LSTM", "LSTM-AE", "IsolationForest", "Physics-only"]
SECONDARY = ["KNN-Bagging*", "Stacked*"]


def make(name, epochs=20, log=print):
    return {"HP-MDS": lambda: HPMDS(epochs=epochs, log=log), "RandomForest": RF, "XGBoost": XGB,
            "Bi-LSTM": lambda: BiLSTM(epochs), "LSTM-AE": lambda: LSTMAEBaseline(epochs),
            "IsolationForest": IForest, "Physics-only": PhysicsOnly,
            "KNN-Bagging*": KNNBag, "Stacked*": Stacked}[name]()
