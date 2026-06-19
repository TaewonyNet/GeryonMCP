"""카테고리 규칙 적용. Silver 인덱싱 소관 — 사용자 무관.

규칙 파일 `~/.geryon/gold/category.yaml`(사람 편집, 인덱싱 설정):
  - match: {space?, tag?, hierarchy_prefix?}
    set: [category, ...]
산출 `Document.category`(자유 문자열 다중값)는 모든 사용자에게 동일하므로 Silver다.
"""
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

RULES_PATH = Path.home() / ".geryon" / "gold" / "category.yaml"


def load_rules(path: Path = RULES_PATH) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return data if isinstance(data, list) else []


def categories_for(doc: Any, rules: list[dict[str, Any]]) -> list[str]:
    """doc.category(수집물) + 규칙 매칭 → 최종 category 다중값(순서 보존·중복 제거)."""
    out: list[str] = list(getattr(doc, "category", None) or [])
    hierarchy_str = " > ".join(getattr(doc, "hierarchy", None) or [])
    for r in rules:
        m = r.get("match", {})
        ok = True
        if "space" in m and m["space"] != getattr(doc, "space_or_repo", None):
            ok = False
        if "tag" in m and m["tag"] not in (getattr(doc, "tags", None) or []):
            ok = False
        if "hierarchy_prefix" in m and not hierarchy_str.startswith(m["hierarchy_prefix"]):
            ok = False
        if ok:
            out.extend(r.get("set", []))
    seen: set[str] = set()
    res: list[str] = []
    for c in out:
        if c not in seen:
            seen.add(c)
            res.append(c)
    return res
