"""Lipinski and extended rule-based drug-likeness assessment."""

from __future__ import annotations

from typing import Any, Mapping


def evaluate_lipinski(descriptors: Mapping[str, Any]) -> dict[str, Any]:
    """Evaluate Lipinski thresholds plus an extended rotatable-bond rule."""
    definitions = (
        ("分子量", float(descriptors["molecular_weight"]), 500.0, "MW > 500"),
        ("LogP", float(descriptors["logp"]), 5.0, "LogP > 5"),
        ("HBD", int(descriptors["hbd"]), 5, "HBD > 5"),
        ("HBA", int(descriptors["hba"]), 10, "HBA > 10"),
        ("可旋转键", int(descriptors["rotatable_bonds"]), 10, "Rotatable Bonds > 10"),
    )
    checks = [
        {
            "name": name,
            "value": value,
            "limit": limit,
            "passed": bool(value <= limit),
            "message": f"{name} {value:g} ≤ {limit:g}" if value <= limit else f"{name} {value:g} > {limit:g}",
        }
        for name, value, limit, _label in definitions
    ]
    violations = [definition[3] for check, definition in zip(checks, definitions) if not check["passed"]]
    passed = not violations
    if passed:
        summary = "该分子基本符合 Lipinski 类药性规则及扩展可旋转键规则。"
    else:
        summary = f"规则超限项：{', '.join(violations)}。"
    return {"passed": passed, "violations": violations, "checks": checks, "summary": summary}


def analyze_lipinski(smiles: str) -> dict[str, Any]:
    """Convenience wrapper that calculates descriptors before rule evaluation."""
    from .descriptors import calculate_descriptors

    return evaluate_lipinski(calculate_descriptors(smiles))
