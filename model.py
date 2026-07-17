"""Machine-learning and rule-based congestion decisions."""

from __future__ import annotations

from typing import Any

from sklearn.base import clone
from sklearn.calibration import CalibratedClassifierCV
from sklearn.metrics import accuracy_score, precision_recall_fscore_support
from sklearn.model_selection import cross_val_score, train_test_split
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler
from sklearn.svm import SVC


FEATURE_NAMES = (
    "distance_km",
    "rho_current",
    "rho_previous",
    "alpha",
    "estimated_drho",
)
CONGESTION_THRESHOLD = 70.0


class CongestionClassifier:
    """Fit a small RBF-SVM and expose a transparent baseline for comparison."""

    def __init__(self, training_rows: list[dict[str, Any]]) -> None:
        self.training_rows = training_rows
        self.pipeline = make_pipeline(
            StandardScaler(),
            # CalibratedClassifierCV keeps ``predict_proba`` for the Dashboard
            # confidence value without relying on SVC's deprecated probability
            # flag in modern scikit-learn releases.
            CalibratedClassifierCV(
                estimator=SVC(kernel="rbf", random_state=42),
                cv=3,
                ensemble=False,
            ),
        )
        self._fit(training_rows)
        self.metrics = self._calculate_metrics(training_rows)

    @staticmethod
    def _features(row: dict[str, Any]) -> list[float]:
        """Extract features in one fixed order for both training and inference."""

        return [float(row[name]) for name in FEATURE_NAMES]

    def _fit(self, rows: list[dict[str, Any]]) -> None:
        """Fit the production demo model on the complete synthetic data set."""

        features = [self._features(row) for row in rows]
        labels = [1 if row["status"] == "congested" else 0 for row in rows]
        self.pipeline.fit(features, labels)

    def _calculate_metrics(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        """Calculate compact metrics shown by ``/health`` and the Dashboard."""

        features = [self._features(row) for row in rows]
        labels = [1 if row["status"] == "congested" else 0 for row in rows]
        train_x, test_x, train_y, test_y = train_test_split(
            features,
            labels,
            test_size=8,
            random_state=42,
            stratify=labels,
        )

        test_model = clone(self.pipeline)
        test_model.fit(train_x, train_y)
        predictions = test_model.predict(test_x)
        precision, recall, f1, _ = precision_recall_fscore_support(
            test_y,
            predictions,
            average="binary",
            zero_division=0,
        )
        cv_scores = cross_val_score(self.pipeline, features, labels, cv=5)

        return {
            "training_records": len(rows),
            "test_records": len(test_y),
            "safe_records": labels.count(0),
            "congested_records": labels.count(1),
            "test_accuracy": round(float(accuracy_score(test_y, predictions)), 3),
            "precision": round(float(precision), 3),
            "recall": round(float(recall), 3),
            "f1": round(float(f1), 3),
            "five_fold_cv_accuracy": round(float(cv_scores.mean()), 3),
        }

    @staticmethod
    def rule_status(reading: dict[str, Any]) -> str:
        """Return the interpretable baseline decision used in the presentation."""

        return (
            "congested"
            if float(reading["estimated_drho"]) > CONGESTION_THRESHOLD
            else "safe"
        )

    def evaluate(self, reading: dict[str, Any]) -> dict[str, Any]:
        """Return SVM, rule, confidence, and operator-facing recommendation."""

        features = [self._features(reading)]
        prediction = int(self.pipeline.predict(features)[0])
        probabilities = self.pipeline.predict_proba(features)[0]
        svm_status = "congested" if prediction else "safe"
        rule_status = self.rule_status(reading)
        status = svm_status

        return {
            "status": status,
            "svm_status": svm_status,
            "rule_status": rule_status,
            "models_agree": svm_status == rule_status,
            "confidence": round(float(max(probabilities)), 3),
            "threshold": CONGESTION_THRESHOLD,
            "recommended_action": (
                "Dynamic message signs + reroute"
                if status == "congested"
                else "Continue standard monitoring"
            ),
        }
