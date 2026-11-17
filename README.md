# HP-MDS — Phase 3 implementation

Hybrid Physics-informed Misbehavior Detection System (two-stage) for V2X Basic Safety Messages, with all
baselines, experiments, ablations, statistics, figures and an auto-generated `results/RESULTS.md`.
Group: Munhim Qazi (24I-2506), M. Omar Waqar (24I-0044).


## Files
| File | What it does |
|---|---|
| `run.py` | Runs every experiment (`--exp ...`); saves results as it goes and skips finished model/seed runs on restart |
| `hpmds/data.py` | Loads the VeReMi Extension CSV, vehicle-grouped 60/20/20 split, FV-A/B/C features, 9 physics residuals, windowing, noise injection |
| `hpmds/models.py` | PyTorch models: Stage 1 Conv-GRU autoencoder, Stage 2 Conv-GRU classifier, Bi-LSTM, LSTM-AE |
| `hpmds/methods.py` | HP-MDS (score fusion, thresholds, open-set rejection) and baselines: Random Forest, XGBoost, Bi-LSTM, LSTM-AE, Isolation Forest, physics-only, KNN-bagging*, stacked ensemble* |
| `hpmds/evaluate.py` | Metrics, threshold selection, vehicle-level bootstrap, McNemar, Wilcoxon, Holm correction |
| `hpmds/veremi_original.py` | Loader for original VeReMi (reads the .tgz archives directly) for the cross-dataset test |
| `hpmds/plots.py`, `hpmds/report.py` | Figures (`results/figures/`) and `results/RESULTS.md` |
| `*.bat` | One-click Windows scripts (below) |

## Experiments (`python run.py --exp <name>`)
| Name | Experiment |
|---|---|
| `prepare` | Load CSV, build features and windows, cache them (`cache/`) |
| `A` | In-distribution evaluation, all models (binary + 20-class, per-attack) |
| `B` | Traffic-density shift: off-peak hours vs rush hours (07–09, 17–19) |
| `C` | Cross-dataset: train VeReMi Extension (shared fields) → test original VeReMi |
| `D` | Open set: 3 hold-out sets of 4 attack types removed from training |
| `E` | GPS noise and packet-loss robustness |
| `F` | Latency, throughput, parameters, model size (1 CPU thread) |
| `G` | Detection delay per attacker stream + error analysis |
| `H` | Timestamp jitter, adversarial (PGD) sanity check, permutation importance, peak memory, OSCR |
| `onnx` | ONNX export + onnxruntime latency |
| `ablation` | Components (incl. "HP-MDS + confident Stage 2 alarms"), feature sets, latent size, imbalance handling, XGBoost with/without physics features, window size, α and threshold sweeps |
| `stats` / `figures` / `report` | Statistical tests / figures / `RESULTS.md` |

Useful options: `--seeds`, `--open-seeds`, `--abl-seeds`, `--c-seeds`, `--b-seeds` (number of seeds),
`--stream-by pseudo` (windows per pseudonym instead of per vehicle), `--quick` (5% of vehicles, smoke test),
`--no-secondary` (skip KNN-bagging and stacked ensemble), `--out <folder>`.

## Run on Windows (double-click, in this order)
1. `1_setup_and_smoke_test.bat` — installs packages (PyTorch CUDA 12.8 + `requirements.txt`) and runs a 5% smoke test (~3 min).
2. `2_run_full.bat` — builds the cache, then opens two windows: `_t1.bat` (Exp A, E, F, stats, ablations) and `_t2.bat` (Exp D, then Exp C).
3. `4_run_extra_experiments.bat` — opens `_t3.bat`: Exp B, G, ONNX, pseudonym check, then tops Exp D, ablations, C and B up to 5 seeds once windows 1 and 2 finish.
4. `5_run_final_checks.bat` — Exp H (jitter, adversarial, permutation importance, memory, OSCR).
5. `3_make_figures_and_report.bat` — regenerates statistics, figures and `results/RESULTS.md`.

Logs go to `logs/`. If a run crashes, run the same script again: finished runs are skipped.
Total time on a Ryzen 5 7500F + RTX 5070 + 32 GB RAM: about 3 hours for everything.

Manual equivalent (PowerShell, from this folder):
```
$CSV = "..\VeReMi_Extension Dataset for Misbehaviors in VANETs\mixalldata_clean.csv"
python run.py --csv $CSV --exp prepare
python run.py --exp A E F stats --seeds 5
python run.py --exp D --open-seeds 5
python run.py --exp ablation --abl-seeds 5
python run.py --csv $CSV --exp C --veremi-dir ..\veremi_original --veremi-max-scen 225 --c-seeds 5
python run.py --exp B G H onnx --b-seeds 5
python run.py --csv $CSV --stream-by pseudo --exp prepare A --seeds 3 --no-secondary --out results_pseudo
python run.py --exp stats figures report
```

## Key settings (match the Phase 2 methodology)
- Window w = 10 messages of one vehicle; train stride 5, validation/test stride 2.
- Split 60/20/20 **by vehicle** (stratified by the vehicle's class); vehicles are numbered by contiguous row blocks because the CSV merges 39 simulations.
- Scalers, physics-score statistics and Stage 1 training use **training data only** (Stage 1 = benign windows only).
- Fusion weight α and the alarm threshold (benign-validation percentile) are tuned on validation only.
- Open-set threshold τ keeps 95% of flagged known-attack validation windows.
- Seeds: 5 for Exp A, B, C, D and ablations; 3 for the pseudonym check; secondary baselines (*) 1 seed on a 40–60k subsample.
- Caches are pickle files in `cache/` (safe to delete; rebuilt by `prepare`).
