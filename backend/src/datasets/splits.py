"""Leakage-safe dataset split assignment for reviewed OCSR samples."""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from src.ml.admet_baseline import smiles_to_scaffold


class _UnionFind:
    def __init__(self, values: Iterable[str]) -> None:
        self.parent = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parent.setdefault(value, value)
        if parent != value:
            self.parent[value] = self.find(parent)
        return self.parent[value]

    def union(self, first: str, second: str) -> None:
        first_root, second_root = self.find(first), self.find(second)
        if first_root != second_root:
            self.parent[second_root] = first_root


def scaffold_for_smiles(smiles: str | None) -> str:
    if not smiles:
        return "negative"
    try:
        return smiles_to_scaffold(smiles) or "acyclic"
    except ValueError:
        return "invalid"


def assign_grouped_splits(
    rows: Iterable[dict[str, Any]],
    *,
    ratios: tuple[float, float, float] = (0.8, 0.1, 0.1),
) -> list[dict[str, Any]]:
    """Assign deterministic splits while keeping linked sources/identities/scaffolds together."""
    items = [dict(row) for row in rows]
    if len(ratios) != 3 or any(value < 0 for value in ratios) or sum(ratios) <= 0:
        raise ValueError("ratios must be three non-negative values with a positive sum.")
    keys = [f"row:{index}" for index in range(len(items))]
    groups = _UnionFind(keys)
    seen: dict[tuple[str, str], str] = {}
    for index, row in enumerate(items):
        row_key = keys[index]
        identity = str(row.get("ground_truth_inchikey") or row.get("inchikey") or row.get("canonical_smiles") or "").strip()
        source = str(row.get("source_document") or row.get("source_id") or "").strip()
        scaffold = str(row.get("scaffold_key") or scaffold_for_smiles(row.get("ground_truth_smiles") or row.get("canonical_smiles"))).strip()
        row["scaffold_key"] = scaffold
        for kind, value in (("source", source), ("identity", identity), ("scaffold", scaffold)):
            if not value or value in {"negative", "invalid"}:
                continue
            lookup = (kind, value)
            if lookup in seen:
                groups.union(row_key, seen[lookup])
            else:
                seen[lookup] = row_key

    members: dict[str, list[int]] = defaultdict(list)
    for index, row_key in enumerate(keys):
        members[groups.find(row_key)].append(index)
    ordered = sorted(members.values(), key=lambda indices: (-len(indices), tuple(str(items[index].get("sample_id") or "") for index in indices)))
    labels = ("train", "validation", "test")
    target = [len(items) * ratio / sum(ratios) for ratio in ratios]
    counts = [0, 0, 0]
    for component in ordered:
        bucket = min(range(3), key=lambda idx: (counts[idx] / max(target[idx], 1), counts[idx], idx))
        for index in component:
            items[index]["split"] = labels[bucket]
        counts[bucket] += len(component)
    return items


def validate_split_isolation(rows: Iterable[dict[str, Any]]) -> list[str]:
    """Return concrete leakage errors rather than silently accepting a bad split."""
    errors: list[str] = []
    for key_name in ("source_document", "ground_truth_inchikey", "inchikey", "canonical_smiles", "scaffold_key"):
        split_by_value: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            value = str(row.get(key_name) or "").strip()
            if value and value not in {"negative", "invalid"}:
                split_by_value[value].add(str(row.get("split") or ""))
        errors.extend(
            f"{key_name} '{value}' appears in multiple splits: {', '.join(sorted(splits))}"
            for value, splits in split_by_value.items()
            if len(splits) > 1
        )
    return errors
