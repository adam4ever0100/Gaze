#!/usr/bin/env python3
"""
Algorithm Validation & Evaluation Script for Gaze Attention Monitoring.

This script provides three modes for empirically validating the attention
detection algorithm's parameters:

1. GUIDED RECORDING — Records webcam footage with automatically generated
   ground-truth labels by prompting the user to perform specific actions
   (look center, look away, close eyes, etc.).

2. EVALUATION — Compares algorithm predictions against human-labeled
   ground-truth (CSV) and reports accuracy, precision, recall, F1-score,
   and a confusion matrix.

3. GRID SEARCH — Tests multiple weight combinations (Gaze, Head Pose,
   Eye Openness, Face Presence) and reports which set yields the best
   overall F1-score against the labeled data.

Usage:
    # Record a guided test session (saves video + labels)
    python evaluate_algorithm.py --guided-record --duration 90

    # Evaluate against labeled data
    python evaluate_algorithm.py --evaluate data/test_recording.avi data/test_labels.csv

    # Grid search for optimal weights
    python evaluate_algorithm.py --grid-search data/test_recording.avi data/test_labels.csv

    # Run all modes in sequence (record → evaluate → grid search)
    python evaluate_algorithm.py --full-pipeline --duration 90
"""

import argparse
import csv
import cv2
import json
import numpy as np
import os
import sys
import time
from collections import defaultdict
from itertools import product

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.ai_engine.attention_detector import AttentionDetector
from config import (
    WEIGHT_GAZE, WEIGHT_HEAD_POSE, WEIGHT_EYE_OPENNESS, WEIGHT_FACE_PRESENCE,
    THRESHOLD_FOCUSED, THRESHOLD_PARTIAL
)


# ============================================================
# Constants
# ============================================================

DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")

# Guided recording prompts — each tuple is (duration_seconds, label, instruction)
GUIDED_PROMPTS = [
    (8, "Focused",    "Look directly at the CENTER of your screen"),
    (5, "Focused",    "Keep looking at the screen naturally"),
    (5, "Distracted", "Look to the LEFT (turn your head)"),
    (5, "Focused",    "Look back at the CENTER of the screen"),
    (5, "Distracted", "Look to the RIGHT (turn your head)"),
    (5, "Focused",    "Look back at the CENTER of the screen"),
    (5, "Distracted", "Look DOWN at your lap / phone"),
    (5, "Focused",    "Look back at the CENTER of the screen"),
    (5, "Distracted", "Look UP at the ceiling"),
    (5, "Focused",    "Look back at the CENTER of the screen"),
    (8, "Drowsy",     "Half-close your eyes — pretend to be sleepy"),
    (5, "Focused",    "Open your eyes and look at the screen"),
    (5, "Absent",     "Cover the camera or move out of frame"),
    (5, "Focused",    "Come back into frame, look at the screen"),
    (5, "Distracted", "Turn your head to talk to someone beside you"),
    (5, "Focused",    "Look back at the CENTER of the screen"),
]


# ============================================================
# Utility Functions
# ============================================================

def ensure_data_dir():
    """Ensure the data directory exists."""
    os.makedirs(DATA_DIR, exist_ok=True)


def classify_score(score, focused_thresh=None, partial_thresh=None):
    """Classify an attention score into a status label."""
    ft = focused_thresh if focused_thresh is not None else THRESHOLD_FOCUSED
    pt = partial_thresh if partial_thresh is not None else THRESHOLD_PARTIAL
    if score >= ft:
        return "Focused"
    elif score >= pt:
        return "Partially Attentive"
    else:
        return "Distracted"


def map_label_to_predicted(label):
    """
    Map ground-truth labels to the categories the algorithm can predict.
    Drowsy and Absent are treated as Distracted for basic evaluation,
    since the base algorithm only outputs Focused/Partial/Distracted.
    """
    mapping = {
        "Focused": "Focused",
        "Distracted": "Distracted",
        "Partially Attentive": "Partially Attentive",
        "Drowsy": "Distracted",
        "Absent": "Distracted",
        "Phone Use": "Distracted",
    }
    return mapping.get(label, label)


def compute_metrics(y_true, y_pred, labels=None):
    """
    Compute per-class and overall metrics without sklearn dependency.
    Returns a dict with per-class precision, recall, F1, and overall accuracy.
    """
    if labels is None:
        labels = sorted(set(y_true) | set(y_pred))

    # Build confusion matrix
    label_to_idx = {l: i for i, l in enumerate(labels)}
    n = len(labels)
    confusion = [[0] * n for _ in range(n)]
    for true, pred in zip(y_true, y_pred):
        ti = label_to_idx.get(true, -1)
        pi = label_to_idx.get(pred, -1)
        if ti >= 0 and pi >= 0:
            confusion[ti][pi] += 1

    # Per-class metrics
    per_class = {}
    total_correct = 0
    total_samples = len(y_true)

    for i, label in enumerate(labels):
        tp = confusion[i][i]
        fp = sum(confusion[j][i] for j in range(n)) - tp
        fn = sum(confusion[i][j] for j in range(n)) - tp

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0
        support = sum(confusion[i])

        per_class[label] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": support,
        }
        total_correct += tp

    accuracy = total_correct / total_samples if total_samples > 0 else 0.0

    # Macro-average F1
    macro_f1 = sum(pc["f1"] for pc in per_class.values()) / len(per_class) if per_class else 0.0

    # Weighted-average F1
    weighted_f1 = (
        sum(pc["f1"] * pc["support"] for pc in per_class.values()) / total_samples
        if total_samples > 0 else 0.0
    )

    return {
        "accuracy": round(accuracy, 4),
        "macro_f1": round(macro_f1, 4),
        "weighted_f1": round(weighted_f1, 4),
        "per_class": per_class,
        "confusion_matrix": confusion,
        "labels": labels,
    }


def print_classification_report(metrics):
    """Pretty-print a classification report."""
    labels = metrics["labels"]
    per_class = metrics["per_class"]

    print("\n" + "=" * 65)
    print("                  CLASSIFICATION REPORT")
    print("=" * 65)
    print(f"{'Class':<22} {'Precision':>10} {'Recall':>10} {'F1-Score':>10} {'Support':>10}")
    print("-" * 65)

    for label in labels:
        pc = per_class.get(label, {})
        print(
            f"{label:<22} {pc.get('precision', 0):>10.4f} "
            f"{pc.get('recall', 0):>10.4f} "
            f"{pc.get('f1', 0):>10.4f} "
            f"{pc.get('support', 0):>10d}"
        )

    print("-" * 65)
    print(f"{'Accuracy':<22} {'':>10} {'':>10} {metrics['accuracy']:>10.4f} {sum(pc.get('support', 0) for pc in per_class.values()):>10d}")
    print(f"{'Macro Avg F1':<22} {'':>10} {'':>10} {metrics['macro_f1']:>10.4f}")
    print(f"{'Weighted Avg F1':<22} {'':>10} {'':>10} {metrics['weighted_f1']:>10.4f}")
    print("=" * 65)

    # Confusion matrix
    print("\nConfusion Matrix:")
    header = "          " + "  ".join(f"{l[:8]:>8}" for l in labels)
    print(header)
    for i, label in enumerate(labels):
        row = f"{label[:8]:>8}  " + "  ".join(f"{metrics['confusion_matrix'][i][j]:>8d}" for j in range(len(labels)))
        print(row)
    print()


# ============================================================
# Mode 1: Guided Recording
# ============================================================

def guided_record(duration_total=90, output_prefix="test_recording"):
    """
    Record webcam footage with automatically generated ground-truth labels.
    The user is prompted to perform specific actions (look center, look away, etc.)
    and each frame is labeled accordingly.
    """
    ensure_data_dir()

    video_path = os.path.join(DATA_DIR, f"{output_prefix}.avi")
    labels_path = os.path.join(DATA_DIR, f"{output_prefix}_labels.csv")

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("❌ Could not open webcam")
        return None, None

    frame_w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = 15

    fourcc = cv2.VideoWriter_fourcc(*"MJPG")
    writer = cv2.VideoWriter(video_path, fourcc, fps, (frame_w, frame_h))

    labels = []
    frame_idx = 0
    start_time = time.time()

    print("\n" + "=" * 60)
    print("  GUIDED RECORDING SESSION")
    print("  Follow the instructions shown on screen.")
    print("  Press 'q' to quit early.")
    print("=" * 60)

    # Countdown before starting
    for countdown in range(3, 0, -1):
        ret, frame = cap.read()
        if ret:
            overlay = frame.copy()
            cv2.putText(overlay, f"Starting in {countdown}...", (frame_w // 4, frame_h // 2),
                        cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 255, 0), 3)
            cv2.imshow("Gaze Evaluation - Guided Recording", overlay)
            cv2.waitKey(1000)

    prompt_idx = 0
    prompt_start = time.time()

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        # Determine current prompt
        elapsed_in_prompt = time.time() - prompt_start
        if prompt_idx < len(GUIDED_PROMPTS):
            prompt_duration, prompt_label, prompt_text = GUIDED_PROMPTS[prompt_idx]
            if elapsed_in_prompt >= prompt_duration:
                prompt_idx += 1
                prompt_start = time.time()
                if prompt_idx >= len(GUIDED_PROMPTS):
                    break
                prompt_duration, prompt_label, prompt_text = GUIDED_PROMPTS[prompt_idx]
        else:
            break

        total_elapsed = time.time() - start_time
        if total_elapsed >= duration_total:
            break

        # Record frame and label
        writer.write(frame)
        labels.append({
            "frame_idx": frame_idx,
            "timestamp_sec": round(total_elapsed, 3),
            "label": prompt_label,
        })
        frame_idx += 1

        # Display overlay
        overlay = frame.copy()
        # Dark semi-transparent banner at top
        cv2.rectangle(overlay, (0, 0), (frame_w, 90), (0, 0, 0), -1)
        frame_display = cv2.addWeighted(overlay, 0.7, frame, 0.3, 0)

        # Instruction text
        color_map = {
            "Focused": (0, 255, 0),
            "Distracted": (0, 0, 255),
            "Drowsy": (0, 165, 255),
            "Absent": (128, 128, 128),
        }
        color = color_map.get(prompt_label, (255, 255, 255))

        cv2.putText(frame_display, f"[{prompt_label}] {prompt_text}",
                    (10, 35), cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

        remaining = prompt_duration - elapsed_in_prompt
        cv2.putText(frame_display, f"Next in: {remaining:.1f}s | Total: {total_elapsed:.0f}s",
                    (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1)

        # Progress bar
        progress = total_elapsed / duration_total
        bar_w = int(frame_w * progress)
        cv2.rectangle(frame_display, (0, 85), (bar_w, 90), (0, 200, 0), -1)

        cv2.imshow("Gaze Evaluation - Guided Recording", frame_display)
        if cv2.waitKey(int(1000 / fps)) & 0xFF == ord("q"):
            print("Recording stopped early by user.")
            break

    writer.release()
    cap.release()
    cv2.destroyAllWindows()

    # Save labels CSV
    with open(labels_path, "w", newline="") as f:
        writer_csv = csv.DictWriter(f, fieldnames=["frame_idx", "timestamp_sec", "label"])
        writer_csv.writeheader()
        writer_csv.writerows(labels)

    print(f"\n✅ Recording saved: {video_path}")
    print(f"✅ Labels saved:    {labels_path}")
    print(f"   Frames recorded: {frame_idx}")
    print(f"   Duration:        {time.time() - start_time:.1f}s")

    return video_path, labels_path


# ============================================================
# Mode 2: Evaluation
# ============================================================

def evaluate(video_path, labels_path, weights=None):
    """
    Run the attention detector on a recorded video and compare
    predictions against ground-truth labels.

    Args:
        video_path: Path to the recorded video file.
        labels_path: Path to the CSV with ground-truth labels.
        weights: Optional dict with custom weights for scoring.

    Returns:
        Metrics dict with accuracy, precision, recall, F1, confusion matrix.
    """
    # Load labels
    gt_labels = {}
    with open(labels_path, "r") as f:
        reader = csv.DictReader(f)
        for row in reader:
            gt_labels[int(row["frame_idx"])] = row["label"]

    if not gt_labels:
        print("❌ No labels found in CSV")
        return None

    # Open video
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        print(f"❌ Could not open video: {video_path}")
        return None

    # Initialize detector
    detector = AttentionDetector()

    # Override weights if provided
    if weights:
        # We'll manually compute scores with custom weights
        custom_weights = weights
    else:
        custom_weights = None

    y_true = []
    y_pred = []
    raw_scores = []
    frame_idx = 0

    while True:
        ret, frame = cap.read()
        if not ret:
            break

        if frame_idx not in gt_labels:
            frame_idx += 1
            continue

        # Process frame
        metrics = detector.process_frame(frame)

        # Get prediction
        if custom_weights:
            # Recompute attention score with custom weights
            custom_score = (
                custom_weights.get("gaze", WEIGHT_GAZE) * metrics.get("gaze_score", 0) +
                custom_weights.get("head_pose", WEIGHT_HEAD_POSE) * metrics.get("head_pose_score", 0) +
                custom_weights.get("eye_openness", WEIGHT_EYE_OPENNESS) * metrics.get("eye_openness", 0) +
                custom_weights.get("face_presence", WEIGHT_FACE_PRESENCE) * metrics.get("face_presence", 0)
            )
            predicted_label = classify_score(custom_score)
        else:
            predicted_label = metrics.get("status", "Distracted")

        # Map ground truth to comparable labels
        gt_label = map_label_to_predicted(gt_labels[frame_idx])
        predicted_mapped = map_label_to_predicted(predicted_label)

        y_true.append(gt_label)
        y_pred.append(predicted_mapped)
        raw_scores.append(metrics.get("attention_score", 0))

        frame_idx += 1

    cap.release()
    detector.release()

    if not y_true:
        print("❌ No frames matched between video and labels")
        return None

    # Compute metrics
    all_labels = ["Focused", "Partially Attentive", "Distracted"]
    metrics = compute_metrics(y_true, y_pred, labels=all_labels)
    metrics["total_frames"] = len(y_true)
    metrics["avg_score"] = round(sum(raw_scores) / len(raw_scores), 4) if raw_scores else 0

    return metrics


# ============================================================
# Mode 3: Grid Search
# ============================================================

def grid_search(video_path, labels_path):
    """
    Test multiple weight combinations and report which yields the best F1.
    """
    print("\n" + "=" * 60)
    print("  GRID SEARCH — Testing Weight Combinations")
    print("=" * 60)

    # Define search space (all must sum to 1.0)
    gaze_range = [0.25, 0.30, 0.35, 0.40, 0.45]
    head_range = [0.20, 0.25, 0.30, 0.35]
    eye_range = [0.15, 0.20, 0.25, 0.30]
    # face_presence is computed as 1.0 - sum(others)

    results = []
    total_combos = 0

    # Count valid combinations
    for g, h, e in product(gaze_range, head_range, eye_range):
        f = round(1.0 - g - h - e, 2)
        if 0.05 <= f <= 0.20:
            total_combos += 1

    print(f"  Testing {total_combos} valid weight combinations...")

    tested = 0
    for g, h, e in product(gaze_range, head_range, eye_range):
        f = round(1.0 - g - h - e, 2)
        if not (0.05 <= f <= 0.20):
            continue

        weights = {"gaze": g, "head_pose": h, "eye_openness": e, "face_presence": f}
        metrics = evaluate(video_path, labels_path, weights=weights)

        if metrics:
            results.append({
                "weights": weights,
                "accuracy": metrics["accuracy"],
                "macro_f1": metrics["macro_f1"],
                "weighted_f1": metrics["weighted_f1"],
                "per_class": metrics["per_class"],
            })

        tested += 1
        if tested % 10 == 0:
            print(f"  Progress: {tested}/{total_combos} combinations tested...")

    if not results:
        print("❌ No valid results from grid search")
        return None

    # Sort by weighted F1
    results.sort(key=lambda x: x["weighted_f1"], reverse=True)

    # Print top 10 results
    print("\n" + "=" * 80)
    print("  TOP 10 WEIGHT COMBINATIONS (by Weighted F1)")
    print("=" * 80)
    print(f"{'Rank':<6} {'Gaze':>6} {'Head':>6} {'Eye':>6} {'Face':>6} {'Accuracy':>10} {'Macro-F1':>10} {'Wtd-F1':>10}")
    print("-" * 80)

    for i, r in enumerate(results[:10]):
        w = r["weights"]
        print(
            f"{i+1:<6} {w['gaze']:>6.2f} {w['head_pose']:>6.2f} "
            f"{w['eye_openness']:>6.2f} {w['face_presence']:>6.2f} "
            f"{r['accuracy']:>10.4f} {r['macro_f1']:>10.4f} {r['weighted_f1']:>10.4f}"
        )

    # Show current weights for comparison
    print("-" * 80)
    current_idx = None
    for i, r in enumerate(results):
        w = r["weights"]
        if (abs(w["gaze"] - WEIGHT_GAZE) < 0.01 and
            abs(w["head_pose"] - WEIGHT_HEAD_POSE) < 0.01 and
            abs(w["eye_openness"] - WEIGHT_EYE_OPENNESS) < 0.01):
            current_idx = i
            break

    if current_idx is not None:
        print(f"\n  📌 Current weights rank: #{current_idx + 1} out of {len(results)}")
        r = results[current_idx]
        print(f"     Accuracy: {r['accuracy']:.4f} | Macro-F1: {r['macro_f1']:.4f} | Weighted-F1: {r['weighted_f1']:.4f}")
    else:
        print(f"\n  ⚠️  Current weights ({WEIGHT_GAZE}, {WEIGHT_HEAD_POSE}, {WEIGHT_EYE_OPENNESS}, {WEIGHT_FACE_PRESENCE}) not in search space")

    best = results[0]
    print(f"\n  🏆 BEST WEIGHTS:")
    print(f"     Gaze: {best['weights']['gaze']}")
    print(f"     Head Pose: {best['weights']['head_pose']}")
    print(f"     Eye Openness: {best['weights']['eye_openness']}")
    print(f"     Face Presence: {best['weights']['face_presence']}")
    print(f"     Weighted F1: {best['weighted_f1']:.4f}")

    return results


# ============================================================
# Save Results
# ============================================================

def save_results(evaluation_metrics, grid_results=None):
    """Save evaluation results to a JSON file."""
    ensure_data_dir()
    output_path = os.path.join(DATA_DIR, "evaluation_results.json")

    results = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "current_weights": {
            "gaze": WEIGHT_GAZE,
            "head_pose": WEIGHT_HEAD_POSE,
            "eye_openness": WEIGHT_EYE_OPENNESS,
            "face_presence": WEIGHT_FACE_PRESENCE,
        },
        "current_thresholds": {
            "focused": THRESHOLD_FOCUSED,
            "partial": THRESHOLD_PARTIAL,
        },
    }

    if evaluation_metrics:
        results["evaluation"] = {
            "accuracy": evaluation_metrics["accuracy"],
            "macro_f1": evaluation_metrics["macro_f1"],
            "weighted_f1": evaluation_metrics["weighted_f1"],
            "total_frames": evaluation_metrics.get("total_frames", 0),
            "avg_score": evaluation_metrics.get("avg_score", 0),
            "per_class": evaluation_metrics["per_class"],
        }

    if grid_results:
        results["grid_search"] = {
            "total_tested": len(grid_results),
            "best_weights": grid_results[0]["weights"],
            "best_weighted_f1": grid_results[0]["weighted_f1"],
            "top_10": [
                {
                    "weights": r["weights"],
                    "accuracy": r["accuracy"],
                    "macro_f1": r["macro_f1"],
                    "weighted_f1": r["weighted_f1"],
                }
                for r in grid_results[:10]
            ],
        }

    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    print(f"\n📄 Results saved to: {output_path}")


# ============================================================
# Main CLI
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="Gaze Attention Algorithm Validation & Evaluation",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Record a guided test session
  python evaluate_algorithm.py --guided-record --duration 90

  # Evaluate against labeled data
  python evaluate_algorithm.py --evaluate data/test_recording.avi data/test_recording_labels.csv

  # Grid search for best weights
  python evaluate_algorithm.py --grid-search data/test_recording.avi data/test_recording_labels.csv

  # Full pipeline (record + evaluate + grid search)
  python evaluate_algorithm.py --full-pipeline --duration 90
        """
    )

    parser.add_argument(
        "--guided-record", action="store_true",
        help="Start guided recording session with auto-generated labels"
    )
    parser.add_argument(
        "--evaluate", nargs=2, metavar=("VIDEO", "LABELS"),
        help="Evaluate algorithm against labeled data (video_path labels_csv)"
    )
    parser.add_argument(
        "--grid-search", nargs=2, metavar=("VIDEO", "LABELS"),
        help="Grid search for optimal weights (video_path labels_csv)"
    )
    parser.add_argument(
        "--full-pipeline", action="store_true",
        help="Run full pipeline: guided record → evaluate → grid search"
    )
    parser.add_argument(
        "--duration", type=int, default=90,
        help="Duration for guided recording in seconds (default: 90)"
    )
    parser.add_argument(
        "--output-prefix", type=str, default="test_recording",
        help="Output filename prefix for guided recording (default: test_recording)"
    )

    args = parser.parse_args()

    if not any([args.guided_record, args.evaluate, args.grid_search, args.full_pipeline]):
        parser.print_help()
        return

    video_path = None
    labels_path = None
    eval_metrics = None
    grid_results = None

    # Mode 1: Guided Recording
    if args.guided_record or args.full_pipeline:
        print("\n🎬 Starting Guided Recording...")
        video_path, labels_path = guided_record(
            duration_total=args.duration,
            output_prefix=args.output_prefix,
        )
        if not video_path:
            print("❌ Recording failed. Exiting.")
            return

    # Mode 2: Evaluation
    if args.evaluate:
        video_path, labels_path = args.evaluate
    if args.full_pipeline or args.evaluate:
        if video_path and labels_path:
            print("\n📊 Running Evaluation...")
            eval_metrics = evaluate(video_path, labels_path)
            if eval_metrics:
                print_classification_report(eval_metrics)

    # Mode 3: Grid Search
    if args.grid_search:
        video_path, labels_path = args.grid_search
    if args.full_pipeline or args.grid_search:
        if video_path and labels_path:
            print("\n🔍 Running Grid Search...")
            grid_results = grid_search(video_path, labels_path)

    # Save results
    if eval_metrics or grid_results:
        save_results(eval_metrics, grid_results)

    print("\n✅ Done!")


if __name__ == "__main__":
    main()
