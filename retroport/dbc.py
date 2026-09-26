"""WDBC (3.3.5a DBC) reader/writer and client-registration helpers.

The DBC class is adapted from the Race Builder's dbc_manager - a generic engine
that reads a .dbc, exposes records + the string block, and writes it back. On top
of it, ``register_creature_model`` adds the CreatureModelData + CreatureDisplayInfo
entries that make a converted creature usable in-game, and returns the new
displayId to reference from creature_template.

Field layouts for CreatureModelData / CreatureDisplayInfo follow the offsets the
Race Builder established (modelPath at field 2, scale at field 4; display modelId
at field 1).
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass

_FLOAT_ONE = struct.unpack("<I", struct.pack("<f", 1.0))[0]


class DBC:
    HEADER_FMT = "<4sIIII"

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.records: list[bytes] = []
        self.string_block = b""
        self._load()

    def _load(self) -> None:
        with open(self.filepath, "rb") as fh:
            header = fh.read(20)
            (self.signature, self.record_count, self.field_count,
             self.record_size, self.string_block_size) = struct.unpack(self.HEADER_FMT, header)
            if self.signature != b"WDBC":
                raise ValueError(f"invalid DBC signature in {self.filepath}")
            for _ in range(self.record_count):
                self.records.append(fh.read(self.record_size))
            self.string_block = fh.read(self.string_block_size)

    def save(self, output_dir: str) -> str:
        os.makedirs(output_dir, exist_ok=True)
        out_path = os.path.join(output_dir, os.path.basename(self.filepath))
        with open(out_path, "wb") as fh:
            fh.write(struct.pack(self.HEADER_FMT, self.signature, self.record_count,
                                 self.field_count, self.record_size, self.string_block_size))
            for rec in self.records:
                fh.write(rec)
            fh.write(self.string_block)
        return out_path

    def unpack(self, record: bytes) -> list[int]:
        return list(struct.unpack(f"<{self.field_count}I", record))

    def pack(self, fields: list[int]) -> bytes:
        return struct.pack(f"<{self.field_count}I", *fields)

    def append_string(self, text: str) -> int:
        if not text:
            return 0
        offset = len(self.string_block)
        self.string_block += text.encode("latin-1") + b"\x00"
        self.string_block_size = len(self.string_block)
        return offset

    def get_string(self, offset: int) -> str:
        end = self.string_block.index(b"\x00", offset)
        return self.string_block[offset:end].decode("latin-1")

    def max_id(self, id_field: int = 0) -> int:
        return max((self.unpack(r)[id_field] for r in self.records), default=0)

    def add_record(self, fields: list[int]) -> None:
        self.records.append(self.pack(fields))
        self.record_count += 1


@dataclass
class RegistrationResult:
    model_id: int
    display_id: int
    model_dbc: str
    display_dbc: str
    sql: str


def register_creature_model(dbc_dir: str, out_dir: str, ingame_m2_path: str,
                            scale: float = 1.0) -> RegistrationResult:
    """Append CreatureModelData + CreatureDisplayInfo rows for a converted model.

    ``ingame_m2_path`` is the client-relative path (e.g. Creature\\Foo\\foo.m2).
    Writes patched DBCs into ``out_dir`` and returns the new displayId to put in
    creature_template.modelid.
    """
    cmd = DBC(os.path.join(dbc_dir, "CreatureModelData.dbc"))
    cdi = DBC(os.path.join(dbc_dir, "CreatureDisplayInfo.dbc"))

    model_id = cmd.max_id() + 1
    path_off = cmd.append_string(ingame_m2_path.replace("/", "\\"))
    scale_bits = struct.unpack("<I", struct.pack("<f", scale))[0]
    cmd_row = [0] * cmd.field_count
    cmd_row[0] = model_id          # id
    cmd_row[2] = path_off          # modelName (string offset)
    if cmd.field_count > 4:
        cmd_row[4] = scale_bits     # modelScale
    cmd.add_record(cmd_row)

    display_id = cdi.max_id() + 1
    cdi_row = [0] * cdi.field_count
    cdi_row[0] = display_id         # id
    cdi_row[1] = model_id           # modelId -> CreatureModelData
    if cdi.field_count > 4:
        cdi_row[4] = _FLOAT_ONE     # creatureModelScale
    cdi.add_record(cdi_row)

    cmd_path = cmd.save(out_dir)
    cdi_path = cdi.save(out_dir)

    sql = (
        f"-- register converted model {os.path.basename(ingame_m2_path)}\n"
        f"INSERT INTO `creature_model_info` (`DisplayID`,`BoundingRadius`,`CombatReach`,`Gender`)\n"
        f"  VALUES ({display_id}, 0.3, 1.5, 2);\n"
        f"-- set a creature to use it:  UPDATE creature_template SET modelid1={display_id} WHERE entry=<ENTRY>;\n"
    )
    return RegistrationResult(model_id, display_id, cmd_path, cdi_path, sql)
