"""Read-only access to a 3.3.5a client's files, resolved the way the client resolves them.

``MPQ`` is a minimal MPQ v1/v2 reader (hash and block tables, zlib/bzip2 sectors,
encrypted files). ``ClientData`` opens every archive of an install and answers
"which copy of this file does the client actually load?", using the archive
priority that WarcraftXL mounts (src/host/mpq/MpqStore.cpp), highest first:

1. loose override folders - ``Data\\patch*.MPQ`` that are directories, by name, descending;
2. custom patches ``Data\\patch-<token>.MPQ`` and ``Data\\<loc>\\patch-<loc>-<token>.MPQ``
   with token 4-999999 or letters, ranked numbers < letters (a < z), the locale archive
   first on a tie;
3. the stock chain: patch-<loc>-3, patch-3, patch-<loc>-2, patch-2, patch-<loc>, patch,
   then the locale and base archives.
"""

from __future__ import annotations

import bz2
import glob
import os
import struct
import zlib


def _crypt_table() -> list[int]:
    seed, tab = 0x00100001, [0] * 0x500
    for i in range(0x100):
        j = i
        for _ in range(5):
            seed = (seed * 125 + 3) % 0x2AAAAB
            hi = (seed & 0xFFFF) << 16
            seed = (seed * 125 + 3) % 0x2AAAAB
            tab[j] = hi | (seed & 0xFFFF)
            j += 0x100
    return tab


_CRYPT = _crypt_table()


def hash_string(s: str, kind: int) -> int:
    s1, s2 = 0x7FED7FED, 0xEEEEEEEE
    for ch in s.upper().replace("/", "\\").encode("latin-1"):
        s1 = (_CRYPT[(kind << 8) + ch] ^ (s1 + s2)) & 0xFFFFFFFF
        s2 = (ch + s1 + s2 + (s2 << 5) + 3) & 0xFFFFFFFF
    return s1


def _decrypt(data: bytes, key: int) -> bytes:
    n = len(data) // 4
    out = []
    s2 = 0xEEEEEEEE
    for v in struct.unpack(f"<{n}I", data[:n * 4]):
        s2 = (s2 + _CRYPT[0x400 + (key & 0xFF)]) & 0xFFFFFFFF
        d = (v ^ (key + s2)) & 0xFFFFFFFF
        out.append(d)
        key = ((((~key) << 0x15) + 0x11111111) | (key >> 0x0B)) & 0xFFFFFFFF
        s2 = (d + s2 + (s2 << 5) + 3) & 0xFFFFFFFF
    return struct.pack(f"<{n}I", *out) + data[n * 4:]


class MPQError(ValueError):
    pass


_EXISTS, _ENCRYPTED, _FIX_KEY, _SINGLE_UNIT, _SECTOR_CRC = 0x80000000, 0x10000, 0x20000, 0x01000000, 0x04000000
_COMPRESSED = 0x200 | 0x100


class MPQ:
    """One archive. ``read(name)`` returns the file's bytes or None."""

    def __init__(self, path: str):
        self.path = path
        self._fh = open(path, "rb")
        off = 0
        while True:
            self._fh.seek(off)
            magic = self._fh.read(4)
            if magic == b"MPQ\x1a":
                break
            if magic == b"MPQ\x1b":                          # user data header
                self._fh.seek(off + 8)
                off += struct.unpack("<I", self._fh.read(4))[0]
                continue
            off += 0x200
            if off > os.path.getsize(path):
                raise MPQError(f"{path}: no MPQ header")
        self.base = off
        self._fh.seek(off)
        head = self._fh.read(44)
        _m, _hs, _as, fmt, shift, ht_pos, bt_pos, ht_n, bt_n = struct.unpack_from("<4sIIHHIIII", head)
        ht_hi = bt_hi = 0
        if fmt >= 1:
            _hi_bt, ht_hi, bt_hi = struct.unpack_from("<QHH", head, 32)
        self.sector = 512 << shift
        self._fh.seek(off + (ht_hi << 32 | ht_pos))
        ht = _decrypt(self._fh.read(ht_n * 16), hash_string("(hash table)", 3))
        self._fh.seek(off + (bt_hi << 32 | bt_pos))
        bt = _decrypt(self._fh.read(bt_n * 16), hash_string("(block table)", 3))
        self._hashes = [struct.unpack_from("<IIHHI", ht, i * 16) for i in range(ht_n)]
        self._blocks = [struct.unpack_from("<IIII", bt, i * 16) for i in range(bt_n)]

    def close(self) -> None:
        self._fh.close()

    def _block(self, name: str):
        n = len(self._hashes)
        if not n:
            return None
        i = hash_string(name, 0) % n
        a, b = hash_string(name, 1), hash_string(name, 2)
        for _ in range(n):
            ha, hb, _loc, _plat, blk = self._hashes[i]
            if blk == 0xFFFFFFFF:
                return None
            if ha == a and hb == b and blk < len(self._blocks):
                block = self._blocks[blk]
                return block if block[3] & _EXISTS else None
            i = (i + 1) % n
        return None

    def has(self, name: str) -> bool:
        return self._block(name) is not None

    def read(self, name: str) -> bytes | None:
        block = self._block(name)
        if block is None:
            return None
        pos, csize, size, flags = block
        self._fh.seek(self.base + pos)
        raw = self._fh.read(csize)
        key = None
        if flags & _ENCRYPTED:
            key = hash_string(name.replace("/", "\\").split("\\")[-1], 3)
            if flags & _FIX_KEY:
                key = ((key + pos) ^ size) & 0xFFFFFFFF
        if flags & _SINGLE_UNIT:
            if key is not None:
                raw = _decrypt(raw, key)
            return self._decompress(raw, size) if flags & _COMPRESSED and csize < size else raw[:size]
        count = (size + self.sector - 1) // self.sector
        if not flags & _COMPRESSED:
            out = bytearray()
            for k in range(count):
                chunk = raw[k * self.sector:(k + 1) * self.sector]
                out += _decrypt(chunk, (key + k) & 0xFFFFFFFF) if key is not None else chunk
            return bytes(out[:size])
        table = raw[:(count + 1) * 4]
        if key is not None:
            table = _decrypt(table, (key - 1) & 0xFFFFFFFF)
        offsets = struct.unpack(f"<{count + 1}I", table)
        out = bytearray()
        for k in range(count):
            chunk = raw[offsets[k]:offsets[k + 1]]
            if key is not None:
                chunk = _decrypt(chunk, (key + k) & 0xFFFFFFFF)
            want = min(self.sector, size - k * self.sector)
            out += self._decompress(chunk, want) if len(chunk) < want else chunk
        if len(out) != size:
            raise MPQError(f"{self.path}:{name}: read {len(out)} bytes, expected {size}")
        return bytes(out)

    @staticmethod
    def _decompress(data: bytes, want: int) -> bytes:
        mask, body = data[0], data[1:]
        if mask & 0x10:
            body = bz2.decompress(body)
            mask &= ~0x10
        if mask & 0x02:
            body = zlib.decompress(body)
            mask &= ~0x02
        if mask:
            raise MPQError(f"unsupported MPQ compression {data[0]:#x}")
        return body


_STOCK_LOCALE_FIRST = ["patch-{loc}-3", "patch-3", "patch-{loc}-2", "patch-2", "patch-{loc}", "patch",
                       "locale-{loc}", "base-{loc}", "expansion-locale-{loc}", "lichking-locale-{loc}",
                       "speech-{loc}", "expansion-speech-{loc}", "lichking-speech-{loc}", "backup-{loc}",
                       "lichking", "expansion", "common-2", "common"]


def _patch_rank(token: str) -> int | None:
    token = token.lower()
    if token.isdigit():
        value = int(token)
        return value if 3 < value <= 999999 else None
    if token.isalpha() and token.isascii():
        value = 0
        for c in token:
            value = value * 26 + (ord(c) - ord("a") + 1)
        return 1_000_000 + value
    return None


class ClientData:
    """Every archive of a 3.3.5a install, in the order the client searches them."""

    def __init__(self, client_dir: str, locale: str | None = None):
        data = os.path.join(client_dir, "Data")
        if not os.path.isdir(data):
            raise MPQError(f"{client_dir} has no Data folder - point at the WoW 3.3.5a folder")
        if locale is None:
            locale = next((d for d in sorted(os.listdir(data))
                           if os.path.isfile(os.path.join(data, d, f"locale-{d}.MPQ"))), None)
        if not locale:
            raise MPQError(f"no Data\\<locale>\\locale-<locale>.MPQ in {client_dir}")
        self.locale = locale
        self.sources: list[tuple[str, object]] = []        # (label, MPQ | folder path)

        loose = sorted((d for d in os.listdir(data) if os.path.isdir(os.path.join(data, d))
                        and d.lower().startswith("patch") and d.lower().endswith(".mpq")),
                       key=str.lower, reverse=True)
        for d in loose:
            self.sources.append((f"Data\\{d} (folder)", os.path.join(data, d)))

        custom = []
        for rel_dir, prefix, is_locale in ((os.path.join(data, locale), f"patch-{locale}-", True),
                                           (data, "patch-", False)):
            for path in glob.glob(os.path.join(rel_dir, "*.mpq")) + glob.glob(os.path.join(rel_dir, "*.MPQ")):
                name = os.path.basename(path)
                if not os.path.isfile(path) or not name.lower().startswith(prefix.lower()):
                    continue
                rank = _patch_rank(name[len(prefix):-4])
                if rank is not None:
                    custom.append((rank, is_locale, path))
        seen = set()
        for _rank, _loc, path in sorted(custom, key=lambda t: (t[0], t[1], t[2].lower()), reverse=True):
            if path.lower() not in seen:
                seen.add(path.lower())
                self._open(path, data)
        for stem in _STOCK_LOCALE_FIRST:
            stem = stem.format(loc=locale)
            path = os.path.join(data, locale, stem + ".MPQ") if locale in stem else os.path.join(data, stem + ".MPQ")
            if os.path.isfile(path):
                self._open(path, data)

    def _open(self, path: str, data: str) -> None:
        self.sources.append((os.path.relpath(path, data), MPQ(path)))

    def find(self, name: str) -> tuple[str, bytes] | None:
        """(source label, bytes) of the copy the client loads, or None."""
        rel = name.replace("/", "\\")
        for label, src in self.sources:
            if isinstance(src, str):
                p = os.path.join(src, *rel.split("\\"))
                if os.path.isfile(p):
                    with open(p, "rb") as fh:
                        return label, fh.read()
                # folder patches on Windows are case-insensitive already
            else:
                data = src.read(rel)
                if data is not None:
                    return label, data
        return None

    def read(self, name: str) -> bytes:
        hit = self.find(name)
        if hit is None:
            raise MPQError(f"{name} is not in any archive of this client")
        return hit[1]

    def close(self) -> None:
        for _label, src in self.sources:
            if isinstance(src, MPQ):
                src.close()
