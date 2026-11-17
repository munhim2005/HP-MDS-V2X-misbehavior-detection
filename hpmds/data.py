"""Data loading, cleaning, grouped splitting, feature engineering (FV-A/B/C) and windowing
for the VeReMi Extension dataset (single labelled CSV, Mendeley release 10.17632/k62n4z9gdz.1).

The loader auto-detects common column spellings so that slightly different CSV exports work.
"""
import ast
import os
import re

import numpy as np
import pandas as pd

# F2MD / VeReMi Extension numbering of behaviour classes
CLASS_NAMES = {
    0: "Genuine", 1: "ConstPos", 2: "ConstPosOffset", 3: "RandomPos", 4: "RandomPosOffset",
    5: "ConstSpeed", 6: "ConstSpeedOffset", 7: "RandomSpeed", 8: "RandomSpeedOffset",
    9: "EventualStop", 10: "Disruptive", 11: "DataReplay", 12: "DelayedMessages",
    13: "DoS", 14: "DoSRandom", 15: "DoSDisruptive", 16: "GridSybil",
    17: "DataReplaySybil", 18: "DoSRandomSybil", 19: "DoSDisruptiveSybil",
}

FV_A = ["rel_x", "rel_y", "vx", "vy", "v", "ax", "ay", "a", "hx", "hy"]
FV_B_EXTRA = ["dt", "rate", "dpos", "dv", "dhead"]
PHYS = ["r_disp", "r_implied_v", "r_cv_pred", "r_acc", "r_head_vel", "r_head_move",
        "r_vmax", "r_amax", "r_time"]
FV_B = FV_A + FV_B_EXTRA
FV_C = FV_B + PHYS
FEATURE_SETS = {"A": FV_A, "B": FV_B, "C": FV_C}
# heavy-tailed features get a signed log1p before scaling
LOG_FEATS = set(FV_B_EXTRA + PHYS)


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _find(cols, *cands):
    m = {_norm(c): c for c in cols}
    for c in cands:
        if _norm(c) in m:
            return m[_norm(c)]
    return None


def _vec(df, base, cols):
    """Return (x, y) arrays for a vector field stored as base_x/base_y, base0/base1 or '[x, y, z]'."""
    cx = _find(cols, base + "x", base + "_x", base + "0", base + "_0")
    cy = _find(cols, base + "y", base + "_y", base + "1", base + "_1")
    if cx and cy:
        return df[cx].astype("float64").values, df[cy].astype("float64").values
    c = _find(cols, base)
    if c is not None:
        parsed = df[c].astype(str).map(lambda s: ast.literal_eval(s) if s.startswith("[") else [np.nan, np.nan])
        arr = np.array(parsed.tolist(), dtype="float64")
        return arr[:, 0], arr[:, 1]
    return None, None


def load_veremi_csv(path, max_rows=None, verbose=True, stream_by="sender"):
    """Load the labelled VeReMi Extension CSV into a tidy message table.
    stream_by='sender' builds one message stream per vehicle; 'pseudo' one per pseudonym (stricter,
    but Sybil/DoS pseudonyms are often too short to form a window)."""
    df = pd.read_csv(path, nrows=max_rows, low_memory=False)
    cols = list(df.columns)
    if verbose:
        print(f"[data] loaded {len(df):,} rows, columns: {cols}")

    c_type = _find(cols, "type")
    if c_type is not None and df[c_type].nunique() > 1 and (df[c_type] == 3).any():
        df = df[df[c_type] == 3]  # keep received BSMs only (type 2 = own GPS)

    c_time = _find(cols, "sendTime", "send_time", "rcvTime", "rcv_time", "time", "timestamp")
    c_sender = _find(cols, "sender", "senderId", "sender_id", "vehicle")
    c_pseudo = _find(cols, "senderPseudo", "sender_pseudo", "pseudo", "pseudonym")
    c_cls = _find(cols, "class", "attackerType", "attacker_type", "attack_type", "label", "attack")
    c_mid = _find(cols, "messageID", "message_id", "msgid")
    c_rcv = _find(cols, "rcvTime", "rcv_time")
    missing = [n for n, c in [("time", c_time), ("sender", c_sender or c_pseudo), ("class", c_cls)] if c is None]
    if missing:
        raise ValueError(f"Could not find columns {missing} in CSV. Columns are: {cols}")

    x, y = _vec(df, "pos", cols)
    vx, vy = _vec(df, "spd", cols)
    if vx is None:
        vx, vy = _vec(df, "speed", cols)
    ax, ay = _vec(df, "acl", cols)
    if ax is None:
        ax, ay = _vec(df, "acc", cols)
    hx, hy = _vec(df, "hed", cols)
    if hx is None:
        hx, hy = _vec(df, "heading", cols)
    if x is None or vx is None:
        raise ValueError(f"Could not find position/speed columns. Columns are: {cols}")
    n = len(df)
    if ax is None:
        ax, ay = np.zeros(n), np.zeros(n)
    if hx is None:  # derive heading from velocity
        sp = np.hypot(vx, vy) + 1e-9
        hx, hy = vx / sp, vy / sp

    raw_cls = df[c_cls]
    if raw_cls.dtype == object:
        def to_int(s):
            s2 = str(s).strip()
            if s2.isdigit():
                return int(s2)
            for k, v in CLASS_NAMES.items():
                if _norm(v) == _norm(s2):
                    return k
            if _norm(s2) in ("normal", "benign", "genuine"):
                return 0
            return -99
        cls = raw_cls.map(to_int).values
    else:
        cls = raw_cls.astype(int).values

    sender_raw = df[c_sender].values if c_sender else df[c_pseudo].values
    # The CSV merges 39 simulations; vehicle ids can repeat across simulations, but each vehicle is one
    # contiguous block of rows. Using the block number as vehicle id avoids merging different vehicles.
    sender = np.cumsum(np.r_[True, sender_raw[1:] != sender_raw[:-1]])
    if c_pseudo and stream_by == "pseudo":
        pseudo = pd.Series(sender).astype(str).values + "_" + df[c_pseudo].astype(str).values
    else:
        pseudo = sender
    out = pd.DataFrame({
        "t": df[c_time].astype("float64").values,
        "sender": pd.factorize(sender)[0].astype(np.int64),
        "stream": pd.factorize(pseudo)[0].astype(np.int64),
        "cls": cls.astype(np.int64),
        "x": x, "y": y, "vx": vx, "vy": vy, "ax": ax, "ay": ay, "hx": hx, "hy": hy,
    })
    if c_mid is not None:
        out["mid"] = df[c_mid].values
    if c_rcv is not None and c_rcv != c_time:
        out["rcv"] = df[c_rcv].astype("float64").values

    before = len(out)
    out = out.replace([np.inf, -np.inf], np.nan).dropna(subset=["t", "x", "y", "vx", "vy"])
    out[["ax", "ay", "hx", "hy"]] = out[["ax", "ay", "hx", "hy"]].fillna(0.0)
    # a BSM received by several vehicles appears once per receiver -> keep one copy per message
    if "mid" in out:
        out = out.drop_duplicates(subset=["mid"])
    else:
        out = out.drop_duplicates(subset=["stream", "t", "x", "y"])
    out = out[out["cls"] >= 0]
    if verbose:
        print(f"[data] after cleaning/dedup: {len(out):,} messages (removed {before - len(out):,})")
        vc = out["cls"].value_counts().sort_index()
        print("[data] class counts:", {CLASS_NAMES.get(k, k): int(v) for k, v in vc.items()})
    return out.reset_index(drop=True)


def grouped_split(msgs, seed=0, frac=(0.6, 0.2, 0.2)):
    """Split by *vehicle* (sender) so no vehicle is in two splits; stratified by the vehicle's class."""
    rng = np.random.default_rng(seed)
    veh_cls = msgs.groupby("sender")["cls"].max()
    split = {}
    for c, vehs in veh_cls.groupby(veh_cls):
        v = vehs.index.values.copy()
        rng.shuffle(v)
        n = len(v)
        n_tr = max(1, int(round(frac[0] * n)))
        n_va = max(1 if n >= 3 else 0, int(round(frac[1] * n)))
        for i, vid in enumerate(v):
            split[vid] = "train" if i < n_tr else ("val" if i < n_tr + n_va else "test")
    return msgs["sender"].map(split).values


def add_message_features(msgs):
    """Per-message kinematic, delta (FV-B) and physics-residual (FV-C) features within each stream."""
    m = msgs.sort_values(["stream", "t"], kind="mergesort").reset_index(drop=True)
    g = m["stream"].values
    first = np.r_[True, g[1:] != g[:-1]]

    def prev(a):
        p = np.r_[a[:1], a[:-1]]
        p[first] = a[first]
        return p

    x, y, vx, vy = m["x"].values, m["y"].values, m["vx"].values, m["vy"].values
    ax, ay, hx, hy = m["ax"].values, m["ay"].values, m["hx"].values, m["hy"].values
    t = m["t"].values
    v = np.hypot(vx, vy)
    a = np.hypot(ax, ay)
    dt_raw = t - prev(t)
    med_dt = np.median(dt_raw[~first]) if (~first).any() else 1.0
    dt_raw[first] = med_dt
    dt = np.clip(dt_raw, 1e-3, None)
    dx, dy = x - prev(x), y - prev(y)
    dpos = np.hypot(dx, dy)
    vprev = prev(v)
    dv = v - vprev
    head = np.arctan2(hy, hx)
    dhead = np.abs(np.angle(np.exp(1j * (head - prev(head)))))

    # physics-consistency residuals (FV-C)
    r_disp = np.abs(dpos - 0.5 * (v + vprev) * dt)                      # displacement vs reported speed
    r_implied_v = np.abs(dpos / dt - v)                                 # implied vs reported speed
    px, py = prev(x) + prev(vx) * dt, prev(y) + prev(vy) * dt           # constant-velocity prediction
    r_cv_pred = np.hypot(x - px, y - py)
    r_acc = np.abs(np.abs(dv / dt) - a)                                 # speed change vs reported accel
    vel_ang = np.arctan2(vy, vx)
    r_head_vel = np.where(v > 1.0, np.abs(np.angle(np.exp(1j * (head - vel_ang)))), 0.0)
    mov_ang = np.arctan2(dy, dx)
    r_head_move = np.where(dpos > 0.5, np.abs(np.angle(np.exp(1j * (head - mov_ang)))), 0.0)
    r_vmax = np.maximum(0.0, v - 50.0)                                  # > 180 km/h
    r_amax = np.maximum(0.0, np.abs(dv / dt) - 10.0)                    # > ~1 g
    r_time = (dt_raw <= 0).astype(float) + np.abs(np.log(dt / med_dt))  # non-monotonic / odd beacon gap

    for r in (r_disp, r_implied_v, r_cv_pred, r_acc, r_head_vel, r_head_move, r_amax, r_time):
        r[first] = 0.0
    feats = dict(vx=vx, vy=vy, v=v, ax=ax, ay=ay, a=a, hx=hx, hy=hy,
                 dt=dt, rate=1.0 / dt, dpos=dpos, dv=dv, dhead=dhead,
                 r_disp=r_disp, r_implied_v=r_implied_v, r_cv_pred=r_cv_pred, r_acc=r_acc,
                 r_head_vel=r_head_vel, r_head_move=r_head_move, r_vmax=r_vmax, r_amax=r_amax,
                 r_time=r_time)
    for k, val in feats.items():
        m[k] = val.astype(np.float32)
    m["rel_x"] = 0.0
    m["rel_y"] = 0.0
    return m


def make_windows(m, w=10, stride=5, split_col="split", split=None):
    """Build sliding windows of w consecutive messages from one stream.
    Returns X (N, w, F_C) float32, y (N,), group (N,) vehicle id, and the window start indices."""
    if split is not None:
        sub = np.where(m[split_col].values == split)[0]
    else:
        sub = np.arange(len(m))
    g = m["stream"].values[sub]
    if len(sub) < w:
        return np.zeros((0, w, len(FV_C)), np.float32), np.zeros(0, int), np.zeros(0, int), np.zeros(0, int)
    # a window starting at local i is valid if all w messages are in the same stream
    same = g[: len(g) - w + 1] == g[w - 1:]
    # contiguity inside sub is required too (sub is sorted and m sorted by stream,t)
    contig = (sub[w - 1:] - sub[: len(sub) - w + 1]) == (w - 1)
    starts = np.where(same & contig)[0]
    # apply stride within each stream
    if stride > 1 and len(starts):
        sg = g[starts]
        new = np.r_[True, sg[1:] != sg[:-1]]
        grp_start = np.maximum.accumulate(np.where(new, np.arange(len(starts)), 0))
        rank = np.arange(len(starts)) - grp_start
        starts = starts[rank % stride == 0]
    idx = sub[starts][:, None] + np.arange(w)[None, :]
    F = m[FV_C].values.astype(np.float32)
    X = F[idx]
    ix, iy = FV_C.index("rel_x"), FV_C.index("rel_y")
    pos = m[["x", "y"]].values.astype(np.float64)[idx]
    X[:, :, ix] = (pos[:, :, 0] - pos[:, :1, 0]).astype(np.float32)
    X[:, :, iy] = (pos[:, :, 1] - pos[:, :1, 1]).astype(np.float32)
    cls = m["cls"].values[idx]
    y = cls.max(axis=1)
    grp = m["sender"].values[idx[:, 0]]
    return X, y.astype(int), grp.astype(int), idx[:, 0]


def feature_index(fs):
    return [FV_C.index(f) for f in FEATURE_SETS[fs]]


class Scaler:
    """Signed-log for heavy-tailed features, then standardisation fitted on training windows; clip to +-10."""

    def __init__(self, feats):
        self.feats = feats
        self.log = np.array([f in LOG_FEATS for f in feats])

    def _tf(self, X):
        X = X.copy()
        if self.log.any():
            X[..., self.log] = np.sign(X[..., self.log]) * np.log1p(np.abs(X[..., self.log]))
        return X

    def fit(self, X):
        Z = self._tf(X).reshape(-1, X.shape[-1])
        self.mu = Z.mean(0)
        self.sd = Z.std(0) + 1e-6
        return self

    def transform(self, X):
        return np.clip((self._tf(X) - self.mu) / self.sd, -10, 10).astype(np.float32)


class PhysicsScore:
    """Training-free physics violation score: robust z of each residual against benign training data,
    max over the window, averaged over residuals."""

    def fit(self, X_benign):
        idx = [FV_C.index(p) for p in PHYS]
        R = X_benign[:, :, idx].reshape(-1, len(idx))
        self.med = np.median(R, 0)
        self.scale = np.quantile(R, 0.99, axis=0) - self.med + 1e-3
        return self

    def score(self, X):
        idx = [FV_C.index(p) for p in PHYS]
        Z = np.clip((X[:, :, idx] - self.med) / self.scale, 0, 20)
        return Z.max(axis=1).mean(axis=1)


def summary_features(X):
    """Flattened per-window summary (mean, std, min, max) for tree / IF baselines."""
    return np.concatenate([X.mean(1), X.std(1), X.min(1), X.max(1)], axis=1).astype(np.float32)


def add_noise(msgs, pos_sigma=0.0, spd_sigma=0.0, drop=0.0, seed=0):
    """Noise / packet-loss injection on raw messages (used on the test split only)."""
    rng = np.random.default_rng(seed)
    m = msgs.copy()
    if drop > 0:
        m = m[rng.random(len(m)) >= drop]
    if pos_sigma > 0:
        m["x"] = m["x"] + rng.normal(0, pos_sigma, len(m))
        m["y"] = m["y"] + rng.normal(0, pos_sigma, len(m))
    if spd_sigma > 0:
        m["vx"] = m["vx"] + rng.normal(0, spd_sigma, len(m))
        m["vy"] = m["vy"] + rng.normal(0, spd_sigma, len(m))
    return m.reset_index(drop=True)


def cache_path(cache_dir, name):
    os.makedirs(cache_dir, exist_ok=True)
    return os.path.join(cache_dir, name)
