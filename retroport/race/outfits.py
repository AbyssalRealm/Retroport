"""Start outfits of a race's own faction: a package of fixes to CharStartOutfit.dbc.

An All Races All Classes patch gives a race a class 3.3.5 doesn't by copying another race's
start outfit, and some copies cross factions: its Undead hunter wears the Dwarf's (the Rugged
Trapper's set and a blunderbuss, where every other Horde hunter wears the Trapper's set and a
bow), its Blood Elf druid the Night Elf's. And it wrote the copies' display ids by hand: the
Human and Undead hunters' shirt (item 148) shows display 9676, not the shirt's 9976, so in
character create they stand shirtless.

Both copies get the same two fixes - the client's shows character create, the server's gives the
items (AzerothCore's Player::Create):

* a race/class/sex whose outfit no other race of its faction wears, but a race of the other
  faction does, gets the outfit of the first race (by id) of its faction with the class whose
  outfit is its faction's own;
* every item shows its own display (Item.dbc's DisplayInfoID).

The package is built on the files the client and server load, so it keeps a race package's rows;
install it after the race packages, then rebuild them on top of it as on top of the glue package.
"""

from __future__ import annotations

import hashlib
import os
import struct
from typing import Callable

from pydantic import BaseModel

from ..dbc import DBCFile, RawDBC
from ..mpq import ClientData
from ..safeio import write_verified
from .tables import item_displays, playable_races

OUTFIT = "CharStartOutfit.dbc"
CLIENT_DBC = "DBFilesClient\\"
# CharStartOutfit: uint32 ID, uint8 race, class, sex, outfit, then 24 item ids, their 24 display
# ids and their 24 inventory types
ITEMS, DISPLAYS, SLOTS = 8, 104, 24
OUTFIT_END = 8 + 3 * 4 * SLOTS
HEARTHSTONE = 6948              # every outfit's: no sign of whose an outfit is
CHRRACES_ALLIANCE = 13          # ChrRaces: 0 Alliance, 1 Horde
CHRRACES_NAME = 14              # ChrRaces: Name, enUS
CLASSES = {1: "Warrior", 2: "Paladin", 3: "Hunter", 4: "Rogue", 5: "Priest", 6: "Death Knight", 7: "Shaman",
           8: "Mage", 9: "Warlock", 11: "Druid"}


class OutfitsError(ValueError):
    """Files the fixes can't be built from."""


class OutfitsResult(BaseModel):
    name: str = "Start outfit fixes"
    kind: str = "outfits"
    bases: dict[str, dict] = {}         # client/... and server/dbc/... -> {source, sha256}
    owns: list[str] = []
    changes: dict[str, list[str]] = {}  # client / server -> what changed, in words
    warnings: list[str] = []


def _row(rec: bytes) -> tuple[int, int, int]:
    _id, race, cls, sex, _outfit = struct.unpack_from("<I4B", rec, 0)
    return race, cls, sex


def _kit(rec: bytes) -> frozenset[int]:
    return frozenset(i for i in struct.unpack_from(f"<{SLOTS}i", rec, ITEMS) if i > 0 and i != HEARTHSTONE)


def factions(chr_races: DBCFile) -> dict[int, int]:
    """Playable race -> 0 Alliance / 1 Horde."""
    return {r[0]: r[CHRRACES_ALLIANCE] for r in playable_races(chr_races)}


def faction_outfits(outfits: RawDBC, faction: dict[int, int], kin: dict[int, str] | None = None
                    ) -> list[tuple[int, int, int, int]]:
    """Give every race/class/sex that wears only the other faction's outfit its own faction's
    (module docstring); (race, class, sex, the race whose outfit it got) of each change. ``kin``
    (race -> ClientFileString): a race's own twin in the other faction (a Kul Tiran's 22 and 23,
    wearing the BfA outfit race packages give them) is no other faction's wearer, and such a race's
    outfit is its own, given to no other race."""
    rows = {_row(rec): i for i, rec in enumerate(outfits.records) if _row(rec)[0] in faction}
    kits = {key: _kit(outfits.records[i]) for key, i in rows.items()}
    kin = kin or {}
    twins = {r for r in kin if sum(1 for v in kin.values() if v == kin[r]) > 1}
    crossed = set()
    for (race, cls, sex), kit in kits.items():
        wearers = [r for (r, c, s), k in kits.items() if (c, s) == (cls, sex) and r != race and k == kit
                   and not (race in kin and kin.get(r) == kin[race])]
        if wearers and all(faction[r] != faction[race] for r in wearers):
            crossed.add((race, cls, sex))
    changes = []
    for race, cls, sex in sorted(crossed):
        donors = sorted(r for (r, c, s) in kits if (c, s) == (cls, sex) and faction[r] == faction[race]
                        and (r, c, s) not in crossed and r not in twins)
        if not donors:
            continue
        rec = bytearray(outfits.records[rows[(race, cls, sex)]])
        rec[ITEMS:OUTFIT_END] = outfits.records[rows[(donors[0], cls, sex)]][ITEMS:OUTFIT_END]
        outfits.records[rows[(race, cls, sex)]] = bytes(rec)
        changes.append((race, cls, sex, donors[0]))
    return changes


def fix_displays(outfits: RawDBC, displays: dict[int, int]) -> list[tuple[int, int, int, int, int, int]]:
    """Every item showing its own display; (race, class, sex, item, display it had, its own) of each."""
    changes = []
    for n, rec in enumerate(outfits.records):
        out = bytearray(rec)
        items = struct.unpack_from(f"<{SLOTS}i", rec, ITEMS)
        shown = struct.unpack_from(f"<{SLOTS}i", rec, DISPLAYS)
        for k, (item, display) in enumerate(zip(items, shown)):
            own = displays.get(item) if item > 0 else None
            if own is not None and own != display:
                struct.pack_into("<i", out, DISPLAYS + 4 * k, own)
                changes.append((*_row(rec), item, display, own))
        outfits.records[n] = bytes(out)
    return changes


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fix(outfits: RawDBC, chr_races: DBCFile, items: DBCFile, names: dict[int, str]) -> list[str]:
    def who(race: int, cls: int, sex: int) -> str:
        return f"{names.get(race, f'race {race}')} {CLASSES.get(cls, f'class {cls}')} ({'male' if sex == 0 else 'female'})"
    said = [f"{who(race, cls, sex)}: the {names.get(donor, f'race {donor}')}'s outfit"
            for race, cls, sex, donor in faction_outfits(outfits, factions(chr_races), _file_strings(chr_races))]
    said += [f"{who(race, cls, sex)}: item {item} shows display {own} (was {had})"
             for race, cls, sex, item, had, own in fix_displays(outfits, item_displays(items))]
    return said


def _file_strings(chr_races: DBCFile) -> dict[int, str]:
    """Playable race -> its ClientFileString (a race with one per faction shares it)."""
    return {r[0]: chr_races.get_string(r[11]).lower() for r in playable_races(chr_races)}


def _race_names(chr_races: DBCFile) -> dict[int, str]:
    """Race -> its name (enUS; else its ClientFileString)."""
    return {r[0]: chr_races.get_string(r[CHRRACES_NAME]) or chr_races.get_string(r[11]) for r in playable_races(chr_races)}


def build_outfits(*, client_dir: str, server_dbc_dir: str, out_dir: str, patch: str | None = "patch-H.MPQ",
                  on_log: Callable[[str], None] | None = None) -> OutfitsResult:
    """The fixed CharStartOutfit.dbc for the client (from the copy it loads) and the server, as a
    package for ``retroport install``."""
    log = on_log or (lambda _m: None)
    res = OutfitsResult()
    client = ClientData(client_dir, top=patch)
    try:
        hit = client.find(CLIENT_DBC + OUTFIT)
        if hit is None:
            raise OutfitsError(f"{CLIENT_DBC}{OUTFIT} is in no archive of {client_dir}")
        log(f"Client {OUTFIT}: {hit[0]}")
        chr_races = DBCFile.from_bytes(client.read(CLIENT_DBC + "ChrRaces.dbc"))
        outfits = RawDBC.from_bytes(hit[1])
        res.changes["client"] = _fix(outfits, chr_races, DBCFile.from_bytes(client.read(CLIENT_DBC + "Item.dbc")),
                                     _race_names(chr_races))
        res.bases["client/DBFilesClient/" + OUTFIT] = {"source": hit[0], "sha256": _sha256(hit[1])}
        path = outfits.save(os.path.join(out_dir, "client", *CLIENT_DBC.rstrip("\\").split("\\"), OUTFIT))
        log(f"  client: {len(res.changes['client'])} fix(es) -> {path}")
    finally:
        client.close()

    server = os.path.join(server_dbc_dir, OUTFIT)
    with open(server, "rb") as fh:
        data = fh.read()
    chr_races = DBCFile.load(os.path.join(server_dbc_dir, "ChrRaces.dbc"))
    outfits = RawDBC.from_bytes(data, server)
    res.changes["server"] = _fix(outfits, chr_races, DBCFile.load(os.path.join(server_dbc_dir, "Item.dbc")),
                                 _race_names(chr_races))
    res.bases["server/dbc/" + OUTFIT] = {"source": server, "sha256": _sha256(data)}
    path = outfits.save(os.path.join(out_dir, "server", "dbc", OUTFIT))
    log(f"  server: {len(res.changes['server'])} fix(es) -> {path}")

    write_verified(os.path.join(out_dir, "package.json"), res.model_dump_json(indent=1).encode("utf-8"))
    _write_install(out_dir, res)
    return res


def _write_install(out_dir: str, res: OutfitsResult) -> None:
    lines = ["Start outfits of a race's own faction - fixes to CharStartOutfit.dbc built by retroport", "",
             "INSTALL: retroport install - it never overwrites a file you changed since this build, backs",
             "up everything it replaces, and writes an undo receipt:",
             f"  retroport install \"{out_dir}\" --client <WoW folder> --patch patch-H.MPQ --server-dbc <server dbc folder>",
             "  (shows the plan; add --apply to install; undo: retroport uninstall <installs\\...json>)", "",
             "RACE PACKAGES: built on the files your client and server load, so it keeps the rows of the",
             "  race packages installed; after installing it, rebuild each race package on top of it",
             "  (`retroport race ... --reuse --replace`) and install that.", ""]
    for side in ("client", "server"):
        lines += [f"{side.title()}:"] + ([f"  - {c}" for c in res.changes.get(side, [])] or ["  (nothing to fix)"])
    lines += ["", "Built on:"] + [f"  {k}: {v['source']}" for k, v in res.bases.items()]
    if res.warnings:
        lines += ["", "Warnings:"] + [f"  - {w}" for w in res.warnings]
    write_verified(os.path.join(out_dir, "INSTALL.txt"), ("\r\n".join(lines) + "\r\n").encode("utf-8"))
