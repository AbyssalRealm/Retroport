"""Named columns of 3.3.5a (12340) DBC files, from WoWDBDefs.

A ``.dbc`` row is a flat list of 32-bit fields; this maps a WoWDBDefs column name to its place:
``DbcLayout("Spell")["EffectAura"]`` -> (95, 3). A ``locstring`` takes 17 fields in a pre-Cataclysm
DBC - one string per locale, then a flags field - and an array column as many as its size.
"""

from __future__ import annotations

import functools
import os

BUILD = "3.3.5.12340"
LOCALES = 16
# the flags field after a locstring's 16 strings, as Blizzard's 3.3.5 rows carry it
LOCSTRING_FLAGS = 0x00FF01FE


class LayoutError(ValueError):
    pass


@functools.lru_cache(maxsize=None)
def _index(dbd_dir: str):
    from wotlkconv.db.dbd import DbdIndex
    index = DbdIndex(dbd_dir)
    if not index:
        raise LayoutError(f"no .dbd definitions in {dbd_dir}")
    return index


class DbcLayout:
    def __init__(self, table: str, dbd_dir: str, build: str = BUILD):
        definition = _index(os.path.abspath(dbd_dir)).get(table)
        layout = definition.by_build(build) if definition else None
        if layout is None:
            raise LayoutError(f"WoWDBDefs has no {build} layout for {table}")
        self.table = table
        self.columns: dict[str, tuple[int, int]] = {}
        self.strings: set[str] = set()
        self.localized: set[str] = set()
        at = 0
        for c in layout.inline_columns():
            width = LOCALES + 1 if c.localized else c.array_size
            self.columns[c.name] = (at, width)
            if c.is_string:
                self.strings.add(c.name)
            if c.localized:
                self.localized.add(c.name)
            at += width
        self.field_count = at

    def __getitem__(self, name: str) -> tuple[int, int]:
        try:
            return self.columns[name]
        except KeyError:
            raise LayoutError(f"{self.table} has no column {name}") from None

    def at(self, name: str, k: int = 0) -> int:
        start, width = self[name]
        if not 0 <= k < width:
            raise LayoutError(f"{self.table}.{name}[{k}] is out of range (size {width})")
        return start + k

    def get(self, row: list[int], name: str, k: int = 0) -> int:
        return row[self.at(name, k)]

    def set(self, row: list[int], name: str, value: int, k: int = 0) -> None:
        row[self.at(name, k)] = value

    def set_text(self, dbc, row: list[int], name: str, text: str) -> None:
        """A locstring: the same text in every locale (the client shows its own), Blizzard's flags."""
        start, width = self[name]
        offset = dbc.add_string(text) if text else 0
        row[start:start + LOCALES] = [offset] * LOCALES
        row[start + LOCALES] = LOCSTRING_FLAGS if width > LOCALES else row[start + LOCALES]

    def text(self, dbc, row: list[int], name: str) -> str:
        return dbc.get_string(row[self.at(name)])

    def blank(self) -> list[int]:
        return [0] * self.field_count
