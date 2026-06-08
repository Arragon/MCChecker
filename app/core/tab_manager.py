from __future__ import annotations

from typing import Dict, List, Optional, Tuple


def ensure_opened_at(tabs: List[Dict]) -> None:
    for i, t in enumerate(tabs):
        if "opened_at" not in t:
            t["opened_at"] = i


def enforce_tab_limit(
    tabs: List[Dict],
    active_name: str,
    limit: int = 15,
) -> List[str]:
    removed: List[str] = []
    if limit <= 0:
        return removed

    ensure_opened_at(tabs)
    while len(tabs) > limit:
        candidates = [t for t in tabs if t.get("name") != active_name]
        if not candidates:
            break
        candidates.sort(key=lambda x: x.get("opened_at", 0))
        to_remove = candidates[0]
        removed.append(to_remove.get("name"))
        try:
            tabs.remove(to_remove)
        except ValueError:
            break
    return removed


def close_tab_adjacent(
    tabs: List[Dict],
    active_name: str,
    close_name: str,
) -> Tuple[List[Dict], str]:
    if close_name == "overview":
        return tabs, active_name

    idx = next((i for i, t in enumerate(tabs) if t.get("name") == close_name), None)
    if idx is None:
        return tabs, active_name

    closing_active = active_name == close_name
    tabs = [t for t in tabs if t.get("name") != close_name]

    if not closing_active:
        return tabs, active_name

    if not tabs:
        return tabs, "overview"

    new_idx = min(idx, len(tabs) - 1)
    return tabs, tabs[new_idx].get("name") or "overview"


def prune_history(history: List[str], removed_names: List[str]) -> List[str]:
    if not removed_names:
        return history
    removed = set(removed_names)
    return [h for h in history if h not in removed]

