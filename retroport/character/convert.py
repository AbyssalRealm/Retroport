"""`retroport character`: port one BfA (8.x) playable race/sex to 3.3.5a.

Output (under ``out_dir``, laid out like the MPQ):

* ``Character\\<Race>\\<Sex>\\<Race><Sex>.m2`` + skins + anims - converted by wotlkconv
  straight from the install, fixed up, and UV-remapped to the 3.3.5 layout;
* every CharSections texture the race/sex uses, converted to the 3.3.5 layout and
  written palettized (facial hair and scalp overlays with 8-bit alpha, never DXT);
* ``DBFilesClient\\CharSections.dbc``, ``CharHairGeosets.dbc``,
  ``CharacterFacialHairStyles.dbc`` - the template DBCs with rows for ``race_id``
  appended (when a template folder is given).

BfA CharSections slots (per wowdev / the BfA tables) and what each becomes:
  0 skin:        [skin composite, extra composite, -]
  1 face:        [FaceLower, FaceUpper, -]               (layout 2: one texture, whole face)
  2 facial hair: [FacialLowerHair, FacialUpperHair, -]
  3 hair:        [hair texture, ScalpLowerHair, ScalpUpperHair]
  4 underwear:   [NakedPelvisSkin, NakedTorsoSkin, -]
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, field
from typing import Callable

from pydantic import BaseModel

from ..check import analyze_skin, check_model
from ..convert import engine, fixup
from ..convert.texbake import resolve_fdids
from ..dbc import DBCError, DBCFile, _refuse_overwrite
from ..formats import blp
from ..listfile import Listfile
from ..safeio import write_verified
from . import layouts as L
from .model import remap_model_uvs
from .textures import convert_composite, convert_region, group_for

SEX_NAMES = {0: "Male", 1: "Female"}
FACE, LOWER, UPPER = (9, 10), 10, 9
# (BaseSection, slot) -> the layout section a region texture covers
REGION_SLOTS = {(1, 0): LOWER, (1, 1): UPPER, (2, 0): LOWER, (2, 1): UPPER,
                (3, 1): LOWER, (3, 2): UPPER, (4, 0): 5, (4, 1): 3}
# (BaseSection, destination section) -> 3.3.5 slot, and the file-name part
OUT_SLOTS = {(1, LOWER): (0, "FaceLower"), (1, UPPER): (1, "FaceUpper"),
             (2, LOWER): (0, "FacialLowerHair"), (2, UPPER): (1, "FacialUpperHair"),
             (3, LOWER): (1, "ScalpLowerHair"), (3, UPPER): (2, "ScalpUpperHair"),
             (4, 5): (0, "NakedPelvisSkin"), (4, 3): (1, "NakedTorsoSkin")}
DB2_TABLES = ("ChrRaces", "CharComponentTextureLayouts", "CharComponentTextureSections",
              "CharSections", "TextureFileData", "CharHairGeosets", "CharacterFacialHairStyles")
CHARSECTIONS_FIELDS, HAIR_FIELDS, FACIAL_FIELDS = 10, 6, 8


class CharacterResult(BaseModel):
    race: str
    sex: str
    race_id: int
    model: str = ""
    files: list[str] = []
    textures: int = 0
    rows: dict[str, int] = {}
    uv: dict = {}
    warnings: list[str] = []
    check: list[str] = []


@dataclass
class _Row:
    section: int
    variation: int
    color: int
    flags: int
    textures: list[str] = field(default_factory=lambda: ["", "", ""])


def _find_listfile_paths(listfile: str, paths: set[str]) -> dict[str, int]:
    """Stream the listfile once for exact (case-insensitive) paths -> FileDataID."""
    want = {p.lower().replace("\\", "/") for p in paths}
    found: dict[str, int] = {}
    with open(listfile, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parsed = Listfile._split(line)
            if parsed:
                key = parsed[1].lower().replace("\\", "/")
                if key in want:
                    found[key] = parsed[0]
    return found


class BfaInstall:
    """Read access to a local BfA install: files by FileDataID and DB2 tables by name."""

    def __init__(self, install_dir: str, dbd_dir: str, listfile: str):
        from wotlkconv.casc import CascStorage
        from wotlkconv.db.dbd import DbdIndex
        self.casc = CascStorage.open(install_dir)
        self.defs = DbdIndex(dbd_dir)
        if not self.defs:
            raise ValueError(f"no .dbd definitions in {dbd_dir} (point at WoWDBDefs\\definitions)")
        self.listfile = listfile
        self._tables: dict[str, list[tuple[int, dict]]] = {}
        self.fdids = _find_listfile_paths(listfile, {f"dbfilesclient/{t.lower()}.db2" for t in DB2_TABLES})

    def read(self, fdid: int) -> bytes:
        return self.casc.read_file_id(fdid)

    def table(self, name: str) -> list[tuple[int, dict]]:
        from wotlkconv.db.db2 import parse_db2
        if name not in self._tables:
            fdid = self.fdids.get(f"dbfilesclient/{name.lower()}.db2")
            if fdid is None:
                raise ValueError(f"{name}.db2 is not in the listfile")
            data = self.casc.read_file_id(fdid, zero_encrypted=True)
            self._tables[name] = list(parse_db2(data, name, definitions=self.defs, table_name=name))
        return self._tables[name]


def _find_race(install: BfaInstall, race: str) -> tuple[int, dict]:
    want = race.lower().replace(" ", "").replace("'", "")
    for rid, row in install.table("ChrRaces"):
        names = {str(row.get("ClientFileString", "")).lower(), str(row.get("Name_lang", "")).lower().replace(" ", "").replace("'", "")}
        if want in names:
            return rid, row
    raise ValueError(f"race '{race}' not found in ChrRaces (use the ClientFileString, e.g. KulTiran)")


def convert_character(install_dir: str, race: str, sex: str, out_dir: str, *, race_id: int = 33,
                      listfile: str, dbd_dir: str, dbc_dir: str | None = None, target: str = "hd",
                      on_log: Callable[[str], None] | None = None) -> CharacterResult:
    log = on_log or (lambda m: None)
    sex_id = {"male": 0, "female": 1}.get(sex.lower())
    if sex_id is None:
        raise ValueError("sex must be male or female")
    if target not in L.TARGETS:
        raise ValueError("target must be 'hd' or 'classic'")
    if not engine.available():
        raise ValueError(engine.INSTALL_HINT)
    if dbc_dir:
        _refuse_overwrite(dbc_dir, os.path.join(out_dir, "DBFilesClient"))

    log(f"Opening {install_dir} ...")
    bfa = BfaInstall(install_dir, dbd_dir, listfile)
    bfa_race_id, race_row = _find_race(bfa, race)
    cfs = race_row["ClientFileString"]
    sex_name = SEX_NAMES[sex_id]
    res = CharacterResult(race=cfs, sex=sex_name, race_id=race_id)
    log(f"Race {cfs} (BfA id {bfa_race_id}), {sex_name} -> 3.3.5 race id {race_id}, {target} layout")

    # --- layouts ---
    layouts = {i: r for i, r in bfa.table("CharComponentTextureLayouts")}
    sections = [r for _i, r in bfa.table("CharComponentTextureSections")]
    src = L.layout_from_tables(layouts, sections, race_row["CharComponentTextureLayoutID"])
    dst = L.TARGETS[target]
    groups = L.build_groups(src, dst)
    identity = L.is_identity(src, dst)
    log(f"  source layout {race_row['CharComponentTextureLayoutID']} ({src.width}x{src.height}) -> "
        f"{dst.width}x{dst.height}" + (" (same shape: no UV remap)" if identity else ""))

    # --- model ---
    char_dir = os.path.join(out_dir, "Character", cfs, sex_name)
    stem = f"{cfs}{sex_name}"
    model_path = f"character/{cfs.lower()}/{sex_name.lower()}/{cfs.lower()}{sex_name.lower()}.m2"
    found = _find_listfile_paths(listfile, {model_path})
    if model_path not in found:
        raise ValueError(f"{model_path} is not in the listfile")
    work = os.path.join(out_dir, "_work")
    os.makedirs(work, exist_ok=True)
    src_model = os.path.join(work, f"{stem}.m2")
    write_verified(src_model, bfa.read(found[model_path]))
    log(f"Converting {model_path} (wotlkconv, from the install)...")
    er = engine.convert_with_wotlkconv(src_model, char_dir, output_stem=stem, listfile_path=listfile,
                                       casc=bfa.casc, on_log=log)
    for n in er.notes:
        if n.level != "info":
            log(f"  [{n.level}] {n.code}: {n.message}")
    if not er.ok:
        raise ValueError(f"model conversion failed: {er.error}")
    res.model = er.output
    res.files += er.written
    skins = sorted(p for p in er.written if p.lower().endswith(".skin"))

    fixup.clear_stale_anim_flags(er.output)
    for sp in skins:
        fixup.clamp_skin_submeshes(sp, 64)
    fixup.clear_combiner_combos(er.output, skins)

    if not identity:
        rep = remap_model_uvs(er.output, skins, src, dst, groups)
        res.uv = rep.__dict__
        log(f"  UV remap: {rep.remapped} skin vertices moved {rep.per_group}; unchanged: "
            f"{rep.outside} outside any section, {rep.shared} shared with other textures; "
            f"{rep.straddling} triangle(s) straddle two regions")
        if rep.straddling or rep.shared:
            res.warnings.append(f"UV remap: {rep.straddling} straddling triangle(s), {rep.shared} shared vertices")
        if rep.outside:
            res.warnings.append(f"UV remap: {rep.outside} skin vertices outside every section (left as-is)")

    # --- textures ---
    tfd: dict[int, list[tuple[int, int]]] = {}
    for fid, r in bfa.table("TextureFileData"):
        tfd.setdefault(r["MaterialResourcesID"], []).append((r.get("UsageType", 0), r.get("FileDataID", fid)))
    rows = [r for _i, r in bfa.table("CharSections")
            if r["RaceID"] == bfa_race_id and r["SexID"] == sex_id and r["BaseSection"] in (0, 1, 2, 3, 4)]
    log(f"Converting textures for {len(rows)} CharSections row(s)...")
    out_rows, cache = [], {}
    images: dict[int, object] = {}

    def fdid_of(mat: int) -> int | None:
        files = sorted(tfd.get(mat, []))
        return files[0][1] if files else None

    def image(fdid: int):
        if fdid not in images:
            p = os.path.join(work, f"{fdid}.blp")
            write_verified(p, bfa.read(fdid))
            images[fdid] = blp.decode(p)
        return images[fdid]

    def write_blp(name: str, img, folder: str) -> str:
        rel = "\\".join(["Character", cfs, *([sex_name] if folder == "sex" else []), name + ".blp"])
        out = os.path.join(out_dir, *rel.split("\\"))
        if out not in cache:
            data = blp.encode_palettized(img)
            write_verified(out, data, lambda p: blp.verify_blp(p, img.size))
            cache[out] = True
            res.files.append(out)
        return rel

    for r in rows:
        sec, var, col = r["BaseSection"], r["VariationIndex"], r["ColorIndex"]
        mats = list(r["MaterialResourcesID"]) + [0, 0, 0]
        row = _Row(sec, var, col, r["Flags"] & 0x1F)
        tag = f"{var:02d}_{col:02d}"
        if sec == 0:
            for slot, suffix in ((0, ""), (1, "_Extra")):
                fd = fdid_of(mats[slot]) if mats[slot] else None
                if fd:
                    img = image(fd) if identity else convert_composite(image(fd), src, dst, groups)
                    if identity:
                        img = img.resize((dst.width, dst.height), blp.Image.Resampling.LANCZOS)
                    row.textures[slot] = write_blp(f"{stem}Skin{tag}{suffix}", img, "sex")
        else:
            if sec == 3 and mats[0]:
                fd = fdid_of(mats[0])
                if fd:
                    row.textures[0] = write_blp(f"Hair{tag}", image(fd), "race")
            by_group: dict = {}
            for slot in range(3):
                target_sec = REGION_SLOTS.get((sec, slot))
                fd = fdid_of(mats[slot]) if mats[slot] and target_sec is not None else None
                if fd:
                    g = group_for(target_sec, groups)
                    if g is None:
                        res.warnings.append(f"section {sec} slot {slot}: no 3.3.5 region for layout section {target_sec}")
                        continue
                    by_group.setdefault(g, []).append((target_sec, image(fd)))
            for g, layers in by_group.items():
                layers.sort(key=lambda t: 0 if t[0] == LOWER else 1)        # lower first, upper on top
                parts = convert_region([img for _s, img in layers], g, dst)
                for dsec, img in parts.items():
                    out_slot = OUT_SLOTS.get((sec, dsec))
                    if out_slot:
                        slot, part = out_slot
                        folder = "sex" if sec in (1, 4, 2) else "race"
                        row.textures[slot] = write_blp(f"{stem}{part}{tag}" if folder == "sex" else f"{part}{tag}",
                                                       img, folder)
        out_rows.append(row)
    res.textures = len(cache)
    log(f"  wrote {res.textures} texture(s)")

    # --- geosets ---
    hair = [r for _i, r in bfa.table("CharHairGeosets")
            if r["RaceID"] == bfa_race_id and r["SexID"] == sex_id]
    skipped = [r for r in hair if r.get("CustomGeoFileDataID") or r.get("HdCustomGeoFileDataID")]
    hair = [r for r in hair if r not in skipped]
    if skipped:
        res.warnings.append(f"{len(skipped)} hair style(s) use separate custom-geometry files; not ported")
    facial = [r for _i, r in bfa.table("CharacterFacialHairStyles")
              if r["RaceID"] == bfa_race_id and r["SexID"] == sex_id]
    model_geosets = set(analyze_skin(skins[0]).geosets) if skins else set()
    missing = sorted({r["GeosetID"] for r in hair if r["GeosetID"] > 0 and r["GeosetID"] not in model_geosets})
    if missing:
        res.warnings.append(f"hair geosets {missing} are referenced but not in the model")

    # --- DBCs ---
    if dbc_dir:
        res.rows = _write_dbcs(dbc_dir, os.path.join(out_dir, "DBFilesClient"), race_id, sex_id,
                               out_rows, hair, facial)
        res.files += [os.path.join(out_dir, "DBFilesClient", n) for n in
                      ("CharSections.dbc", "CharHairGeosets.dbc", "CharacterFacialHairStyles.dbc")]
        log(f"  DBC rows added: {res.rows}")

    chk = check_model(er.output)
    res.check = [f"{f.severity.value} {f.check}: {f.detail}" for f in chk.report().findings
                 if f.severity.value != "pass"]
    shutil.rmtree(work, ignore_errors=True)        # source extracts; nothing to pack
    return res


def _write_dbcs(dbc_dir: str, out_dir: str, race_id: int, sex_id: int, rows: list[_Row],
                hair: list[dict], facial: list[dict]) -> dict[str, int]:
    def load(name: str, fields: int) -> DBCFile:
        path = os.path.join(dbc_dir, name)
        if not os.path.isfile(path):
            raise DBCError(f"{name} not found in {dbc_dir}")
        d = DBCFile.load(path)
        if d.field_count != fields:
            raise DBCError(f"{name}: expected {fields} fields (3.3.5a), found {d.field_count}")
        return d

    cs, hg, fh = (load("CharSections.dbc", CHARSECTIONS_FIELDS), load("CharHairGeosets.dbc", HAIR_FIELDS),
                  load("CharacterFacialHairStyles.dbc", FACIAL_FIELDS))
    if any(r[1] == race_id and r[2] == sex_id for r in cs.records):
        raise DBCError(f"CharSections already has rows for race {race_id} sex {sex_id}; "
                       "use another --race-id or start from a template without them")
    next_id = cs.max_id() + 1
    for r in rows:
        cs.add_record([next_id, race_id, sex_id, r.section, *(cs.add_string(t) for t in r.textures),
                       r.flags, r.variation, r.color])
        next_id += 1
    next_id = hg.max_id() + 1
    for r in hair:
        hg.add_record([next_id, race_id, sex_id, r["VariationID"], r["GeosetID"], r["Showscalp"]])
        next_id += 1
    for r in facial:
        geo = [g if g >= 0 else 0 for g in (list(r["Geoset"]) + [0] * 5)[:5]]
        fh.add_record([race_id, sex_id, r["VariationID"], *geo])
    os.makedirs(out_dir, exist_ok=True)
    cs.save(os.path.join(out_dir, "CharSections.dbc"))
    hg.save(os.path.join(out_dir, "CharHairGeosets.dbc"))
    fh.save(os.path.join(out_dir, "CharacterFacialHairStyles.dbc"))
    return {"CharSections": len(rows), "CharHairGeosets": len(hair), "CharacterFacialHairStyles": len(facial)}
