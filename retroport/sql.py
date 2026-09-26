"""Repoint SQL for AzerothCore's acore_world (``creature_template_model`` schema).

Adapted from Conjure's ``conjure/sql.py`` (MIT, TheDaveCalaz). Retroport never
talks to a database - it only produces text to copy/paste.
"""

from __future__ import annotations


class SQLInputError(ValueError):
    pass


def escape_sql_like(term: str) -> str:
    """Escape a term for a single-quoted LIKE pattern (handles names like Vol'jin)."""
    return term.replace("\\", "\\\\").replace("'", "\\'")


def build_name_search_sql(search_term: str) -> str:
    if not search_term:
        raise SQLInputError("enter a creature name (or part of one) to search for")
    return f"SELECT entry, name FROM creature_template WHERE name LIKE '%{escape_sql_like(search_term)}%';"


def parse_entries(entries_str: str) -> list[int]:
    parts = [p.strip() for p in (entries_str or "").replace(";", ",").split(",") if p.strip()]
    if not parts:
        raise SQLInputError("enter at least one creature entry (the numeric id from creature_template)")
    bad = [p for p in parts if not p.isdigit()]
    if bad:
        raise SQLInputError(f"'{bad[0]}' is not a valid numeric creature entry")
    return [int(p) for p in parts]


def build_repoint_sql_block(search_term: str, entries_str: str, new_display_id: int) -> str:
    """The full copy-paste block: find, inspect, check display sharing, repoint.

    Step 3 uses a subquery so the block needs only the entries and the new
    DisplayID - no hand-copied display ids."""
    entries = ", ".join(str(e) for e in parse_entries(entries_str))
    search = (build_name_search_sql(search_term) if (search_term or "").strip()
              else "-- (no name search term given - you already know the entry/entries)")
    return "\n".join([
        "-- 1) find the creature entry/entries:",
        search,
        "",
        "-- 2) see which displays they use:",
        "SELECT ct.entry, ctm.Idx, ctm.CreatureDisplayID FROM creature_template ct",
        "  JOIN creature_template_model ctm ON ctm.CreatureID = ct.entry",
        f"  WHERE ct.entry IN ({entries}) ORDER BY ctm.CreatureDisplayID, ct.entry;",
        "",
        "-- 3) confirm those displays aren't shared with creatures NOT in this list",
        "--    (never edit a shared display - if this returns rows, repoint by CreatureID only):",
        "SELECT DISTINCT CreatureID FROM creature_template_model",
        "  WHERE CreatureDisplayID IN (SELECT CreatureDisplayID FROM creature_template_model "
        f"WHERE CreatureID IN ({entries}))",
        f"  AND CreatureID NOT IN ({entries});",
        "",
        "-- 4) repoint ONLY the target creature(s) (updates every Idx row for each):",
        f"UPDATE creature_template_model SET CreatureDisplayID = {new_display_id} WHERE CreatureID IN ({entries});",
    ])
