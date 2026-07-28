"""SMILES parsing, validation and canonicalization with RDKit."""

from __future__ import annotations

from contextlib import contextmanager
import logging
import re
from threading import RLock
from typing import Any

from rdkit import Chem, rdBase


_RDKIT_LOG_LOCK = RLock()


@contextmanager
def suppress_rdkit_parse_errors():
    """Temporarily suppress expected RDKit SMILES parse errors."""
    rdBase.DisableLog("rdApp.error")
    try:
        yield
    finally:
        rdBase.EnableLog("rdApp.error")


def smiles_to_mol(smiles: str) -> Chem.Mol | None:
    """Parse a SMILES string into an RDKit molecule, returning None if invalid."""
    if not isinstance(smiles, str) or not smiles.strip():
        return None
    try:
        with suppress_rdkit_parse_errors():
            return Chem.MolFromSmiles(smiles.strip())
    except Exception:
        return None


def canonicalize_smiles(smiles: str) -> str | None:
    """Return isomeric canonical SMILES, or None for an invalid input."""
    molecule = smiles_to_mol(smiles)
    if molecule is None or unsupported_structure_reason(molecule):
        return None
    return Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True)


def unsupported_structure_reason(molecule: Chem.Mol | None) -> str | None:
    """Return a reason for structures this app should not analyze as normal molecules."""
    if molecule is None:
        return None
    dummy_atoms = [atom.GetIdx() for atom in molecule.GetAtoms() if atom.GetAtomicNum() == 0]
    if dummy_atoms:
        return "SMILES 含有通配符或查询原子（*），不能作为确定分子进入性质计算。"
    return None


def validate_smiles(smiles: str | None) -> dict[str, Any]:
    """Validate SMILES and return a stable, JSON-friendly result dictionary."""
    if smiles is None or not isinstance(smiles, str) or not smiles.strip():
        return {"valid": False, "canonical_smiles": None, "error": "SMILES 不能为空。"}
    molecule = smiles_to_mol(smiles)
    unsupported = unsupported_structure_reason(molecule)
    if unsupported:
        return {"valid": False, "canonical_smiles": None, "error": unsupported}
    if molecule is None:
        return {
            "valid": False,
            "canonical_smiles": None,
            "error": "RDKit 无法解析该 SMILES，请检查原子、键和括号。",
        }
    canonical = Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True)
    return {"valid": True, "canonical_smiles": canonical, "error": None}


def diagnose_smiles(smiles: str | None) -> dict[str, Any]:
    """Validate SMILES with a best-effort parse position and repair hints."""
    if smiles is None or not isinstance(smiles, str) or not smiles.strip():
        return {
            "valid": False,
            "canonical_smiles": None,
            "error": "SMILES 不能为空。",
            "error_position": None,
            "error_character": None,
            "rdkit_messages": [],
            "suggestions": ["请输入一条 SMILES；批量输入时每行放一条结构。"],
        }
    raw = smiles
    text = raw.strip()
    molecule, messages = _parse_with_messages(text)
    unsupported = unsupported_structure_reason(molecule)
    if unsupported:
        return {
            "valid": False,
            "canonical_smiles": None,
            "error": unsupported,
            "error_position": text.find("*") + 1 if "*" in text else None,
            "error_character": "*" if "*" in text else None,
            "rdkit_messages": messages,
            "suggestions": ["请将通配符或查询原子替换成确定的元素和键型。"],
        }
    if molecule is not None:
        canonical = Chem.MolToSmiles(molecule, canonical=True, isomericSmiles=True)
        suggestions = []
        if raw != text:
            suggestions.append("已忽略首尾空白字符；导出时建议使用去除空白后的 SMILES。")
        return {
            "valid": True,
            "canonical_smiles": canonical,
            "error": None,
            "error_position": None,
            "error_character": None,
            "rdkit_messages": [],
            "suggestions": suggestions,
        }
    position = _message_position(messages) or _heuristic_error_position(text)
    reason = _localized_parse_reason(messages, text)
    character = text[position - 1] if position and 0 < position <= len(text) else None
    return {
        "valid": False,
        "canonical_smiles": None,
        "error": reason,
        "error_position": position,
        "error_character": character,
        "rdkit_messages": messages,
        "suggestions": common_smiles_repair_hints(text, messages),
    }


def common_smiles_repair_hints(smiles: str, messages: list[str] | None = None) -> list[str]:
    """Return concise format repair guidance without changing chemistry silently."""
    joined = " ".join(messages or []).lower()
    hints: list[str] = []
    if "parenthe" in joined or smiles.count("(") != smiles.count(")"):
        hints.append("检查分支括号是否成对，例如 CC(=O)O。")
    ring_tokens = re.findall(r"%\d{2}|\d", smiles)
    if "ring" in joined or any(ring_tokens.count(token) % 2 for token in set(ring_tokens)):
        hints.append("环编号必须成对出现，例如 c1ccccc1；两位环编号使用 %10 格式。")
    if "valence" in joined:
        hints.append("检查原子价态；带电原子请使用方括号和显式电荷，例如 [NH4+]、[O-]。")
    if "syntax" in joined or "parse" in joined:
        hints.append("检查元素大小写、键符号和方括号；Cl、Br 是双字符元素符号。")
    if any(character.isspace() for character in smiles):
        hints.append("单条 SMILES 中不要包含空格；SMI 文件的空格后内容会被视为名称。")
    if any(character in smiles for character in "（）【】＝－–—"):
        hints.append("将全角括号或排版符号替换为 ASCII 字符：()、[]、=、-。")
    hints.append("不要根据提示自动补原子或键；修复后请再次核对结构图。")
    return list(dict.fromkeys(hints))


def _parse_with_messages(smiles: str) -> tuple[Chem.Mol | None, list[str]]:
    messages: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            message = re.sub(r"^\[[^\]]+\]\s*", "", record.getMessage()).strip()
            if message:
                messages.append(message)

    with _RDKIT_LOG_LOCK:
        logger = logging.getLogger("rdkit")
        previous_handlers = list(logger.handlers)
        previous_propagate = logger.propagate
        previous_level = logger.level
        try:
            logger.handlers = [Capture()]
            logger.propagate = False
            logger.setLevel(logging.ERROR)
            rdBase.LogToPythonLogger()
            molecule = Chem.MolFromSmiles(smiles)
        except Exception as exc:
            molecule = None
            messages.append(str(exc))
        finally:
            rdBase.LogToCppStreams()
            logger.handlers = previous_handlers
            logger.propagate = previous_propagate
            logger.setLevel(previous_level)
    return molecule, messages


def _message_position(messages: list[str]) -> int | None:
    for message in messages:
        match = re.search(r"position\s+(\d+)", message, flags=re.IGNORECASE)
        if match:
            return int(match.group(1))
    for message in messages:
        match = re.search(r"atom\s*#?\s*(\d+)", message, flags=re.IGNORECASE)
        if match:
            return int(match.group(1)) + 1
    return None


def _heuristic_error_position(smiles: str) -> int | None:
    stacks: dict[str, list[int]] = {"(": [], "[": []}
    closing = {")": "(", "]": "["}
    for index, character in enumerate(smiles, start=1):
        if character in stacks:
            stacks[character].append(index)
        elif character in closing:
            opening = closing[character]
            if not stacks[opening]:
                return index
            stacks[opening].pop()
    unmatched = sorted(position for positions in stacks.values() for position in positions)
    if unmatched:
        return unmatched[-1]
    ring_positions: dict[str, list[int]] = {}
    for match in re.finditer(r"%\d{2}|\d", smiles):
        ring_positions.setdefault(match.group(0), []).append(match.start() + 1)
    for positions in ring_positions.values():
        if len(positions) % 2:
            return positions[-1]
    for index, character in enumerate(smiles, start=1):
        if character.isspace() or character in "（）【】＝－–—":
            return index
    return None


def _localized_parse_reason(messages: list[str], smiles: str) -> str:
    joined = " ".join(messages).lower()
    if "extra open parentheses" in joined:
        return "存在未闭合的分支括号。"
    if "extra close parentheses" in joined:
        return "存在多余的右括号。"
    if "unclosed ring" in joined:
        return "环编号未闭合或未成对出现。"
    if "valence" in joined:
        return "原子价态或电荷写法无效。"
    if "syntax error" in joined:
        return "SMILES 语法错误，请检查标记位置附近的原子或键。"
    if any(character in smiles for character in "（）【】＝－–—"):
        return "包含 SMILES 不支持的全角或排版符号。"
    return "RDKit 无法解析该 SMILES，请检查原子、键、价态、括号和环编号。"
