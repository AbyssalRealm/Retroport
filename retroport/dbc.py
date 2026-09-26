"""WDBC (3.3.5a DBC) reader/writer and client-registration helpers.

The engine and the CreatureModelData / CreatureDisplayInfo row layouts are
adapted from Conjure (MIT, TheDaveCalaz): strict header validation, a
field-count check that refuses the wrong DBC or client build, ID allocation
that never collides, and every write re-read and verified before it counts.
Inputs are never overwritten - patched DBCs are written, under their original
names (ready to pack), into a separate output folder.

``register_creature_model`` appends the two rows that make a converted creature
usable in-game and returns the new displayId plus AzerothCore SQL.
"""

from __future__ import annotations

import os
import struct

from pydantic import BaseModel

from .safeio import same_dir, write_verified
from .sql import build_repoint_sql_block

MAGIC = b"WDBC"
HEADER_FORMAT = "<4sIIII"
HEADER_SIZE = 20

CREATURE_MODEL_DATA_FIELDS = 28
CREATURE_DISPLAY_INFO_FIELDS = 16


class DBCError(ValueError):
    """A DBC that can't be read, or an edit refused, with an explanation."""


def float_to_u32(x: float) -> int:
    """Reinterpret an IEEE-754 float's bits as a uint32, for storing in a DBC field."""
    return struct.unpack("<I", struct.pack("<f", x))[0]


def u32_to_float(u: int) -> float:
    return struct.unpack("<f", struct.pack("<I", u & 0xFFFFFFFF))[0]


class DBCFile:
    """An in-memory WDBC: ``records`` is a list of rows, each ``field_count`` uint32s.
    String fields hold an offset into ``string_block``; offset 0 is the empty string."""

    def __init__(self, field_count: int, record_size: int | None = None):
        self.field_count = field_count
        self.record_size = record_size if record_size is not None else field_count * 4
        if self.record_size != self.field_count * 4:
            raise DBCError(f"record_size ({self.record_size}) != field_count * 4 "
                           f"({self.field_count * 4}) - unsupported DBC layout")
        self.records: list[list[int]] = []
        self.string_block = bytearray(b"\x00")
        self.path: str | None = None

    @classmethod
    def load(cls, path: str) -> "DBCFile":
        with open(path, "rb") as fh:
            data = fh.read()
        return cls.from_bytes(data, path)

    @classmethod
    def from_bytes(cls, data: bytes, path: str = "<memory>") -> "DBCFile":
        if len(data) < HEADER_SIZE:
            raise DBCError(f"{path}: file too small to be a DBC")
        magic, n_rec, n_fields, rec_size, str_size = struct.unpack_from(HEADER_FORMAT, data, 0)
        if magic != MAGIC:
            raise DBCError(f"{path}: not a WDBC file (magic {magic!r})")
        if n_fields == 0 or rec_size != n_fields * 4:
            raise DBCError(f"{path}: record_size ({rec_size}) != field_count * 4 ({n_fields * 4}) "
                           "- unsupported or corrupt DBC")
        expected = HEADER_SIZE + n_rec * rec_size + str_size
        if len(data) < expected:
            raise DBCError(f"{path}: truncated - header declares {expected} bytes, file has {len(data)}")
        obj = cls(n_fields, rec_size)
        fmt = f"<{n_fields}I"
        pos = HEADER_SIZE
        for _ in range(n_rec):
            obj.records.append(list(struct.unpack_from(fmt, data, pos)))
            pos += rec_size
        obj.string_block = bytearray(data[pos:pos + str_size]) or bytearray(b"\x00")
        obj.path = path
        return obj

    # --- strings ---
    def get_string(self, offset: int) -> str:
        if offset == 0:
            return ""
        if not 0 <= offset < len(self.string_block):
            raise DBCError(f"string offset {offset} out of range of the string block")
        end = self.string_block.index(b"\x00", offset)
        return self.string_block[offset:end].decode("latin-1")

    def add_string(self, s: str) -> int:
        """Append a string to the string block; return its offset. Never rewrites existing strings."""
        if not s:
            return 0
        offset = len(self.string_block)
        self.string_block += s.encode("latin-1") + b"\x00"
        return offset

    # --- records ---
    def add_record(self, fields: list[int]) -> None:
        fields = list(fields)
        if len(fields) != self.field_count:
            raise DBCError(f"record has {len(fields)} fields, expected {self.field_count}")
        self.records.append(fields)

    def find_by_id(self, record_id: int, id_field: int = 0) -> list[int] | None:
        for rec in self.records:
            if rec[id_field] == record_id:
                return rec
        return None

    def max_id(self, id_field: int = 0) -> int:
        return max((r[id_field] for r in self.records), default=0)

    def next_free_id(self, floor: int | None = None, id_field: int = 0) -> int:
        """``max_id + 1`` by default; with a floor, the first unused id at or above it."""
        if floor is None:
            return self.max_id(id_field) + 1
        used = {r[id_field] for r in self.records}
        candidate = floor
        while candidate in used:
            candidate += 1
        return candidate

    # --- output ---
    def to_bytes(self) -> bytes:
        header = struct.pack(HEADER_FORMAT, MAGIC, len(self.records), self.field_count,
                             self.record_size, len(self.string_block))
        fmt = f"<{self.field_count}I"
        body = bytearray()
        for rec in self.records:
            if len(rec) != self.field_count:
                raise DBCError("internal error: record field count mismatch before write")
            body += struct.pack(fmt, *[v & 0xFFFFFFFF for v in rec])
        return header + bytes(body) + bytes(self.string_block)

    def save(self, path: str) -> str:
        """Write atomically; the file is re-parsed and checked before it replaces ``path``."""
        return write_verified(path, self.to_bytes(), verify_dbc)


def verify_dbc(path: str) -> None:
    """Re-read a written DBC and assert its header is internally consistent."""
    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) < HEADER_SIZE or data[:4] != MAGIC:
        raise DBCError(f"{path}: written file does not start with WDBC magic")
    _m, n_rec, n_fields, rec_size, str_size = struct.unpack_from(HEADER_FORMAT, data, 0)
    expected = HEADER_SIZE + n_rec * rec_size + str_size
    if len(data) != expected:
        raise DBCError(f"{path}: length {len(data)} != header-declared {expected} after write")
    DBCFile.from_bytes(data, path)


def _load_expecting(path: str, field_count: int, name: str) -> DBCFile:
    if not os.path.isfile(path):
        raise DBCError(f"{name} not found: {path}")
    dbc = DBCFile.load(path)
    if dbc.field_count != field_count:
        raise DBCError(f"{path}: expected {field_count} fields for {name}, found {dbc.field_count} "
                       "- wrong file, or not a 3.3.5a (12340) DBC")
    return dbc


def _refuse_overwrite(src_dir: str, out_dir: str) -> None:
    if same_dir(src_dir, out_dir):
        raise DBCError("output folder is the same as the input DBC folder - pick a separate "
                       "folder so the original DBCs are never overwritten")


def ingame_model_path(path: str) -> str:
    """Normalise and validate a client-relative model path (Creature\\Foo\\foo.m2)."""
    p = path.strip().replace("/", "\\")
    if not p or p.startswith("\\") or (len(p) > 1 and p[1] == ":"):
        raise DBCError(f"'{path}' is not an in-game path - use the path inside the MPQ, "
                       "e.g. Creature\\Foo\\foo.m2, not a location on your computer")
    if not p.lower().endswith((".m2", ".mdx")):
        raise DBCError(f"'{path}' must end in .m2 or .mdx")
    return p


# --------------------------------------------------------------------------
# creature registration
# --------------------------------------------------------------------------

class RegistrationResult(BaseModel):
    model_id: int
    display_id: int
    model_name: str
    model_dbc: str
    display_dbc: str
    sql: str


def register_creature_model(dbc_dir: str, out_dir: str, ingame_m2_path: str,
                            scale: float = 1.0, *,
                            display_floor: int | None = None,
                            modeldata_floor: int | None = None,
                            texture_variations: tuple[str, str, str] = ("", "", ""),
                            geoset_data: int = 0,
                            bounds: tuple[tuple[float, float, float], tuple[float, float, float]] | None = None,
                            creature_entries: str = "",
                            search_term: str = "") -> RegistrationResult:
    """Append CreatureModelData + CreatureDisplayInfo rows for a converted model.

    Writes patched ``CreatureModelData.dbc`` / ``CreatureDisplayInfo.dbc`` into
    ``out_dir`` (which must differ from ``dbc_dir``). IDs are the next free id
    (``max + 1``), or the first free id at/above a floor if one is given.
    ``texture_variations`` are bare names for creature models whose texture slots
    are type 11/12/13. ``bounds`` (from the converted .m2) fills the GeoBox.
    ``creature_entries`` (comma-separated) turns the SQL into a full, safe
    repoint block for AzerothCore's ``creature_template_model``.
    """
    for label, value in zip(("TextureVariation1", "TextureVariation2", "TextureVariation3"),
                            texture_variations):
        if value and any(c in value for c in "\\/:"):
            raise DBCError(f"{label} should be a bare name (e.g. sl_skin), not a path - got '{value}'")
    model_path = ingame_model_path(ingame_m2_path)
    _refuse_overwrite(dbc_dir, out_dir)

    md_in = os.path.join(dbc_dir, "CreatureModelData.dbc")
    di_in = os.path.join(dbc_dir, "CreatureDisplayInfo.dbc")
    md = _load_expecting(md_in, CREATURE_MODEL_DATA_FIELDS, "CreatureModelData.dbc")
    di = _load_expecting(di_in, CREATURE_DISPLAY_INFO_FIELDS, "CreatureDisplayInfo.dbc")

    model_id = md.next_free_id(modeldata_floor)
    display_id = di.next_free_id(display_floor)

    (bx0, by0, bz0), (bx1, by1, bz1) = bounds or ((-1.0, -1.0, 0.0), (1.0, 1.0, 2.5))
    F = float_to_u32
    md.add_record([
        model_id,                   # [0] ID
        0,                          # [1] Flags
        md.add_string(model_path),  # [2] ModelName
        1,                          # [3] SizeClass
        F(scale),                   # [4] ModelScale
        1,                          # [5] BloodID
        0,                          # [6] FootprintTextureID
        F(12.0), F(10.0),           # [7-8] FootprintTextureLength / Width
        F(1.0),                     # [9] FootprintParticleScale
        0, 0, 0, 0,                 # [10-13] FoleyMaterialID, FootstepShake, DeathThudShake, SoundID
        F(0.6), F(2.2),             # [14-15] CollisionWidth / Height
        F(1.87),                    # [16] MountHeight
        F(bx0), F(by0), F(bz0),     # [17-19] GeoBoxMin
        F(bx1), F(by1), F(bz1),     # [20-22] GeoBoxMax
        F(1.0), F(1.0),             # [23-24] WorldEffectScale / AttachedEffectScale
        0, 0, 0,                    # [25-27] MissileCollision radius / push / raise
    ])

    tv1, tv2, tv3 = texture_variations
    di.add_record([
        display_id,                 # [0] ID
        model_id,                   # [1] ModelID -> CreatureModelData
        0, 0,                       # [2-3] SoundID, ExtendedDisplayInfoID
        F(1.0),                     # [4] CreatureModelScale
        255,                        # [5] CreatureModelAlpha (0 = invisible)
        di.add_string(tv1),         # [6] TextureVariation1
        di.add_string(tv2),         # [7] TextureVariation2
        di.add_string(tv3),         # [8] TextureVariation3
        0,                          # [9] PortraitTextureName
        1, 0, 0, 0,                 # [10-13] BloodLevel, BloodID, NPCSoundID, ParticleColorID
        geoset_data & 0xFFFFFFFF,   # [14] CreatureGeosetData
        0,                          # [15] ObjectEffectPackageID
    ])

    os.makedirs(out_dir, exist_ok=True)
    md_out = md.save(os.path.join(out_dir, "CreatureModelData.dbc"))
    di_out = di.save(os.path.join(out_dir, "CreatureDisplayInfo.dbc"))

    # the DisplayInfo -> ModelData -> ModelName chain must resolve in what was written
    check_md, check_di = DBCFile.load(md_out), DBCFile.load(di_out)
    md_rec, di_rec = check_md.find_by_id(model_id), check_di.find_by_id(display_id)
    if md_rec is None or di_rec is None or di_rec[1] != model_id:
        raise DBCError("post-write verification failed: DisplayInfo -> ModelData chain does not resolve")
    resolved = check_md.get_string(md_rec[2])

    sql = [f"-- register converted model {resolved}  (DisplayID {display_id} -> ModelData {model_id})",
           "INSERT INTO `creature_model_info` (`DisplayID`,`BoundingRadius`,`CombatReach`,`Gender`)",
           f"  VALUES ({display_id}, 0.3, 1.5, 2);", ""]
    if creature_entries.strip():
        sql.append(build_repoint_sql_block(search_term, creature_entries, display_id))
    else:
        sql += ["-- to use it (check the old display isn't shared with other creatures first):",
                f"-- UPDATE creature_template_model SET CreatureDisplayID = {display_id} WHERE CreatureID = <entry>;"]
    return RegistrationResult(model_id=model_id, display_id=display_id, model_name=resolved,
                              model_dbc=md_out, display_dbc=di_out, sql="\n".join(sql))


def set_texture_variations(display_info_path: str, out_dir: str, display_id: int,
                           tex1: str, tex2: str, tex3: str, geoset: int = 0) -> dict:
    """Point an existing CreatureDisplayInfo row at three TextureVariation names
    (community-port mode: textures fed via DBC instead of baked into the .m2)."""
    for label, value in (("TextureVariation1", tex1), ("TextureVariation2", tex2), ("TextureVariation3", tex3)):
        if value and any(c in value for c in "\\/:"):
            raise DBCError(f"{label} should be a bare name (e.g. sl_skin), not a path - got '{value}'")
    _refuse_overwrite(os.path.dirname(os.path.abspath(display_info_path)), out_dir)
    di = _load_expecting(display_info_path, CREATURE_DISPLAY_INFO_FIELDS, "CreatureDisplayInfo.dbc")
    rec = di.find_by_id(display_id)
    if rec is None:
        raise DBCError(f"DisplayID {display_id} was not found in {os.path.basename(display_info_path)}")
    rec[6], rec[7], rec[8] = di.add_string(tex1), di.add_string(tex2), di.add_string(tex3)
    rec[14] = geoset & 0xFFFFFFFF

    os.makedirs(out_dir, exist_ok=True)
    out = di.save(os.path.join(out_dir, "CreatureDisplayInfo.dbc"))
    check = DBCFile.load(out).find_by_id(display_id)
    if check is None:
        raise DBCError("post-write verification failed: DisplayID row went missing")
    return {"output_path": out, "display_id": display_id,
            "TextureVariation1": tex1, "TextureVariation2": tex2, "TextureVariation3": tex3,
            "CreatureGeosetData": check[14]}
