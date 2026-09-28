"""Train a logistic regression from scratch and record the whole thing.

Run it, then open the dashboard:

    python examples/quickstart.py
    mlexp ui

Standard library only, like the tracker itself. It logs per-epoch metrics, a confusion
matrix, ROC curve and feature importances, checkpoints, captured prints as logs, and
provenance (check it with `mlexp verify <run_id>`). Same seed, same results.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
from pathlib import Path

import mlexperimenttracker as met

# Synthetic data model. `noise_a` and `noise_b` carry no signal, so they should rank last
# in feature importance.
FEATURES: tuple[str, ...] = (
    "tenure_months",
    "monthly_charges",
    "support_tickets",
    "contract_length",
    "noise_a",
    "noise_b",
)
TRUE_WEIGHTS: tuple[float, ...] = (1.8, -1.2, 0.9, -0.5, 0.0, 0.0)
TRUE_BIAS = -0.2


# --------------------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------------------


def make_dataset(rng: random.Random, count: int) -> tuple[list[list[float]], list[int]]:
    """Draw `count` samples from a logistic model with two uninformative columns."""
    rows: list[list[float]] = []
    labels: list[int] = []
    for _ in range(count):
        features = [rng.gauss(0.0, 1.0) for _ in FEATURES]
        logit = TRUE_BIAS + sum(w * x for w, x in zip(TRUE_WEIGHTS, features, strict=True))
        # Sample the label instead of thresholding, so the data isn't perfectly separable.
        labels.append(1 if rng.random() < sigmoid(logit) else 0)
        rows.append(features)
    return rows, labels


def write_csv(path: Path, rows: list[list[float]], labels: list[int]) -> None:
    """Write the dataset so provenance has a real file to hash."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([*FEATURES, "label"])
        for features, label in zip(rows, labels, strict=True):
            writer.writerow([f"{value:.6f}" for value in features] + [label])


# --------------------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------------------


def sigmoid(z: float) -> float:
    """Logistic function that doesn't overflow for large negative z."""
    if z >= 0.0:
        return 1.0 / (1.0 + math.exp(-z))
    exp_z = math.exp(z)
    return exp_z / (1.0 + exp_z)


def predict(weights: list[float], bias: float, features: list[float]) -> float:
    return sigmoid(bias + sum(w * x for w, x in zip(weights, features, strict=True)))


def evaluate(
    weights: list[float], bias: float, rows: list[list[float]], labels: list[int]
) -> tuple[float, float]:
    """Return (mean log loss, accuracy) over a split."""
    total_loss = 0.0
    correct = 0
    for features, label in zip(rows, labels, strict=True):
        p = predict(weights, bias, features)
        # Clamp to avoid log(0); non-finite metrics are dropped when logged.
        p = min(max(p, 1e-12), 1.0 - 1e-12)
        total_loss += -(label * math.log(p) + (1 - label) * math.log(1.0 - p))
        correct += int((p >= 0.5) == bool(label))
    count = len(rows)
    return total_loss / count, correct / count


def gradient(
    weights: list[float],
    bias: float,
    rows: list[list[float]],
    labels: list[int],
    l2: float,
) -> tuple[list[float], float]:
    """Full-batch gradient of the L2-regularised log loss."""
    grad_w = [0.0] * len(weights)
    grad_b = 0.0
    for features, label in zip(rows, labels, strict=True):
        error = predict(weights, bias, features) - label
        for index, value in enumerate(features):
            grad_w[index] += error * value
        grad_b += error
    count = len(rows)
    grad_w = [g / count + l2 * w for g, w in zip(grad_w, weights, strict=True)]
    return grad_w, grad_b / count


# --------------------------------------------------------------------------------------
# Evaluation on the held-out split
# --------------------------------------------------------------------------------------


def confusion(
    weights: list[float], bias: float, rows: list[list[float]], labels: list[int]
) -> list[list[int]]:
    """Rows are the true class, columns the predicted class, in `labels` order."""
    matrix = [[0, 0], [0, 0]]
    for features, label in zip(rows, labels, strict=True):
        predicted = int(predict(weights, bias, features) >= 0.5)
        matrix[label][predicted] += 1
    return matrix


def prf(matrix: list[list[int]]) -> tuple[list[float], list[float], list[float]]:
    """Per-class precision, recall and F1 from a 2x2 confusion matrix."""
    precision: list[float] = []
    recall: list[float] = []
    f1: list[float] = []
    for cls in (0, 1):
        true_positive = matrix[cls][cls]
        predicted = sum(matrix[row][cls] for row in (0, 1))
        actual = sum(matrix[cls])
        p = true_positive / predicted if predicted else 0.0
        r = true_positive / actual if actual else 0.0
        precision.append(p)
        recall.append(r)
        f1.append(2 * p * r / (p + r) if (p + r) else 0.0)
    return precision, recall, f1


def roc(
    weights: list[float], bias: float, rows: list[list[float]], labels: list[int]
) -> tuple[list[float], list[float], list[float], float]:
    """Sweep every distinct score as a threshold; return fpr, tpr, thresholds and AUC."""
    scored = sorted(
        (
            (predict(weights, bias, features), label)
            for features, label in zip(rows, labels, strict=True)
        ),
        key=lambda pair: pair[0],
        reverse=True,
    )
    positives = sum(1 for _, label in scored if label == 1)
    negatives = len(scored) - positives
    if positives == 0 or negatives == 0:
        return [0.0, 1.0], [0.0, 1.0], [1.0, 0.0], 0.5

    fpr = [0.0]
    tpr = [0.0]
    thresholds = [1.0]
    true_positive = 0
    false_positive = 0
    for index, (score, label) in enumerate(scored):
        if label == 1:
            true_positive += 1
        else:
            false_positive += 1
        # Only emit a point where the score changes, so ties collapse to one threshold.
        if index + 1 < len(scored) and scored[index + 1][0] == score:
            continue
        fpr.append(false_positive / negatives)
        tpr.append(true_positive / positives)
        thresholds.append(score)
    fpr.append(1.0)
    tpr.append(1.0)
    thresholds.append(0.0)

    area = 0.0
    for left in range(len(fpr) - 1):
        area += (fpr[left + 1] - fpr[left]) * (tpr[left + 1] + tpr[left]) / 2.0
    return fpr, tpr, thresholds, area


def permutation_importance(
    weights: list[float],
    bias: float,
    rows: list[list[float]],
    labels: list[int],
    rng: random.Random,
    repeats: int,
) -> list[dict[str, float | str]]:
    """Importance = drop in validation accuracy when a feature column is shuffled.

    Repeated `repeats` times to get a mean and std per feature.
    """
    _, base_accuracy = evaluate(weights, bias, rows, labels)

    features: list[dict[str, float | str]] = []
    for index, name in enumerate(FEATURES):
        drops: list[float] = []
        for _ in range(repeats):
            column = [row[index] for row in rows]
            rng.shuffle(column)
            shuffled = [
                [*row[:index], column[position], *row[index + 1 :]]
                for position, row in enumerate(rows)
            ]
            _, accuracy = evaluate(weights, bias, shuffled, labels)
            drops.append(base_accuracy - accuracy)
        mean = sum(drops) / len(drops)
        variance = sum((drop - mean) ** 2 for drop in drops) / len(drops)
        features.append({"name": name, "importance": mean, "std": math.sqrt(variance)})
    return features


# --------------------------------------------------------------------------------------
# Training run
# --------------------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Train a logistic regression from scratch and record the whole thing."
    )
    parser.add_argument("--project", default="quickstart", help="project (experiment) name")
    parser.add_argument("--seed", type=int, default=7, help="seed for data and shuffling")
    parser.add_argument("--epochs", type=int, default=60, help="full-batch gradient steps")
    parser.add_argument("--learning-rate", type=float, default=0.6)
    parser.add_argument("--l2", type=float, default=0.001, help="L2 penalty on the weights")
    parser.add_argument("--train-samples", type=int, default=900)
    parser.add_argument("--val-samples", type=int, default=300)
    parser.add_argument(
        "--storage",
        default=None,
        help="storage root; defaults to $EXPERIMENT_STORAGE_PATH or ~/.experiment_tracker",
    )
    parser.add_argument(
        "--outdir",
        default=str(Path(__file__).resolve().parent / "quickstart_output"),
        help="where the generated dataset and the checkpoint weights are written",
    )
    args = parser.parse_args()

    outdir = Path(args.outdir).resolve()
    dataset_path = outdir / "synthetic.csv"

    rng = random.Random(args.seed)
    train_rows, train_labels = make_dataset(rng, args.train_samples)
    val_rows, val_labels = make_dataset(rng, args.val_samples)
    write_csv(dataset_path, train_rows + val_rows, train_labels + val_labels)

    config = {
        "learning_rate": args.learning_rate,
        "epochs": args.epochs,
        "l2": args.l2,
        "optimizer": "gradient_descent",
        "batch_size": args.train_samples,  # full batch
        "train_samples": args.train_samples,
        "val_samples": args.val_samples,
        "n_features": len(FEATURES),
        "seed": args.seed,
    }

    # `with` marks the run failed (with the traceback) if anything below raises.
    with met.init(
        project=args.project,
        name=f"logreg-lr{args.learning_rate}-seed{args.seed}",
        config=config,
        tags=["example", "logistic-regression"],
        notes="Hand-written logistic regression on synthetic data; standard library only.",
        storage_path=args.storage,
        datasets=[dataset_path],
    ) as run:
        print(f"run {run.id} -> {run.path}")
        print(f"dataset {dataset_path} ({args.train_samples} train / {args.val_samples} val)")
        run.log_text(f"seed={args.seed} features={list(FEATURES)}", level="info")

        weights = [0.0] * len(FEATURES)
        bias = 0.0

        for epoch in range(1, args.epochs + 1):
            grad_w, grad_b = gradient(weights, bias, train_rows, train_labels, args.l2)
            weights = [w - args.learning_rate * g for w, g in zip(weights, grad_w, strict=True)]
            bias -= args.learning_rate * grad_b

            train_loss, train_accuracy = evaluate(weights, bias, train_rows, train_labels)
            val_loss, val_accuracy = evaluate(weights, bias, val_rows, val_labels)
            grad_norm = math.sqrt(sum(g * g for g in grad_w) + grad_b * grad_b)

            run.log(
                {
                    "loss": train_loss,
                    "accuracy": train_accuracy,
                    "val_loss": val_loss,
                    "val_accuracy": val_accuracy,
                    "grad_norm": grad_norm,
                },
                step=epoch,
            )

            if epoch % 10 == 0 or epoch == 1:
                print(
                    f"epoch {epoch:3d}/{args.epochs}  "
                    f"loss {train_loss:.4f}  acc {train_accuracy:.4f}  "
                    f"val_loss {val_loss:.4f}  val_acc {val_accuracy:.4f}"
                )

            if epoch % 20 == 0:
                # Save weights to disk and log a checkpoint pointing at them. The dashboard
                # lists the checkpoint, not the weight file (same as it would for a .pt).
                weights_path = outdir / f"epoch_{epoch:03d}.weights.json"
                weights_path.parent.mkdir(parents=True, exist_ok=True)
                weights_path.write_text(
                    json.dumps({"weights": weights, "bias": bias}), encoding="utf-8"
                )
                run.log_checkpoint(f"epoch_{epoch:03d}", step=epoch, path=weights_path)
                print(f"  checkpoint epoch_{epoch:03d} -> {weights_path.name}")

        print("training finished; evaluating on the held-out split")

        matrix = confusion(weights, bias, val_rows, val_labels)
        precision, recall, f1 = prf(matrix)
        accuracy = (matrix[0][0] + matrix[1][1]) / len(val_rows)
        run.log_confusion_matrix(
            labels=["negative", "positive"],
            matrix=matrix,
            accuracy=accuracy,
            precision=precision,
            recall=recall,
            f1_score=f1,
        )
        print(f"  confusion matrix {matrix}  accuracy {accuracy:.4f}")

        fpr, tpr, thresholds, auc = roc(weights, bias, val_rows, val_labels)
        run.log_roc_curve(fpr=fpr, tpr=tpr, thresholds=thresholds, auc=auc, class_name="positive")
        print(f"  roc auc {auc:.4f} over {len(thresholds)} thresholds")

        features = permutation_importance(
            weights, bias, val_rows, val_labels, random.Random(args.seed + 1), repeats=5
        )
        run.log_feature_importance(features)
        ranked = ", ".join(f"{item['name']}={item['importance']:.4f}" for item in features)
        print(f"  permutation importance {ranked}")

        print(f"done. inspect it with:  mlexp show {run.id}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
