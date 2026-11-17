"""PyTorch models: HP-MDS Stage 1 temporal autoencoder, Stage 2 compact classifier,
and the deep baselines (Bi-LSTM classifier, LSTM autoencoder)."""
import copy
import time

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")


def set_seed(seed):
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def n_params(model):
    return sum(p.numel() for p in model.parameters())


class ConvGRUAE(nn.Module):
    """Stage 1: 1D-conv + GRU encoder -> latent -> GRU decoder (benign-only training)."""

    def __init__(self, f, w, latent=16, hidden=32):
        super().__init__()
        self.w = w
        self.conv = nn.Conv1d(f, hidden, 3, padding=1)
        self.enc = nn.GRU(hidden, hidden, batch_first=True)
        self.to_z = nn.Linear(hidden, latent)
        self.dec = nn.GRU(latent, hidden, batch_first=True)
        self.out = nn.Linear(hidden, f)

    def forward(self, x):
        h = F.relu(self.conv(x.transpose(1, 2))).transpose(1, 2)
        _, hn = self.enc(h)
        z = self.to_z(hn[-1])
        d, _ = self.dec(z.unsqueeze(1).repeat(1, self.w, 1))
        return self.out(d)

    def score(self, x):
        return ((self.forward(x) - x) ** 2).mean(dim=(1, 2))


class LSTMAE(nn.Module):
    """Baseline (E8-style, centralised): LSTM autoencoder 64-32-(32)-32-64."""

    def __init__(self, f, w):
        super().__init__()
        self.w = w
        self.e1 = nn.LSTM(f, 64, batch_first=True)
        self.e2 = nn.LSTM(64, 32, batch_first=True)
        self.d1 = nn.LSTM(32, 32, batch_first=True)
        self.d2 = nn.LSTM(32, 64, batch_first=True)
        self.out = nn.Linear(64, f)
        self.drop = nn.Dropout(0.2)

    def forward(self, x):
        h, _ = self.e1(x)
        _, (hn, _) = self.e2(self.drop(h))
        z = hn[-1].unsqueeze(1).repeat(1, self.w, 1)
        d, _ = self.d1(z)
        d, _ = self.d2(self.drop(d))
        return self.out(d)

    def score(self, x):
        return ((self.forward(x) - x) ** 2).mean(dim=(1, 2))


class ConvGRUClassifier(nn.Module):
    """Stage 2: compact classifier (<100K params) over benign + known attack classes."""

    def __init__(self, f, n_cls, hidden=48):
        super().__init__()
        self.conv = nn.Conv1d(f, hidden, 3, padding=1)
        self.gru = nn.GRU(hidden, hidden, batch_first=True)
        self.head = nn.Sequential(nn.Linear(2 * hidden, hidden), nn.ReLU(), nn.Dropout(0.2), nn.Linear(hidden, n_cls))

    def forward(self, x):
        h = F.relu(self.conv(x.transpose(1, 2))).transpose(1, 2)
        o, hn = self.gru(h)
        return self.head(torch.cat([hn[-1], o.mean(1)], dim=1))


class BiLSTMClassifier(nn.Module):
    """Baseline (E1-style): single Bi-LSTM layer, 64 units."""

    def __init__(self, f, n_cls):
        super().__init__()
        self.lstm = nn.LSTM(f, 64, batch_first=True, bidirectional=True)
        self.head = nn.Linear(128, n_cls)

    def forward(self, x):
        _, (hn, _) = self.lstm(x)
        return self.head(torch.cat([hn[-2], hn[-1]], dim=1))


def _batches(n, bs, shuffle, rng):
    idx = rng.permutation(n) if shuffle else np.arange(n)
    for i in range(0, n, bs):
        yield idx[i:i + bs]


def train_ae(model, Xtr, Xva, epochs=20, bs=1024, lr=2e-3, patience=3, seed=0, log=None):
    set_seed(seed)
    rng = np.random.default_rng(seed)
    model = model.to(DEVICE)
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    Xtr_t = torch.from_numpy(Xtr).to(DEVICE)
    Xva_t = torch.from_numpy(Xva).to(DEVICE)
    best, best_state, bad, hist = 1e18, None, 0, []
    t0 = time.time()
    for ep in range(epochs):
        model.train()
        tot = 0.0
        for b in _batches(len(Xtr_t), bs, True, rng):
            xb = Xtr_t[b]
            loss = ((model(xb) - xb) ** 2).mean()
            opt.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            tot += loss.item() * len(b)
        vl = float(predict_score(model, Xva_t).mean())
        hist.append((ep, tot / len(Xtr_t), vl))
        if log:
            log(f"    ep {ep:02d} train {tot / len(Xtr_t):.4f} val {vl:.4f}")
        if vl < best - 1e-5:
            best, best_state, bad = vl, copy.deepcopy(model.state_dict()), 0
        else:
            bad += 1
            if bad >= patience:
                break
    model.load_state_dict(best_state)
    return model, hist, time.time() - t0


def train_clf(model, Xtr, ytr, Xva, yva, n_cls, epochs=20, bs=1024, lr=1e-3, patience=3, seed=0, log=None,
              class_weights=True):
    set_seed(seed)
    rng = np.random.default_rng(seed)
    model = model.to(DEVICE)
    counts = np.bincount(ytr, minlength=n_cls).astype(float)
    wts = 1.0 / np.sqrt(np.maximum(counts, 1)) if class_weights else np.ones(n_cls)
    wts = wts / wts.mean()
    crit = nn.CrossEntropyLoss(weight=torch.tensor(wts, dtype=torch.float32, device=DEVICE))
    opt = torch.optim.Adam(model.parameters(), lr=lr)
    Xtr_t, ytr_t = torch.from_numpy(Xtr).to(DEVICE), torch.from_numpy(ytr).long().to(DEVICE)
    Xva_t, yva_t = torch.from_numpy(Xva).to(DEVICE), torch.from_numpy(yva).long().to(DEVICE)
    best, best_state, bad, hist = 1e18, None, 0, []
    t0 = time.time()
    for ep in range(epochs):
        model.train()
        tot = 0.0
        for b in _batches(len(Xtr_t), bs, True, rng):
            loss = crit(model(Xtr_t[b]), ytr_t[b])
            opt.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            tot += loss.item() * len(b)
        model.eval()
        with torch.no_grad():
            vl = 0.0
            for b in _batches(len(Xva_t), 8192, False, rng):
                vl += crit(model(Xva_t[b]), yva_t[b]).item() * len(b)
            vl /= max(1, len(Xva_t))
        hist.append((ep, tot / len(Xtr_t), vl))
        if log:
            log(f"    ep {ep:02d} train {tot / len(Xtr_t):.4f} val {vl:.4f}")
        if vl < best - 1e-5:
            best, best_state, bad = vl, copy.deepcopy(model.state_dict()), 0
        else:
            bad += 1
            if bad >= patience:
                break
    model.load_state_dict(best_state)
    return model, hist, time.time() - t0


@torch.no_grad()
def predict_score(model, X, bs=8192):
    model.eval()
    Xt = X if torch.is_tensor(X) else torch.from_numpy(X)
    dev = next(model.parameters()).device
    out = []
    for i in range(0, len(Xt), bs):
        out.append(model.score(Xt[i:i + bs].to(dev)).float().cpu().numpy())
    return np.concatenate(out) if out else np.zeros(0)


@torch.no_grad()
def predict_logits(model, X, bs=8192):
    model.eval()
    Xt = X if torch.is_tensor(X) else torch.from_numpy(X)
    dev = next(model.parameters()).device
    out = []
    for i in range(0, len(Xt), bs):
        out.append(model(Xt[i:i + bs].to(dev)).float().cpu().numpy())
    return np.concatenate(out) if out else np.zeros((0, 1))


def softmax(z):
    z = z - z.max(1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(1, keepdims=True)


def energy(z):
    m = z.max(1, keepdims=True)
    return -(m[:, 0] + np.log(np.exp(z - m).sum(1)))  # higher = more "unknown"
