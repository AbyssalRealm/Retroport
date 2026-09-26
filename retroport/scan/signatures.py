"""Binary signatures and lookup tables used by the scanner.

Two chunk-naming conventions exist in WoW formats and the scanner must respect
both:

* **M2 family** (m2, .skel, chunked .anim): chunk magics are stored *as written*
  in the file. ``AFID`` reads as ``AFID``.
* **RIFF-like family** (wmo, adt, wdt): chunk magics are stored *reversed*.
  ``MVER`` is written ``REVM`` on disk, ``MOHD`` is written ``DHOM``.

So when walking RIFF-like files we reverse each 4-byte magic before comparing;
for M2-family files we do not.
"""

from __future__ import annotations

# --- file-head magics (first 4 bytes) ---------------------------------------
MAGIC_BLP2 = b"BLP2"
MAGIC_BLP1 = b"BLP1"
MAGIC_MD20 = b"MD20"          # pre-Legion, non-chunked M2
MAGIC_MD21 = b"MD21"          # Legion+, chunked M2
MAGIC_REVM = b"REVM"          # "MVER" reversed -> WMO/ADT/WDT
MAGIC_SKL1 = b"SKL1"          # .skel header
MAGIC_AFM2 = b"AFM2"          # chunked .anim header
MAGIC_SKIN = b"SKIN"          # WotLK-era .skin header

# M2 chunk magics (not reversed on disk). Used both to recognise a chunked M2
# when MD21 is not the first chunk, and to flag .skel / FileDataID usage.
M2_CHUNK_MAGICS: frozenset[bytes] = frozenset({
    b"MD21", b"PFID", b"SFID", b"AFID", b"BFID", b"SKID", b"TXID",
    b"PABC", b"PADC", b"PSBC", b"PEDC", b"EXPT", b"EXP2", b"TXAC",
    b"LDV1", b"RPID", b"GPID", b"SKL1",
})

# chunks (M2 convention) that mean the model points at external FileDataIDs
M2_FDID_CHUNKS: frozenset[bytes] = frozenset({b"TXID", b"SFID", b"AFID", b"BFID"})

# RIFF-family disambiguation (compared after reversing the on-disk magic)
WMO_ROOT_CHUNKS = frozenset({b"MOHD"})
WMO_GROUP_CHUNKS = frozenset({b"MOGP"})
WDT_CHUNKS = frozenset({b"MPHD", b"MAIN"})
ADT_CHUNKS = frozenset({b"MHDR", b"MCNK", b"MCIN", b"MTEX", b"MDID", b"MHID",
                        b"MDDF", b"MODF", b"MAMP"})
ADT_FDID_CHUNKS = frozenset({b"MDID", b"MHID"})

WOTLK_M2_VERSION = 264


def expansion_for_m2_version(v: int) -> str:
    """Coarse expansion label from the M2 version word.

    The version word is a weak signal past Warlords of Draenor: Legion through
    The War Within mostly sit at 272-274 while the real differences are carried
    by chunking (MD21) and the .skel split. For anything reporting >= 274,
    refine using ``chunked`` and ``uses_skel`` rather than trusting this label.
    """
    if v <= 257:
        return "Classic"
    if 260 <= v <= 263:
        return "TBC"
    if v == WOTLK_M2_VERSION:
        return "WotLK"
    if 265 <= v <= 271:
        return "Cataclysm"
    if v == 272:
        return "Mists of Pandaria"
    if v == 273:
        return "Warlords of Draenor"
    if v >= 274:
        return "Legion+ (Legion-TWW)"
    return f"Unknown (v{v})"
