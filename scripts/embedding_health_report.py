"""
FOOD-011: Embedding store quality review diagnostic script.

Ranks all dishes by embedding quality signals and flags likely confusion pairs.

Ranking priority (most critical first):
  1. Lowest confirmed_count  — least real-world reinforcement
  2. Highest correction_rate — most often mis-predicted (None = never seen as top-1)
  3. Highest max_peer_sim    — most visually similar to another dish

Confusion pairs: any two dishes with centroid cosine similarity > 0.88 are flagged.

Output: data/embedding_health_report.md

Usage:
    python scripts/embedding_health_report.py
    python scripts/embedding_health_report.py --embeddings-dir data/embeddings \
        --log-path data/correction_log.jsonl --output data/embedding_health_report.md
"""

from __future__ import annotations

import argparse
import datetime
import json
from pathlib import Path

import chromadb
import numpy as np

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EMBEDDINGS_DIR = "data/embeddings"
_LOG_PATH = "data/correction_log.jsonl"
_OUTPUT_PATH = "data/embedding_health_report.md"
_CONFUSION_THRESHOLD = 0.88


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_store(embeddings_dir: str) -> tuple[list[str], np.ndarray, list[dict]]:
    """
    Load all dish centroids and metadata from ChromaDB.

    Returns:
        names:      List of dish name strings, length N.
        embeddings: float32 array of shape (N, D), unit-norm centroids.
        metadatas:  List of metadata dicts, length N.
    """
    client = chromadb.PersistentClient(path=embeddings_dir)
    try:
        col = client.get_collection("dishes")
    except Exception:
        return [], np.empty((0, 512), dtype=np.float32), []

    if col.count() == 0:
        return [], np.empty((0, 512), dtype=np.float32), []

    result = col.get(include=["embeddings", "metadatas"])
    names = [m["dish_name"] for m in result["metadatas"]]
    embeddings = np.array(result["embeddings"], dtype=np.float32)
    return names, embeddings, result["metadatas"]


def _load_correction_stats(log_path: str) -> dict[str, dict]:
    """
    Parse correction_log.jsonl and compute per-dish stats keyed by dish name.

    Stats per dish (as original_prediction):
        total_seen:      # times predicted as top-1
        corrected:       # times user corrected away from this prediction
        correction_rate: corrected / total_seen, or None if total_seen == 0

    Note: dishes never appearing as original_prediction get correction_rate = None.
    """
    stats: dict[str, dict] = {}
    log_file = Path(log_path)

    if not log_file.exists():
        return stats

    for line in log_file.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue

        pred = entry.get("original_prediction", "")
        action = entry.get("action", "")
        if not pred:
            continue

        if pred not in stats:
            stats[pred] = {"total_seen": 0, "corrected": 0}

        stats[pred]["total_seen"] += 1
        if action == "CORRECT":
            stats[pred]["corrected"] += 1

    # Compute rates
    for dish, s in stats.items():
        s["correction_rate"] = (
            s["corrected"] / s["total_seen"] if s["total_seen"] > 0 else None
        )

    return stats


# ---------------------------------------------------------------------------
# Similarity computation
# ---------------------------------------------------------------------------

def _pairwise_cosine(embeddings: np.ndarray) -> np.ndarray:
    """
    Compute NxN cosine similarity matrix.
    Embeddings are assumed unit-norm (as stored by EmbeddingStore).
    cos_sim(a, b) = dot(a, b) for unit-norm vectors.
    """
    return embeddings @ embeddings.T


def _find_confusion_pairs(
    names: list[str],
    sim_matrix: np.ndarray,
    threshold: float = _CONFUSION_THRESHOLD,
) -> list[tuple[str, str, float]]:
    """Return sorted list of (dish_a, dish_b, similarity) for pairs above threshold."""
    n = len(names)
    pairs = []
    for i in range(n):
        for j in range(i + 1, n):
            sim = float(sim_matrix[i, j])
            if sim > threshold:
                pairs.append((names[i], names[j], round(sim, 4)))
    pairs.sort(key=lambda x: x[2], reverse=True)
    return pairs


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def _build_ranked_rows(
    names: list[str],
    metadatas: list[dict],
    sim_matrix: np.ndarray,
    correction_stats: dict[str, dict],
) -> list[dict]:
    """
    Build one row per dish with all quality signals, sorted by priority:
        1. confirmed_count ASC
        2. correction_rate DESC (None treated as -1 so it sorts last)
        3. max_peer_sim DESC
    """
    n = len(names)
    rows = []
    for i in range(n):
        meta = metadatas[i]
        dish = names[i]
        confirmed_count = int(meta.get("confirmed_count", 0))
        date_added = meta.get("date_added", "unknown")

        # Correction stats (may be absent if dish never appeared as a prediction)
        cstats = correction_stats.get(dish, {})
        total_seen = cstats.get("total_seen", 0)
        correction_rate = cstats.get("correction_rate", None)

        # Max similarity to any peer (exclude self)
        peer_sims = [float(sim_matrix[i, j]) for j in range(n) if j != i]
        max_peer_sim = round(max(peer_sims), 4) if peer_sims else 0.0

        rows.append({
            "dish": dish,
            "confirmed_count": confirmed_count,
            "correction_rate": correction_rate,
            "total_seen": total_seen,
            "max_peer_sim": max_peer_sim,
            "date_added": date_added,
        })

    # Sort: confirmed_count ASC, correction_rate DESC (None last), max_peer_sim DESC
    rows.sort(key=lambda r: (
        r["confirmed_count"],
        -(r["correction_rate"] if r["correction_rate"] is not None else -1),
        -r["max_peer_sim"],
    ))

    return rows


# ---------------------------------------------------------------------------
# Report rendering
# ---------------------------------------------------------------------------

def _fmt_rate(rate) -> str:
    if rate is None:
        return "N/A"
    return f"{rate:.0%}"


def _render_report(
    rows: list[dict],
    confusion_pairs: list[tuple[str, str, float]],
    n_dishes: int,
    generated_at: str,
) -> str:
    lines = []

    lines.append("# Embedding Health Report")
    lines.append("")
    lines.append(f"**Generated:** {generated_at}")
    lines.append(f"**Total dishes in store:** {n_dishes}")
    lines.append(f"**Confusion threshold:** cosine similarity > {_CONFUSION_THRESHOLD}")
    lines.append(f"**Confusion pairs flagged:** {len(confusion_pairs)}")
    lines.append("")

    if n_dishes == 0:
        lines.append("> Embedding store is empty. No data to report.")
        return "\n".join(lines)

    # --- Dish quality table ---
    lines.append("## Dish Quality Rankings")
    lines.append("")
    lines.append(
        "Ranked by priority: lowest confirmed_count → highest correction_rate → "
        "highest peer similarity."
    )
    lines.append("")
    lines.append(
        "| # | Dish | confirmed_count | correction_rate | total_seen | max_peer_sim | date_added |"
    )
    lines.append(
        "|---|------|----------------|-----------------|------------|--------------|------------|"
    )

    for rank, row in enumerate(rows, start=1):
        peer_flag = " ⚠" if row["max_peer_sim"] > _CONFUSION_THRESHOLD else ""
        lines.append(
            f"| {rank} "
            f"| {row['dish']} "
            f"| {row['confirmed_count']} "
            f"| {_fmt_rate(row['correction_rate'])} "
            f"| {row['total_seen']} "
            f"| {row['max_peer_sim']}{peer_flag} "
            f"| {row['date_added']} |"
        )

    lines.append("")

    # --- Confusion pairs ---
    lines.append("## Confusion Pairs (similarity > 0.88)")
    lines.append("")
    if not confusion_pairs:
        lines.append("No confusion pairs detected. All dish centroids are sufficiently separated.")
    else:
        lines.append(
            "These dish pairs have highly similar centroids and are likely to be confused "
            "by the classifier. Priority targets for additional training data."
        )
        lines.append("")
        lines.append("| Dish A | Dish B | Cosine Similarity |")
        lines.append("|--------|--------|-------------------|")
        for dish_a, dish_b, sim in confusion_pairs:
            lines.append(f"| {dish_a} | {dish_b} | {sim} |")

    lines.append("")

    # --- Recommendations ---
    lines.append("## Data Collection Priorities")
    lines.append("")

    low_count = [r for r in rows if r["confirmed_count"] < 3]
    high_correction = [
        r for r in rows
        if r["correction_rate"] is not None and r["correction_rate"] > 0.2
    ]

    if low_count:
        lines.append("### Dishes needing more confirmations (confirmed_count < 3)")
        lines.append("")
        for r in low_count:
            lines.append(f"- **{r['dish']}** — {r['confirmed_count']} confirmations so far")
    else:
        lines.append("All dishes have >= 3 confirmations.")

    lines.append("")

    if high_correction:
        lines.append("### High correction rate (> 20%)")
        lines.append("")
        for r in high_correction:
            lines.append(
                f"- **{r['dish']}** — corrected {_fmt_rate(r['correction_rate'])} "
                f"of {r['total_seen']} predictions"
            )
    else:
        lines.append(
            "No dishes with correction rate > 20% "
            "(or no prediction history available yet)."
        )

    lines.append("")

    if confusion_pairs:
        lines.append("### Confusion pairs — collect more diverse images for these dishes")
        lines.append("")
        for dish_a, dish_b, sim in confusion_pairs:
            lines.append(f"- **{dish_a}** ↔ **{dish_b}** (sim={sim})")

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(
        "_Note: `correction_rate = N/A` means the dish has never appeared as a top-1 "
        "prediction in correction_log.jsonl. This is expected for newly added dishes._"
    )

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="FOOD-011: Embedding health report")
    parser.add_argument("--embeddings-dir", default=_EMBEDDINGS_DIR)
    parser.add_argument("--log-path", default=_LOG_PATH)
    parser.add_argument("--output", default=_OUTPUT_PATH)
    args = parser.parse_args()

    print(f"Loading embedding store from {args.embeddings_dir} ...")
    names, embeddings, metadatas = _load_store(args.embeddings_dir)
    print(f"  {len(names)} dishes found")

    print(f"Loading correction stats from {args.log_path} ...")
    correction_stats = _load_correction_stats(args.log_path)
    print(f"  {len(correction_stats)} dishes with prediction history")

    if len(names) == 0:
        print("Embedding store is empty — writing empty report.")
        report = _render_report([], [], 0, datetime.datetime.now().isoformat(timespec="seconds"))
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(report)
        print(f"Report saved to {args.output}")
        return

    print("Computing pairwise cosine similarity ...")
    sim_matrix = _pairwise_cosine(embeddings)

    confusion_pairs = _find_confusion_pairs(names, sim_matrix)
    print(f"  {len(confusion_pairs)} confusion pair(s) with similarity > {_CONFUSION_THRESHOLD}")

    rows = _build_ranked_rows(names, metadatas, sim_matrix, correction_stats)

    generated_at = datetime.datetime.now().isoformat(timespec="seconds")
    report = _render_report(rows, confusion_pairs, len(names), generated_at)

    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(report)
    print(f"\nReport saved to {args.output}")

    # Print summary to stdout
    print(f"\n{'='*55}")
    print(f"  FOOD-011 Embedding Health Summary")
    print(f"{'='*55}")
    print(f"  Dishes in store:    {len(names)}")
    print(f"  Confusion pairs:    {len(confusion_pairs)}")
    if confusion_pairs:
        print(f"  Flagged pairs:")
        for a, b, sim in confusion_pairs:
            print(f"    {a} <-> {b}  (sim={sim})")
    print(f"\n  Top 5 priority dishes (most data needed):")
    for r in rows[:5]:
        rate_str = _fmt_rate(r["correction_rate"])
        print(
            f"    [{r['confirmed_count']:>3} confirms | corr={rate_str:>5} | "
            f"peer_sim={r['max_peer_sim']:.3f}]  {r['dish']}"
        )


if __name__ == "__main__":
    main()
