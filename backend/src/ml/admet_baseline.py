"""Optional Morgan-fingerprint Random Forest baseline for one ADMET endpoint."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, Sequence

import joblib
import numpy as np
from pandas import isna
from rdkit import DataStructs
from rdkit.Chem import rdFingerprintGenerator
from rdkit.Chem.Scaffolds import MurckoScaffold
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    f1_score,
    matthews_corrcoef,
    mean_absolute_error,
    mean_squared_error,
    precision_recall_fscore_support,
    r2_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split

import config
from src.chem.smiles_validator import smiles_to_mol


TaskType = Literal["classification", "regression"]
SplitStrategy = Literal["scaffold", "random"]
DISCLAIMER = "该预测仅为教学 baseline，不能替代实验、毒理研究或专业决策。"


def smiles_to_fingerprint(smiles: str, radius: int = 2, n_bits: int = 2048) -> np.ndarray:
    """Convert valid SMILES to a fixed-length Morgan bit vector."""
    molecule = smiles_to_mol(smiles)
    if molecule is None:
        raise ValueError("无法生成指纹：输入的 SMILES 无效。")
    generator = rdFingerprintGenerator.GetMorganGenerator(radius=radius, fpSize=n_bits)
    fingerprint = generator.GetFingerprint(molecule)
    array = np.zeros((n_bits,), dtype=np.uint8)
    DataStructs.ConvertToNumpyArray(fingerprint, array)
    return array


def smiles_to_scaffold(smiles: str) -> str:
    """Return a Bemis-Murcko scaffold SMILES, with acyclic molecules grouped together."""
    molecule = smiles_to_mol(smiles)
    if molecule is None:
        raise ValueError("无法生成 scaffold：输入的 SMILES 无效。")
    return MurckoScaffold.MurckoScaffoldSmiles(mol=molecule, includeChirality=False)


def _clean_training_rows(
    smiles_values: Sequence[str] | Iterable[str],
    labels: Sequence[Any] | Iterable[Any],
    radius: int,
    n_bits: int,
) -> tuple[list[str], np.ndarray, np.ndarray, list[str]]:
    smiles_list = list(smiles_values)
    label_list = list(labels)
    if len(smiles_list) != len(label_list):
        raise ValueError("SMILES 与标签数量必须一致。")
    clean_smiles: list[str] = []
    fingerprints: list[np.ndarray] = []
    clean_labels: list[Any] = []
    scaffolds: list[str] = []
    for smiles, label in zip(smiles_list, label_list):
        if label is None or bool(isna(label)):
            continue
        try:
            text = str(smiles)
            fingerprints.append(smiles_to_fingerprint(text, radius=radius, n_bits=n_bits))
            scaffolds.append(smiles_to_scaffold(text))
            clean_smiles.append(text)
            clean_labels.append(label)
        except ValueError:
            continue
    if not fingerprints:
        return [], np.empty((0, n_bits), dtype=np.uint8), np.asarray([]), []
    return clean_smiles, np.vstack(fingerprints), np.asarray(clean_labels), scaffolds


def _scaffold_split_indices(
    scaffolds: Sequence[str],
    labels: np.ndarray,
    task_type: TaskType,
    test_size: float,
    random_state: int,
) -> tuple[np.ndarray, np.ndarray, str]:
    groups: dict[str, list[int]] = {}
    for index, scaffold in enumerate(scaffolds):
        groups.setdefault(scaffold, []).append(index)
    ordered_groups = sorted(groups.values(), key=lambda item: (-len(item), item[0]))
    target = max(1, int(round(len(scaffolds) * test_size)))
    test: list[int] = []
    train: list[int] = []
    for group in ordered_groups:
        if len(test) < target:
            test.extend(group)
        else:
            train.extend(group)
    if not train or not test:
        return _random_split_indices(labels, task_type, test_size, random_state, reason="random_fallback_single_scaffold")
    train_idx = np.asarray(sorted(train), dtype=int)
    test_idx = np.asarray(sorted(test), dtype=int)
    if task_type == "classification" and (
        np.unique(labels[train_idx]).size < 2 or np.unique(labels[test_idx]).size < 2
    ):
        return _random_split_indices(labels, task_type, test_size, random_state, reason="random_fallback_class_coverage")
    return train_idx, test_idx, "scaffold"


def _random_split_indices(
    labels: np.ndarray,
    task_type: TaskType,
    test_size: float,
    random_state: int,
    reason: str = "random",
) -> tuple[np.ndarray, np.ndarray, str]:
    indices = np.arange(labels.shape[0])
    stratify = labels if task_type == "classification" and np.min(np.unique(labels, return_counts=True)[1]) >= 2 else None
    train_idx, test_idx = train_test_split(
        indices,
        test_size=test_size,
        random_state=random_state,
        stratify=stratify,
    )
    return np.asarray(sorted(train_idx), dtype=int), np.asarray(sorted(test_idx), dtype=int), reason


def _nearest_tanimoto(training_fingerprints: np.ndarray, fingerprint: np.ndarray) -> float | None:
    if training_fingerprints.size == 0:
        return None
    train = training_fingerprints.astype(bool)
    query = fingerprint.astype(bool)
    intersection = np.logical_and(train, query).sum(axis=1).astype(float)
    union = np.logical_or(train, query).sum(axis=1).astype(float)
    similarity = np.divide(intersection, union, out=np.ones_like(intersection), where=union > 0)
    return round(float(np.max(similarity)), 4)


def _applicability_domain(training_fingerprints: np.ndarray, validation_fingerprints: np.ndarray) -> dict[str, Any]:
    if validation_fingerprints.size == 0:
        return {
            "method": "nearest_training_tanimoto",
            "threshold": None,
            "message": "缺少验证集，未建立适用域阈值。",
        }
    nearest = [
        value for value in (_nearest_tanimoto(training_fingerprints, fingerprint) for fingerprint in validation_fingerprints)
        if value is not None
    ]
    threshold = round(float(np.percentile(nearest, 5)), 4) if nearest else None
    return {
        "method": "nearest_training_tanimoto",
        "threshold": threshold,
        "validation_min": min(nearest) if nearest else None,
        "validation_mean": round(float(np.mean(nearest)), 4) if nearest else None,
        "message": "低于阈值的分子应视为超出训练集适用域。",
    }


def _classification_metrics(estimator: Any, x_test: np.ndarray, y_test: np.ndarray) -> dict[str, Any]:
    predictions = estimator.predict(x_test)
    precision, recall, f1, _ = precision_recall_fscore_support(y_test, predictions, average="macro", zero_division=0)
    metrics: dict[str, Any] = {
        "accuracy": round(float(accuracy_score(y_test, predictions)), 4),
        "precision_macro": round(float(precision), 4),
        "recall_macro": round(float(recall), 4),
        "f1_macro": round(float(f1), 4),
        "mcc": round(float(matthews_corrcoef(y_test, predictions)), 4),
        "class_counts": {str(label): int(count) for label, count in zip(*np.unique(y_test, return_counts=True))},
    }
    if hasattr(estimator, "predict_proba") and np.unique(y_test).size == 2:
        probabilities = estimator.predict_proba(x_test)[:, 1]
        try:
            metrics["roc_auc"] = round(float(roc_auc_score(y_test, probabilities)), 4)
        except ValueError:
            metrics["roc_auc"] = None
        try:
            metrics["pr_auc"] = round(float(average_precision_score(y_test, probabilities)), 4)
        except ValueError:
            metrics["pr_auc"] = None
    return metrics


def _regression_metrics(estimator: Any, x_test: np.ndarray, y_test: np.ndarray) -> dict[str, Any]:
    predictions = estimator.predict(x_test)
    return {
        "mae": round(float(mean_absolute_error(y_test, predictions)), 4),
        "rmse": round(float(mean_squared_error(y_test, predictions, squared=False)), 4),
        "r2": round(float(r2_score(y_test, predictions)), 4),
    }


def _quality_gate(task_type: TaskType, metrics: dict[str, Any], validation_samples: int) -> dict[str, Any]:
    if validation_samples < 4:
        return {"passed": False, "reason": "验证集样本少于 4 条，禁止标记为可用预测。"}
    if task_type == "classification":
        roc_auc = metrics.get("roc_auc")
        f1 = float(metrics.get("f1_macro") or 0.0)
        if roc_auc is not None:
            passed = float(roc_auc) >= 0.6 and f1 >= 0.5
            reason = "分类验证指标达到最低展示门槛。" if passed else "分类 ROC-AUC/F1 未达到最低展示门槛。"
            return {"passed": passed, "reason": reason, "minimum": {"roc_auc": 0.6, "f1_macro": 0.5}}
        passed = f1 >= 0.5
        reason = "分类 F1 达到最低展示门槛。" if passed else "分类 F1 未达到最低展示门槛。"
        return {"passed": passed, "reason": reason, "minimum": {"f1_macro": 0.5}}
    r2 = float(metrics.get("r2") if metrics.get("r2") is not None else -999.0)
    passed = r2 >= 0.0
    reason = "回归验证 R2 达到最低展示门槛。" if passed else "回归 R2 未达到最低展示门槛。"
    return {"passed": passed, "reason": reason, "minimum": {"r2": 0.0}}


@dataclass
class ADMETBaseline:
    """Serializable single-endpoint Random Forest model with validation metadata."""

    estimator: Any
    target_name: str
    task_type: TaskType
    radius: int = 2
    n_bits: int = 2048
    training_samples: int = 0
    validation_samples: int = 0
    total_clean_samples: int = 0
    split_strategy: str = "unknown"
    metrics: dict[str, Any] = field(default_factory=dict)
    quality_gate: dict[str, Any] = field(default_factory=dict)
    applicability_domain: dict[str, Any] = field(default_factory=dict)
    training_fingerprints: np.ndarray | None = None

    @classmethod
    def train(
        cls,
        smiles_values: Sequence[str] | Iterable[str],
        labels: Sequence[Any] | Iterable[Any],
        target_name: str,
        task_type: TaskType = "classification",
        random_state: int = 42,
        split_strategy: SplitStrategy = "scaffold",
        test_size: float = 0.2,
        min_samples: int = 20,
    ) -> "ADMETBaseline":
        """Train a baseline after filtering rows with invalid SMILES or missing labels."""
        if task_type not in {"classification", "regression"}:
            raise ValueError("task_type 必须是 classification 或 regression。")
        if split_strategy not in {"scaffold", "random"}:
            raise ValueError("split_strategy 必须是 scaffold 或 random。")
        if not target_name.strip():
            raise ValueError("target_name 不能为空。")
        clean_smiles, features, targets, scaffolds = _clean_training_rows(smiles_values, labels, radius=2, n_bits=2048)
        if len(clean_smiles) < min_samples:
            raise ValueError(f"至少需要 {min_samples} 条具有有效 SMILES 和标签的数据才能训练 baseline。")
        if task_type == "classification":
            unique_labels, class_counts = np.unique(targets, return_counts=True)
            if unique_labels.size < 2:
                raise ValueError("分类任务至少需要两个不同类别。")
            if int(np.min(class_counts)) < 2:
                raise ValueError("分类任务每个类别至少需要 2 条有效样本。")
        else:
            targets = targets.astype(float)

        bounded_test_size = min(max(float(test_size), 0.1), 0.5)
        if split_strategy == "scaffold":
            train_idx, test_idx, actual_split = _scaffold_split_indices(
                scaffolds,
                targets,
                task_type,
                bounded_test_size,
                random_state,
            )
        else:
            train_idx, test_idx, actual_split = _random_split_indices(targets, task_type, bounded_test_size, random_state)

        if task_type == "classification":
            estimator = RandomForestClassifier(
                n_estimators=300,
                class_weight="balanced",
                random_state=random_state,
                n_jobs=-1,
            )
        else:
            estimator = RandomForestRegressor(
                n_estimators=300,
                random_state=random_state,
                n_jobs=-1,
            )
        estimator.fit(features[train_idx], targets[train_idx])
        metrics = (
            _classification_metrics(estimator, features[test_idx], targets[test_idx])
            if task_type == "classification"
            else _regression_metrics(estimator, features[test_idx], targets[test_idx])
        )
        domain = _applicability_domain(features[train_idx], features[test_idx])
        gate = _quality_gate(task_type, metrics, validation_samples=len(test_idx))
        return cls(
            estimator=estimator,
            target_name=target_name.strip(),
            task_type=task_type,
            training_samples=len(train_idx),
            validation_samples=len(test_idx),
            total_clean_samples=len(clean_smiles),
            split_strategy=actual_split,
            metrics=metrics,
            quality_gate=gate,
            applicability_domain=domain,
            training_fingerprints=features[train_idx],
        )

    def _domain_report(self, fingerprint: np.ndarray) -> dict[str, Any]:
        nearest = _nearest_tanimoto(self.training_fingerprints, fingerprint) if self.training_fingerprints is not None else None
        threshold = self.applicability_domain.get("threshold")
        inside = threshold is None or nearest is None or nearest >= float(threshold)
        return {
            "method": self.applicability_domain.get("method", "nearest_training_tanimoto"),
            "nearest_training_tanimoto": nearest,
            "threshold": threshold,
            "inside": inside,
        }

    def predict(self, smiles: str) -> dict[str, Any]:
        """Predict one endpoint and return a JSON-friendly result."""
        fingerprint = smiles_to_fingerprint(smiles, radius=self.radius, n_bits=self.n_bits)
        features = fingerprint.reshape(1, -1)
        prediction = self.estimator.predict(features)[0]
        probability: float | None = None
        if self.task_type == "classification" and hasattr(self.estimator, "predict_proba"):
            probabilities = self.estimator.predict_proba(features)[0]
            probability = round(float(np.max(probabilities)), 4)
        if hasattr(prediction, "item"):
            prediction = prediction.item()
        domain = self._domain_report(fingerprint)
        gate_passed = bool(self.quality_gate.get("passed"))
        status = "success"
        message = "ADMET baseline 预测完成。"
        if not gate_passed:
            status = "unqualified"
            message = f"ADMET baseline 未通过验证门槛：{self.quality_gate.get('reason') or '原因未知'}"
        elif not domain["inside"]:
            status = "outside_domain"
            message = "输入分子低于训练集适用域相似度阈值，预测不应作为可用结果展示。"
        return {
            "status": status,
            "target": self.target_name,
            "task_type": self.task_type,
            "prediction": prediction,
            "probability": probability,
            "model": type(self.estimator).__name__,
            "training_samples": self.training_samples,
            "validation_samples": self.validation_samples,
            "total_clean_samples": self.total_clean_samples,
            "split_strategy": self.split_strategy,
            "metrics": self.metrics,
            "quality_gate": self.quality_gate,
            "applicability_domain": domain,
            "message": message,
            "disclaimer": DISCLAIMER,
        }

    def save(self, output_path: str | Path) -> str:
        """Save the model artifact and return its absolute path."""
        destination = Path(output_path).expanduser().resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        artifact = {
            "format_version": 2,
            "estimator": self.estimator,
            "target_name": self.target_name,
            "task_type": self.task_type,
            "radius": self.radius,
            "n_bits": self.n_bits,
            "training_samples": self.training_samples,
            "validation_samples": self.validation_samples,
            "total_clean_samples": self.total_clean_samples,
            "split_strategy": self.split_strategy,
            "metrics": self.metrics,
            "quality_gate": self.quality_gate,
            "applicability_domain": self.applicability_domain,
            "training_fingerprints": self.training_fingerprints,
        }
        joblib.dump(artifact, destination)
        return str(destination)

    @classmethod
    def load(cls, model_path: str | Path) -> "ADMETBaseline":
        """Load a trusted model artifact created by :meth:`save`."""
        source = Path(model_path).expanduser().resolve()
        if not source.is_file():
            raise FileNotFoundError(f"ADMET 模型文件不存在：{source}")
        artifact = joblib.load(source)
        required = {"estimator", "target_name", "task_type", "radius", "n_bits"}
        if not isinstance(artifact, dict) or not required <= artifact.keys():
            raise ValueError("ADMET 模型文件格式无效或版本不受支持。")
        legacy_quality = {
            "passed": False,
            "reason": "旧版 ADMET artifact 缺少验证指标，请重新训练后再展示预测。",
        }
        return cls(
            estimator=artifact["estimator"],
            target_name=str(artifact["target_name"]),
            task_type=artifact["task_type"],
            radius=int(artifact["radius"]),
            n_bits=int(artifact["n_bits"]),
            training_samples=int(artifact.get("training_samples", 0)),
            validation_samples=int(artifact.get("validation_samples", 0)),
            total_clean_samples=int(artifact.get("total_clean_samples", artifact.get("training_samples", 0))),
            split_strategy=str(artifact.get("split_strategy", "unknown")),
            metrics=dict(artifact.get("metrics") or {}),
            quality_gate=dict(artifact.get("quality_gate") or legacy_quality),
            applicability_domain=dict(artifact.get("applicability_domain") or {}),
            training_fingerprints=artifact.get("training_fingerprints"),
        )


class ConfiguredADMETPredictor:
    """Lazy, failure-isolated access to the configured optional baseline."""

    def __init__(
        self,
        enabled: bool = config.ENABLE_ADMET_MODEL,
        model_path: str | Path = config.ADMET_MODEL_PATH,
    ) -> None:
        self.enabled = enabled
        self.model_path = Path(model_path).expanduser()
        self._model: ADMETBaseline | None = None
        self._load_error: str | None = None

    def _load(self) -> ADMETBaseline | None:
        if self._model is None and self._load_error is None:
            try:
                self._model = ADMETBaseline.load(self.model_path)
            except Exception as exc:
                self._load_error = str(exc)
        return self._model

    def predict(self, smiles: str) -> dict[str, Any]:
        """Return disabled/unavailable/success without breaking the chemistry workflow."""
        if not self.enabled:
            return {
                "status": "disabled",
                "message": "未启用可选 ADMET baseline，当前仅执行 RDKit 规则分析。",
                "disclaimer": DISCLAIMER,
            }
        model = self._load()
        if model is None:
            return {
                "status": "unavailable",
                "message": self._load_error or "ADMET 模型不可用。",
                "model_path": str(self.model_path),
                "disclaimer": DISCLAIMER,
            }
        try:
            return model.predict(smiles)
        except Exception as exc:
            return {
                "status": "failed",
                "message": f"ADMET baseline 预测失败：{exc}",
                "disclaimer": DISCLAIMER,
            }
