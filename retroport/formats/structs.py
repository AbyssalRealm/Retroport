"""Binary layout constants and helpers for M2 / .skel structures.

All offsets and sizes are for the Wrath+ field ordering, which is also the
layout used *inside* the MD21 data block of Legion-TWW models (the MD21 content
"literally is a pre-legion file starting with MD20"). Sources: wowdev.wiki/M2
and wowdev.wiki/M2/.skel, cross-checked against an independent M2 binary parser.

Centralising every magic number here means a layout correction is a one-line
edit, never a hunt through the relocation logic.
"""

from __future__ import annotations

import struct

# --- record sizes (bytes) ---------------------------------------------------
M2ARRAY = 8          # uint32 size + uint32 offset
M2TRACK = 20         # interp(2) + global_seq(2) + M2Array timestamps(8) + M2Array values(8)
M2COMPBONE = 88      # 0x58; header 16 + translation/rotation/scale tracks (20*3) + pivot C3Vector(12)
M2ATTACHMENT = 40    # 0x28; id(4)+bone(2)+unk(2)+pos C3Vector(12) + M2Track<uint8>(20)
M2SEQUENCE = 64      # 0x40 (Legion+, duration form); opaque except for the flags field
M2SEQUENCE_FLAGS = 0x0C   # uint32 flags within an M2Sequence
SEQ_FLAG_INTERNAL_0x20 = 0x20  # data stored internally; client won't parse inline bone blocks without it
M2LOOP = 4           # uint32 timestamp; opaque

# --- MD20 header field offsets (absolute within the MD21 data block) ---------
# Only the fields the re-inliner reads or rewrites are listed.
MD20_VERSION = 0x04
HDR_NAME = 0x08            # M2Array<char>
HDR_GLOBAL_FLAGS = 0x10   # uint32
HDR_GLOBAL_LOOPS = 0x14   # M2Array<M2Loop>
HDR_SEQUENCES = 0x1C      # M2Array<M2Sequence>
HDR_SEQUENCE_LOOKUPS = 0x24  # M2Array<uint16>  (sequenceIdxHashById)
HDR_BONES = 0x2C          # M2Array<M2CompBone>
HDR_KEY_BONE_LOOKUP = 0x34   # M2Array<uint16>  (boneIndicesById)
HDR_ATTACHMENTS = 0xF0    # M2Array<M2Attachment>
HDR_ATTACHMENT_LOOKUP = 0xF8  # M2Array<uint16>  (attachmentIndicesById)

# additional MD20 header fields read by the WotLK compatibility checker
HDR_VERTICES = 0x3C            # M2Array<M2Vertex>
HDR_NUM_SKIN_PROFILES = 0x44   # uint32 nViews (how many .skin files the client loads)
HDR_TEXTURES = 0x50            # M2Array<M2Texture>
HDR_MATERIALS = 0x70           # M2Array<M2Material>
HDR_TEXTURE_COMBINER_COMBOS = 0x130  # M2Array<uint16>, present only if FLAG_USE_COMBINER_COMBOS

FLAG_USE_COMBINER_COMBOS = 0x08      # global_flags: M2Batch.shader is an index into the combiner-combos table

# M2Texture record (in the MD20 data block)
M2TEXTURE = 16                 # type(4) + flags(4) + filename M2Array(8)
TEXTURE_TYPE = 0x00            # uint32: 0 = hardcoded path; !=0 = filled at runtime (body/hair/etc.)
TEXTURE_FILENAME = 0x08        # M2Array<char>

# WotLK .skin (M2SkinProfile) - magic present, offsets relative to file start
SKIN_MAGIC = b"SKIN"
SKIN_VERTICES = 0x04           # M2Array<uint16> local->global vertex lookup
SKIN_INDICES = 0x0C            # M2Array<uint16> triangle corner indices (3 per triangle)
SKIN_SUBMESHES = 0x1C          # M2Array<M2SkinSection>
SKIN_BATCHES = 0x24            # M2Array<M2Batch>
SKIN_BONE_COUNT_MAX = 0x2C     # uint32
M2SKINSECTION = 48             # 0x30
M2BATCH = 24                   # 0x18
SKINSECTION_ID = 0x00          # uint16 skinSectionId (mesh part / geoset)
BATCH_SHADER = 0x02            # uint16 shader (a.k.a. shader_id)
BATCH_TEXTURE_COUNT = 0x0E     # uint16

# global_flags bits relevant to animation storage (wowdev.wiki/M2)
FLAG_CHUNKED_ANIM_0x2000 = 0x2000        # "seen in various legion models"
FLAG_UPGRADED_ANIM_0x200000 = 0x200000   # chunked .anim files (24500+ exporter)

# --- M2Track internal field offsets (within a 20-byte track) ----------------
TRACK_TIMESTAMPS = 0x04   # M2Array<M2Array<uint32>>
TRACK_VALUES = 0x0C       # M2Array<M2Array<T>>

# --- M2CompBone internal track offsets (within an 88-byte bone) -------------
BONE_TRANSLATION = 0x10
BONE_ROTATION = 0x24
BONE_SCALE = 0x38

# --- M2Attachment internal track offset (within a 40-byte attachment) -------
ATTACH_TRACK = 0x14

# --- .skel chunk header sizes (bytes before the raw data region) ------------
SKL1_HEADER = 16   # flags(4) + M2Array name(8) + pad(4)
SKA1_HEADER = 16   # M2Array attachments(8) + M2Array attachment_lookup(8)
SKB1_HEADER = 16   # M2Array bones(8) + M2Array key_bone_lookup(8)
SKS1_HEADER = 32   # M2Array global_loops(8) + sequences(8) + sequence_lookups(8) + pad(8)
SKPD_SIZE = 16     # pad(8) + parent_skel_file_id(4) + pad(4)

# .skel chunk header field offsets (within chunk content)
SKL1_NAME = 0x04
SKA1_ATTACHMENTS = 0x00
SKA1_ATTACH_LOOKUP = 0x08
SKB1_BONES = 0x00
SKB1_KEY_BONE_LOOKUP = 0x08
SKS1_GLOBAL_LOOPS = 0x00
SKS1_SEQUENCES = 0x08
SKS1_SEQUENCE_LOOKUPS = 0x10
SKPD_PARENT_FILE_ID = 0x08


# --- helpers ----------------------------------------------------------------
def read_u32(buf: bytes, off: int) -> int:
    return struct.unpack_from("<I", buf, off)[0]


def read_m2array(buf: bytes, off: int) -> tuple[int, int]:
    """Return (count, offset) of an M2Array at absolute position ``off``."""
    return struct.unpack_from("<II", buf, off)


def write_u32(buf: bytearray, off: int, value: int) -> None:
    struct.pack_into("<I", buf, off, value & 0xFFFFFFFF)


def write_m2array(buf: bytearray, off: int, count: int, offset: int) -> None:
    struct.pack_into("<II", buf, off, count & 0xFFFFFFFF, offset & 0xFFFFFFFF)
