"""Start outfits of a race's own faction (race/outfits.py)."""

import struct

from retroport.dbc import DBCFile, RawDBC
from retroport.race import outfits

HUMAN, ORC, DWARF, UNDEAD, TAUREN = 1, 2, 3, 5, 6
HUNTER, WARRIOR = 3, 1
FACTION = {HUMAN: 0, DWARF: 0, ORC: 1, UNDEAD: 1, TAUREN: 1}
# Rugged Trapper's shirt, pants, boots and a blunderbuss; Trapper's shirt, pants, boots and a bow
DWARF_KIT, ORC_KIT = (148, 147, 129, 2508), (127, 6126, 6127, 2504)
DISPLAYS = {148: 9976, 147: 9975, 129: 9977, 2508: 6606, 127: 9996, 6126: 10002, 6127: 10003, 2504: 8106,
            outfits.HEARTHSTONE: 6418, 38: 9891}


def _rec(row_id: int, race: int, cls: int, sex: int, items, displays=None) -> bytes:
    items = list(items) + [outfits.HEARTHSTONE]
    shown = list(displays) if displays else [DISPLAYS[i] for i in items]
    pad = [-1] * (outfits.SLOTS - len(items))
    slots = [k + 4 for k in range(len(items))] + pad
    return (struct.pack("<I4B", row_id, race, cls, sex, 0) + struct.pack(f"<{outfits.SLOTS}i", *items, *pad)
            + struct.pack(f"<{outfits.SLOTS}i", *shown, *pad) + struct.pack(f"<{outfits.SLOTS}i", *slots))


def _outfits(*rows) -> RawDBC:
    dbc = RawDBC(77, 296)
    for n, row in enumerate(rows, 1):
        dbc.add_record(_rec(n, *row))
    return dbc


def _items(rec: bytes) -> list[int]:
    return [i for i in struct.unpack_from(f"<{outfits.SLOTS}i", rec, outfits.ITEMS) if i > 0]


def test_a_race_wearing_only_the_other_factions_outfit_gets_its_own_factions():
    """All Races All Classes' Undead hunter wears the Dwarf's outfit (and the Human's, a copy of
    it): it gets the Orc's, the first Horde race's."""
    dbc = _outfits((HUMAN, HUNTER, 0, DWARF_KIT), (ORC, HUNTER, 0, ORC_KIT), (DWARF, HUNTER, 0, DWARF_KIT),
                   (UNDEAD, HUNTER, 0, DWARF_KIT), (TAUREN, HUNTER, 0, (127, 6126, 2508)),
                   (UNDEAD, HUNTER, 1, DWARF_KIT), (ORC, HUNTER, 1, ORC_KIT), (DWARF, HUNTER, 1, DWARF_KIT),
                   (UNDEAD, WARRIOR, 0, (38,)), (HUMAN, WARRIOR, 0, (38,)))
    before = list(dbc.records)
    assert outfits.faction_outfits(dbc, FACTION) == [(UNDEAD, HUNTER, 0, ORC), (UNDEAD, HUNTER, 1, ORC)]
    undead = dbc.records[3]
    assert struct.unpack_from("<I4B", undead, 0) == (4, UNDEAD, HUNTER, 0, 0)           # its own row still
    assert undead[outfits.ITEMS:] == before[1][outfits.ITEMS:]                          # the Orc's outfit
    # the rest as they were: a Human wearing the Dwarf's (its faction's), the Tauren's own, an
    # outfit both factions wear
    assert [r for n, r in enumerate(dbc.records) if n not in (3, 5)] == [r for n, r in enumerate(before) if n not in (3, 5)]
    assert outfits.faction_outfits(dbc, FACTION) == []


def test_a_race_and_its_twin_in_the_other_faction_keep_the_outfit_they_share():
    """A Kul Tiran is races 22 (Alliance) and 23 (Horde) wearing BfA's armour, the same outfit: neither
    is wearing the other faction's."""
    kul_tiran_a, kul_tiran_h = 22, 23
    faction = {**FACTION, kul_tiran_a: 0, kul_tiran_h: 1}
    kin = {kul_tiran_a: "kultiran", kul_tiran_h: "kultiran", HUMAN: "human", ORC: "orc", DWARF: "dwarf"}
    kit = DWARF_KIT                                         # worn by no other warrior

    def rows():
        return _outfits((HUMAN, WARRIOR, 0, (38,)), (ORC, WARRIOR, 0, ORC_KIT), (kul_tiran_a, WARRIOR, 0, kit),
                        (kul_tiran_h, WARRIOR, 0, kit))
    assert outfits.faction_outfits(rows(), faction, kin) == []
    # without knowing they are one race, each looked like a wearer of the other faction's
    assert outfits.faction_outfits(rows(), faction) == [(22, WARRIOR, 0, HUMAN), (23, WARRIOR, 0, ORC)]
    # and its outfit is given to no other race: a Dwarf warrior wearing the Orc's gets nothing from it
    dwarf = _outfits((ORC, WARRIOR, 0, ORC_KIT), (DWARF, WARRIOR, 0, ORC_KIT), (kul_tiran_a, WARRIOR, 0, kit),
                     (kul_tiran_h, WARRIOR, 0, kit))
    assert outfits.faction_outfits(dwarf, faction, kin) == []


def test_every_item_shows_its_own_display():
    """The Human and Undead hunters' shirt showed display 9676 (the Rugged Trapper's shirt is 9976):
    they stood shirtless in character create."""
    typo = [9676, 9975, 9977, 6606, 6418]
    dbc = _outfits((HUMAN, HUNTER, 0, DWARF_KIT, typo), (DWARF, HUNTER, 0, DWARF_KIT))
    assert outfits.fix_displays(dbc, DISPLAYS) == [(HUMAN, HUNTER, 0, 148, 9676, 9976)]
    assert dbc.records[0] == _rec(1, HUMAN, HUNTER, 0, DWARF_KIT)
    assert outfits.fix_displays(dbc, DISPLAYS) == []


def test_factions_are_chrraces_alliance_column_of_the_playable_races():
    chr_races = DBCFile(69)
    for race, flags, horde in ((HUMAN, 0, 0), (ORC, 0, 1), (9, 1, 1)):      # 9: not playable
        row = [0] * 69
        row[0], row[1], row[outfits.CHRRACES_ALLIANCE] = race, flags, horde
        chr_races.add_record(row)
    assert outfits.factions(chr_races) == {HUMAN: 0, ORC: 1}
