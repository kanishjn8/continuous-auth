"""Offline demo: run the real evaluation pipeline on synthetic data and plot it.

This wires together existing, already-tested building blocks only
(``ml.datasets.synthetic``, ``ml.features.extractor``, ``ml.training``,
``ml.evaluation``) -- the same recipe ``ml/tests/test_evaluation_pipeline.py``
already exercises and asserts bounds on. No new modeling or metrics code.

Per ``docs/evaluation.md``, synthetic data validates that the pipeline is
mechanically wired correctly end-to-end; it is never a claim of real-world
authentication accuracy. Every printed/plotted number here is labeled
accordingly.

Usage (from repo root, with the project installed -- ``pip install -e ".[backend]"``
-- and matplotlib available):

    python tools/demo/run_synthetic_evaluation.py

Requires no live backend, collector, or dashboard. Writes a PNG and a JSON
summary to ``tools/demo/output/``.
"""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from ml.evaluation.cross_evaluation import zero_effort_cross_evaluation
from ml.evaluation.metrics import compute_eer, compute_roc_det_curve
from ml.evaluation.splitting import day_disjoint_split, distinct_days
from ml.features.config import load_config
from ml.tests.conftest import generate_multiday_user_windows
from ml.training.isolation_forest import train_user_modality_isolation_forest

OUTPUT_DIR = Path(__file__).parent / "output"

# Two synthetic identities with clearly different typing cadence (150ms vs
# 500ms mean digraph latency), matching the separation used in
# ml/tests/test_evaluation_pipeline.py's own EER assertion (eer < 0.3).
USERS = {
    "alice": {"seed": 1, "mean_dd_latency_us": 150_000.0, "std_dd_latency_us": 15_000.0},
    "bob": {"seed": 2, "mean_dd_latency_us": 500_000.0, "std_dd_latency_us": 15_000.0},
}
NUM_DAYS = 6
SEGMENT_MINUTES = 15
MODALITY = "keyboard"


def main() -> None:
    config = load_config()

    corpus = {
        user_id: generate_multiday_user_windows(
            user_id,
            params["seed"],
            config,
            num_days=NUM_DAYS,
            segments_per_day=1,
            segment_minutes=SEGMENT_MINUTES,
            mean_dd_latency_us=params["mean_dd_latency_us"],
            std_dd_latency_us=params["std_dd_latency_us"],
        )
        for user_id, params in USERS.items()
    }

    test_days = {user_id: [distinct_days(windows)[-1]] for user_id, windows in corpus.items()}

    artifacts = {}
    test_windows = {}
    for user_id, windows in corpus.items():
        split = day_disjoint_split(windows, test_days=test_days[user_id])
        artifacts[user_id] = train_user_modality_isolation_forest(
            user_id, MODALITY, split.train, config
        )
        test_windows[user_id] = split.test

    results = zero_effort_cross_evaluation(artifacts, test_windows)

    summary = {}
    fig, axes = plt.subplots(len(results), 2, figsize=(11, 4.5 * len(results)), squeeze=False)

    for row, (user_id, result) in enumerate(results.items()):
        genuine = np.asarray(result.genuine_scores)
        impostor = result.all_impostor_scores()
        eer_result = compute_eer(genuine, impostor)
        roc = compute_roc_det_curve(genuine, impostor)

        summary[user_id] = {
            "eer": eer_result.eer,
            "eer_threshold": eer_result.threshold,
            "n_genuine_windows": len(genuine),
            "n_impostor_windows": len(impostor),
            "impostor_users": list(result.impostor_scores.keys()),
        }

        ax_hist, ax_roc = axes[row]
        ax_hist.hist(genuine, bins=20, alpha=0.6, label="genuine", color="tab:blue")
        ax_hist.hist(impostor, bins=20, alpha=0.6, label="impostor", color="tab:red")
        ax_hist.axvline(eer_result.threshold, color="black", linestyle="--", linewidth=1,
                         label=f"EER threshold ({eer_result.threshold:.1f})")
        ax_hist.set_title(f"{user_id}: calibrated score distribution")
        ax_hist.set_xlabel("calibrated percentile score (0-100)")
        ax_hist.set_ylabel("window count")
        ax_hist.legend(fontsize=8)

        ax_roc.plot(roc.far, 1 - roc.frr, color="tab:green")
        ax_roc.plot([0, 1], [0, 1], color="gray", linestyle=":", linewidth=1)
        ax_roc.scatter(
            [eer_result.eer], [1 - eer_result.eer], color="black", zorder=5,
            label=f"EER = {eer_result.eer:.1%}",
        )
        ax_roc.set_title(f"{user_id}: ROC (window-level)")
        ax_roc.set_xlabel("FAR (false accept rate)")
        ax_roc.set_ylabel("1 - FRR (true accept rate)")
        ax_roc.legend(fontsize=8)

    fig.suptitle(
        "Synthetic pipeline-validation result — NOT a claim of real-world accuracy\n"
        "(per docs/evaluation.md: synthetic data validates plumbing only)",
        fontsize=10,
    )
    fig.tight_layout(rect=(0, 0, 1, 0.96))

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    png_path = OUTPUT_DIR / "synthetic_evaluation.png"
    json_path = OUTPUT_DIR / "synthetic_evaluation_summary.json"
    fig.savefig(png_path, dpi=150)
    json_path.write_text(json.dumps(summary, indent=2))

    print("Synthetic pipeline-validation result -- NOT a claim of real-world accuracy.")
    print(f"(modality={MODALITY}, {NUM_DAYS} synthetic days/user, day-disjoint split)\n")
    for user_id, entry in summary.items():
        print(
            f"  {user_id}: EER={entry['eer']:.1%} "
            f"(n_genuine={entry['n_genuine_windows']}, n_impostor={entry['n_impostor_windows']}, "
            f"impostors={entry['impostor_users']})"
        )
    print(f"\nPlot saved to: {png_path}")
    print(f"Summary saved to: {json_path}")


if __name__ == "__main__":
    main()
