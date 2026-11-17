"""Loader for the ORIGINAL VeReMi dataset (2018) for the cross-dataset test (Exp C).

Each simulation folder contains traceGroundTruthJSON-*.json (type 4 lines: true sender behaviour per
messageID, with attackerType) and one traceJSON-*.json log per receiving vehicle (type 3 = received BSM).
We label each received BSM through its messageID and map the attack ids to the VeReMi Extension classes.
Original VeReMi has no acceleration/heading fields, so the shared field subset is position + speed + time.
"""
import glob
import io
import json
import os
import tarfile
from multiprocessing import Pool

import numpy as np
import pandas as pd

# original attackerType -> VeReMi Extension class id
ORIG_TO_EXT = {0: 0, 1: 1, 2: 2, 4: 3, 8: 4, 16: 9}


def _scenario_dirs(root):
    """Scenario sources: extracted folders that hold a ground-truth file, or .tgz archives (read directly)."""
    gts = glob.glob(os.path.join(root, "**", "*GroundTruth*.json"), recursive=True)
    dirs = sorted({os.path.dirname(g) for g in gts})
    tgz = sorted(glob.glob(os.path.join(root, "**", "*.tgz"), recursive=True)
                 + glob.glob(os.path.join(root, "**", "*.tar.gz"), recursive=True))
    return dirs + tgz


def _iter_files(src):
    """Yield (basename, line-iterator) for every JSON trace file in a folder or a .tgz archive."""
    if os.path.isdir(src):
        for f in glob.glob(os.path.join(src, "*.json")):
            with open(f) as fh:
                yield os.path.basename(f), fh
    else:
        with tarfile.open(src, "r:gz") as tf:
            for mem in tf:
                if mem.isfile() and mem.name.endswith(".json"):
                    fh = tf.extractfile(mem)
                    yield os.path.basename(mem.name), io.TextIOWrapper(fh, encoding="utf-8")


def _load_one(args):
    sid, d = args
    gt = {}
    traces = []
    for name, fh in _iter_files(d):
        if "GroundTruth" in name:
            for line in fh:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("type") == 4:
                    gt[r["messageID"]] = (r.get("attackerType", 0), r.get("sender"))
        elif name.startswith("traceJSON") or name.startswith("JSONlog"):
            for line in fh:
                if '"type":3' not in line.replace(" ", ""):
                    continue
                try:
                    traces.append(json.loads(line))
                except Exception:
                    continue
    rows = []
    for r in traces:
        at, _ = gt.get(r.get("messageID"), (None, None))
        if at is None:
            continue
        p, s = r.get("pos", [np.nan] * 2), r.get("spd", [np.nan] * 2)
        rows.append((r.get("sendTime", r.get("rcvTime")), r.get("sender"), r.get("messageID"),
                     ORIG_TO_EXT.get(int(at), -1), p[0], p[1], s[0], s[1]))
    df = pd.DataFrame(rows, columns=["t", "sender_raw", "mid", "cls", "x", "y", "vx", "vy"])
    df["scen"] = sid
    return df


def load_original_veremi(root, max_scenarios=None, workers=None, verbose=True):
    dirs = _scenario_dirs(root)
    if not dirs:
        raise FileNotFoundError(f"No VeReMi ground-truth files or .tgz archives found under {root}")
    if max_scenarios:
        rng = np.random.default_rng(0)
        dirs = [dirs[i] for i in sorted(rng.choice(len(dirs), min(max_scenarios, len(dirs)), replace=False))]
    if verbose:
        print(f"[veremi] loading {len(dirs)} scenario folders from {root}")
    with Pool(workers or max(1, (os.cpu_count() or 2) - 1)) as pool:
        parts = pool.map(_load_one, list(enumerate(dirs)))
    df = pd.concat(parts, ignore_index=True)
    df = df.drop_duplicates(subset=["scen", "mid"])
    df = df[df["cls"] >= 0].dropna(subset=["t", "x", "y", "vx", "vy"])
    key = df["scen"].astype(str) + "_" + df["sender_raw"].astype(str)
    df["sender"] = pd.factorize(key)[0]
    df["stream"] = df["sender"]
    df["ax"] = 0.0
    df["ay"] = 0.0
    sp = np.hypot(df["vx"], df["vy"]) + 1e-9
    df["hx"], df["hy"] = df["vx"] / sp, df["vy"] / sp
    if verbose:
        print(f"[veremi] {len(df):,} messages; classes {df['cls'].value_counts().to_dict()}")
    return df[["t", "sender", "stream", "cls", "x", "y", "vx", "vy", "ax", "ay", "hx", "hy"]].reset_index(drop=True)


def to_shared_fields(msgs):
    """Make VeReMi Extension messages look like original VeReMi: no acceleration, heading from velocity."""
    m = msgs.copy()
    m["ax"] = 0.0
    m["ay"] = 0.0
    sp = np.hypot(m["vx"], m["vy"]) + 1e-9
    m["hx"], m["hy"] = m["vx"] / sp, m["vy"] / sp
    return m
