"""Character create and select laid out as Battle for Azeroth's, for every race (`retroport glue`).

BfA 8.3's character create (GlueXML CharacterCreate.xml/.lua, art Blizzard called "Patchwerk") is
rebuilt for the 3.3.5a glue API in ``ui/``: a gender box with the Alliance and Horde race columns
hanging from it on their banners, races 3.3.5 never had on an "Allied Races" page, a class column
down the right edge, race and class info behind "More Info", then a customization step on a
faction banner where the camera glides to the character's head (``zoom.py``), the character
standing in BfA's faction scene (ChrRaces.CreateScreenFileDataID: UI_Alliance for every Alliance
race, UI_Horde for every Horde one). Character select is 3.3.5's own screen - BfA grew from it -
with BfA's character rows and button places.

The package holds:

* ``Interface\\GlueXML\\CharacterCreate.xml`` / ``.lua`` - the new create screen. BfA's XML leans
  on atlases, relativeKey, KeyValues and mixins 3.3.5 doesn't have; the rebuild uses BfA's sizes,
  offsets and texture coordinates in 3.3.5's dialect, its atlas pieces resolved here from
  UiTextureAtlasMember (``@ATLAS:name@`` / ``@COORDS:name@`` in the XML). Race icon blocks a race
  package added to the client's CharacterCreate.lua are carried over, so an installed race keeps
  its icon until its package is rebuilt on the new file. Every race's icon comes from BfA's atlas
  too (``RaceIcons.blp``, sharp), and the zoom's settings and the heads of the races 3.3.5 shipped
  are written at its end.
* ``Interface\\GlueXML\\CharacterSelect.xml`` / ``.lua`` - the new select screen.
* ``Interface\\GlueXML\\GlueParent.lua`` - the client's own, with music for the two scenes.
* BfA's art the client lacks (``Interface\\Glues\\CharacterCreate\\``, the select screen's hover
  glow and service icons), the atlas textures (``...\\Retroport\\Atlas<FileDataID>.blp``), and the
  two create scenes converted with wotlkconv (``Interface\\Glues\\Models\\UI_Alliance\\``,
  ``UI_Horde\\``) - the zoom's cameras added - with the textures they name that the client lacks.

``package.json`` records what the GlueXML files were built on and the folders the package owns,
for ``retroport install``.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import struct
from typing import Callable

from PIL import Image
from pydantic import BaseModel

from ..character.convert import BfaInstall, _find_listfile_paths
from ..convert import engine
from ..formats import blp
from ..mpq import ClientData
from ..race import glue as raceglue
from ..race import icons as raceicons
from ..safeio import write_verified
from . import scenes, zoom

UI = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ui")
GLUE = "Interface\\GlueXML\\"
ART = "Interface\\Glues\\CharacterCreate\\"
ATLAS_DIR = ART + "Retroport\\"
MODELS = "Interface\\Glues\\Models\\"

# BfA art 3.3.5 never had, copied as it is
ART_FILES = ("UI-CharacterCreatePatchwerk", "Frame-Sides", "ButtonFrame-Mid")
# and the select screen's: a hovered character's glow, the paid services' icons
SELECT_ART = ("Interface\\Glues\\CharacterSelect\\Glue-CharacterSelect-Hover", "Interface\\Icons\\VAS_AppearanceChange",
              "Interface\\Icons\\VAS_RaceChange", "Interface\\Icons\\VAS_FactionChange")
# the screens: their XML's atlas pieces are filled in, their Lua copied
SCREENS = ("CharacterCreate", "CharacterSelect")
# BfA's create scene per faction (ChrRaces.CreateScreenFileDataID)
SCENES = {"Alliance": "interface/glues/models/ui_alliance/ui_alliance.m2",
          "Horde": "interface/glues/models/ui_horde/ui_horde.m2"}
# 3.3.5's own music for the scenes, until BfA's is ported
SCENE_MUSIC = {"ALLIANCE": "HUMAN", "HORDE": "ORC", "GNOME": "DWARF", "TROLL": "ORC"}
SELECT_FOG = {"SCOURGE": (0, 0.22, 0.22, 18)}      # BfA's CHAR_MODEL_FOG_INFO: r, g, b, far
# the races 3.3.5 shipped (their ClientFileString): the customize zoom gets their heads from the
# client's own models; a race package adds its own
CORE_RACES = ("Human", "Dwarf", "NightElf", "Gnome", "Draenei", "Orc", "Scourge", "Tauren", "Troll", "BloodElf")
# the customize zoom a bit too close for these (client test: dwarves, gnomes, the Horde's men): a
# view zoom.WIDTHS[1] times as wide
ZOOM_WIDE = {f"{race}_{sex}": 1 for race, sex in (
    ("DWARF", "MALE"), ("DWARF", "FEMALE"), ("GNOME", "MALE"), ("GNOME", "FEMALE"), ("ORC", "MALE"),
    ("SCOURGE", "MALE"), ("TAUREN", "MALE"), ("TROLL", "MALE"), ("BLOODELF", "MALE"))}
# BfA's race icons (raceicon-<race>-<sex>, 64 pixels) upscaled to ICON_CELL in one uncompressed
# atlas: the buttons are ~80 pixels at 1440p, where 3.3.5's 64-pixel DXT icons went soft and blocky
RACE_ICONS = ATLAS_DIR + "RaceIcons"
ICON_CELL, ICON_COLUMNS = 128, 8
# the ClientFileStrings BfA's GetRaceAtlas shortens
ATLAS_RACE_NAMES = {"undead": "SCOURGE", "highmountain": "HIGHMOUNTAINTAUREN",
                    "lightforged": "LIGHTFORGEDDRAENEI", "zandalari": "ZANDALARITROLL"}
_RACE_ICON = re.compile(r"raceicon-([a-z]+)-(male|female)$")

_PLACEHOLDER = re.compile(r"@(ATLAS|COORDS|COORDS_H|COORDS_V|COORDS_HV):([\w-]+)@")
_ICON_BLOCK = re.compile(r"[ \t]*-- \[retroport\] begin (icons [^\r\n]+)\r?\n.*?-- \[retroport\] end \1[ \t]*\r?\n?", re.S)


class GlueError(ValueError):
    """The glue package can't be built as asked."""


class GlueBuildResult(BaseModel):
    name: str = "BfA character create and select"
    kind: str = "glue"
    bases: dict[str, dict] = {}         # client/... -> {source, sha256}: modified files and their base
    owns: list[str] = []                # client-relative folders whose files are the package's
    files: list[str] = []
    atlas: dict[str, list] = {}         # atlas member -> [file, left, right, top, bottom]
    scenes: dict[str, str] = {}         # create: faction -> its scene
    select_scenes: dict[str, str] = {}  # select: race (ClientFileString) -> its scene
    race_icons: list[str] = []          # RACE_SEX of every icon in the race icon atlas
    zoom_cameras: dict[str, int] = {}   # scene -> cameras it has with the zoom's
    zoom_heads: dict[str, list[float]] = {}   # RACE_SEX -> [head height, forward]
    warnings: list[str] = []


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# --------------------------------------------------------------------------- the XML template

def atlas_coords(member: dict, size: tuple[int, int]) -> tuple[float, float, float, float]:
    """(left, right, top, bottom) of an atlas member in its texture, 0..1."""
    w, h = size
    return (member["CommittedLeft"] / w, member["CommittedRight"] / w,
            member["CommittedTop"] / h, member["CommittedBottom"] / h)


def fill_atlas(xml: str, atlas: dict[str, tuple[str, float, float, float, float]]) -> str:
    """Resolve ``@ATLAS:name@`` (the texture's path) and ``@COORDS[_H|_V|_HV]:name@`` (its
    TexCoords attributes, mirrored horizontally / vertically / both) for each atlas member."""
    def sub(m: re.Match) -> str:
        kind, name = m.group(1), m.group(2).lower()
        if name not in atlas:
            raise GlueError(f"atlas member {name} is not known")
        path, left, right, top, bottom = atlas[name]
        if kind == "ATLAS":
            return path
        if kind in ("COORDS_H", "COORDS_HV"):
            left, right = right, left
        if kind in ("COORDS_V", "COORDS_HV"):
            top, bottom = bottom, top
        return f'left="{left:.8f}" right="{right:.8f}" top="{top:.8f}" bottom="{bottom:.8f}"'
    return _PLACEHOLDER.sub(sub, xml)


def atlas_members(xml: str) -> list[str]:
    return sorted({m.group(2).lower() for m in _PLACEHOLDER.finditer(xml)})


def carry_race_icons(new_lua: str, client_lua: str) -> tuple[str, list[str]]:
    """``new_lua`` with every race icon block the client's CharacterCreate.lua holds appended
    (the blocks race packages write: ``-- [retroport] begin icons <RACE>``)."""
    blocks = [(m.group(1), m.group(0)) for m in _ICON_BLOCK.finditer(client_lua)]
    eol = "\r\n" if "\r\n" in new_lua else "\n"
    out = new_lua if new_lua.endswith(("\n", "\r")) else new_lua + eol
    for _tag, text in blocks:
        out += text.replace("\r\n", "\n").replace("\n", eol)
        if not out.endswith(eol):
            out += eol
    return out, [tag for tag, _t in blocks]


def patch_glue_parent(lua: str) -> str:
    """Music for BfA's two create scenes and its Gnome and Troll select scenes (3.3.5's own tracks,
    by the race whose scene they stood in), and BfA's lighting for the select scenes: BfA lights
    them as they are (glueparent.lua ResetLighting / UpdateLighting: their own lights, glow 0.3, fog
    only for the Undead's), where 3.3.5's tables light its own scenes."""
    for table in ("GlueAmbienceTracks", "CharModelFogInfo", "CharModelGlowInfo", "RaceLights"):
        if table not in lua:
            raise GlueError(f"{table} not found in GlueParent.lua")
    lines = [f'GlueAmbienceTracks["{scene}"] = GlueAmbienceTracks["{scene}"] or GlueAmbienceTracks["{race}"];'
             for scene, race in SCENE_MUSIC.items()]
    lua = raceglue.put_block(lua, "bfa glue scenes", "\n".join(lines))
    races = ", ".join(f'"{r.upper()}"' for r in CORE_RACES)
    light = [f"for _, race in ipairs({{ {races} }}) do",
             "\tCharModelFogInfo[race], CharModelGlowInfo[race], RaceLights[race] = nil, nil, nil;",
             "end"]
    light += [f'CharModelFogInfo["{race}"] = {{ r = {r}, g = {g}, b = {b}, far = {far} }};'
              for race, (r, g, b, far) in SELECT_FOG.items()]
    return raceglue.put_block(lua, "bfa select lighting", "\n".join(light))


def race_icon_key(member: str) -> str | None:
    """``RACE_SEX`` (the screen's key: ClientFileString upper-cased) of a BfA race icon atlas
    member, or None."""
    m = _RACE_ICON.match(member.lower())
    if not m:
        return None
    race, sex = m.groups()
    return f"{ATLAS_RACE_NAMES.get(race, race.upper())}_{sex.upper()}"


def race_icon_atlas(icons: dict[str, Image.Image]) -> tuple[Image.Image, dict[str, tuple[float, float, float, float]]]:
    """Every icon upscaled to ICON_CELL (Lanczos) in one atlas ICON_COLUMNS wide and a power of two
    tall, and (left, right, top, bottom) of each."""
    keys = sorted(icons)
    rows = max(1, -(-len(keys) // ICON_COLUMNS))
    width = ICON_CELL * ICON_COLUMNS
    height = 1 << (rows * ICON_CELL - 1).bit_length()
    atlas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    coords = {}
    for i, key in enumerate(keys):
        col, row = i % ICON_COLUMNS, i // ICON_COLUMNS
        cell = icons[key].convert("RGBA").resize((ICON_CELL, ICON_CELL), Image.Resampling.LANCZOS)
        atlas.paste(cell, (col * ICON_CELL, row * ICON_CELL))
        coords[key] = (col * ICON_CELL / width, (col + 1) * ICON_CELL / width,
                       row * ICON_CELL / height, (row + 1) * ICON_CELL / height)
    return atlas, coords


def lua_race_icons(path: str, coords: dict[str, tuple[float, float, float, float]]) -> str:
    lines = [f"RACE_ICON_ATLAS_FILE = {raceglue.lua_string(path)};"]
    lines += [f'RACE_ICON_ATLAS["{k}"] = {{{", ".join(f"{v:.6f}" for v in c)}}};' for k, c in sorted(coords.items())]
    return "\n".join(lines)


def zoom_scene(data: bytes, name: str) -> tuple[bytes, int, int]:
    """(scene, its first zoom camera, cameras in all) - a converted create scene with the
    customize zoom's cameras and animations added (``glue/zoom.py``)."""
    from wotlkconv.m2.model import parse_md20
    from wotlkconv.m2.write import write_md20

    stage = zoom.scene_stage(data)
    model = parse_md20(data, name)
    first = zoom.add_zoom(model, stage)
    out = bytes(write_md20(model))
    check = parse_md20(out, name)
    if len(check.cameras) != first + len(zoom.grid()) or len(check.sequences) != 3:
        raise GlueError(f"{name}: the zoom did not survive writing the scene")
    return out, first, len(check.cameras)


def core_race_heads(read: Callable[[str], bytes | None], warnings: list[str]) -> dict[str, tuple[float, float]]:
    """The customize zoom's heads (``RACE_SEX`` -> (height, forward)) of the races 3.3.5 shipped,
    from the client's models."""
    heads = {}
    for race in CORE_RACES:
        for sex in ("Male", "Female"):
            stem = f"Character\\{race}\\{sex}\\{race}{sex}"
            m2, skin = read(stem + ".m2"), read(stem + "00.skin")
            if not m2 or not skin:
                warnings.append(f"customize zoom: {stem}.m2 or its skin is not in the client - {race} {sex} won't zoom")
                continue
            try:
                heads[f"{race.upper()}_{sex.upper()}"] = zoom.head_spot(m2, skin)
            except (zoom.ZoomError, struct.error) as e:
                warnings.append(f"customize zoom: {stem}: {e}")
    return heads


# --------------------------------------------------------------------------- the build

def build_glue(*, install_dir: str, client_dir: str, out_dir: str, listfile: str, dbd_dir: str,
               patch: str | None = "patch-H.MPQ", on_log: Callable[[str], None] | None = None) -> GlueBuildResult:
    log = on_log or (lambda m: None)
    if not engine.available():
        raise GlueError(engine.INSTALL_HINT)
    client_out = os.path.join(out_dir, "client")
    os.makedirs(client_out, exist_ok=True)
    res = GlueBuildResult()
    log(f"Reading {client_dir} ...")
    client = ClientData(client_dir, top=patch)
    log(f"Opening {install_dir} ...")
    bfa = BfaInstall(install_dir, dbd_dir, listfile)
    want = {f"dbfilesclient/{t}.db2" for t in ("uitextureatlas", "uitextureatlasmember")}
    want |= {f"{ART.lower().replace(chr(92), '/')}{f.lower()}.blp" for f in ART_FILES}
    want |= {f"{f.lower().replace(chr(92), '/')}.blp" for f in SELECT_ART}
    want |= set(SCENES.values()) | {"dbfilesclient/chrraces.db2"}
    fdids = _find_listfile_paths(listfile, want)
    bfa.fdids.update({k: v for k, v in fdids.items() if k.startswith("dbfilesclient/")})

    def write(rel: str, data: bytes, verify=None) -> str:
        path = write_verified(os.path.join(client_out, *rel.split("\\")), data, verify)
        res.files.append(path)
        return path

    def base(rel: str) -> bytes:
        hit = client.find(rel)
        if hit is None:
            raise GlueError(f"{rel} is not in any archive of the client")
        res.bases["client/" + rel.replace("\\", "/")] = {"source": hit[0], "sha256": sha256(hit[1])}
        return hit[1]

    # --- BfA's art ---
    for name in ART_FILES:
        key = f"{ART.lower().replace(chr(92), '/')}{name.lower()}.blp"
        if key not in fdids:
            raise GlueError(f"{key} is not in the listfile")
        write(ART + name + ".blp", bfa.read(fdids[key]), blp.verify_blp)
    for name in SELECT_ART:
        key = f"{name.lower().replace(chr(92), '/')}.blp"
        if key not in fdids:
            raise GlueError(f"{key} is not in the listfile")
        write(name + ".blp", bfa.read(fdids[key]), blp.verify_blp)
    log(f"  art: {', '.join(ART_FILES)}; select: {', '.join(n.rsplit(chr(92), 1)[1] for n in SELECT_ART)}")

    # --- atlas pieces ---
    xmls = {s: open(os.path.join(UI, s + ".xml"), encoding="utf-8").read() for s in SCREENS}
    atlases = {i: r for i, r in bfa.table("UiTextureAtlas")}
    members = {str(r["CommittedName"]).lower(): r for _i, r in bfa.table("UiTextureAtlasMember")}
    atlas: dict[str, tuple] = {}
    for name in sorted({n for x in xmls.values() for n in atlas_members(x)}):
        m = members.get(name)
        if m is None:
            raise GlueError(f"BfA has no atlas member {name}")
        a = atlases[m["UiTextureAtlasID"]]
        rel = f"{ATLAS_DIR}Atlas{a['FileDataID']}.blp"
        path = os.path.join(client_out, *rel.split("\\"))
        if path not in res.files:
            write(rel, bfa.read(a["FileDataID"]), blp.verify_blp)
        with open(path, "rb") as fh:
            h = blp.header(fh.read())
        coords = atlas_coords(m, (h["width"], h["height"]))
        atlas[name] = (rel[:-4],) + coords
        res.atlas[name] = list(atlas[name])
    log(f"  atlas: {len(atlas)} piece(s) from {len({v[0] for v in atlas.values()})} BfA atlas texture(s)")

    # --- race icons ---
    work = os.path.join(out_dir, "_work")
    os.makedirs(work, exist_ok=True)
    sheets: dict[int, Image.Image] = {}
    icon_cells = {}
    for name, m in members.items():
        key = race_icon_key(name)
        if key is None:
            continue
        fdid = atlases[m["UiTextureAtlasID"]]["FileDataID"]
        if fdid not in sheets:
            p = write_verified(os.path.join(work, f"atlas{fdid}.blp"), bfa.read(fdid))
            sheets[fdid] = blp.decode(p)
        icon_cells[key] = raceicons.crop_atlas_member(sheets[fdid], m)
    if not icon_cells:
        raise GlueError("BfA's atlas lists no race icons (raceicon-<race>-<sex>)")
    icon_sheet, icon_coords = race_icon_atlas(icon_cells)
    write(RACE_ICONS + ".blp", blp.encode_bgra(icon_sheet), blp.verify_blp)
    res.race_icons = sorted(icon_coords)
    log(f"  race icons: {len(icon_coords)} from BfA, {ICON_CELL} pixels, uncompressed")

    # --- the scenes: create's, one a faction (with the zoom), and select's, one a classic race ---
    data = {}
    for faction, key in SCENES.items():
        if key not in fdids:
            raise GlueError(f"{key} is not in the listfile")
        data[f"UI_{faction}"] = bfa.read(fdids[key])
    for cfs in CORE_RACES:
        fdid = scenes.select_scene_fdid(bfa, cfs)
        if not fdid:
            raise GlueError(f"BfA has no select scene for {cfs}")
        data[f"UI_{cfs}"] = bfa.read(fdid)
    lf = scenes.scoped_listfile(bfa, list(data.values()), work, listfile)
    first_camera: int | None = None
    for stem, model in data.items():
        try:
            scene = scenes.convert_scene(bfa, model, stem, client=client, client_out=client_out, work=work, lf=lf)
        except scenes.SceneError as e:
            raise GlueError(str(e)) from e
        res.files += [f for f in scene.files if f not in res.files]
        res.warnings += scene.warnings
        res.owns.append(scene.folder + "\\")
        name = stem[len("UI_"):]
        log(f"  scene {stem}: {scene.summary()}")
        if name not in SCENES:
            res.select_scenes[name] = scene.folder + "\\" + stem + ".m2"
            continue
        res.scenes[name] = scene.folder + "\\" + stem + ".m2"
        with open(scene.output, "rb") as fh:
            zoomed, first, cameras = zoom_scene(fh.read(), stem)
        write_verified(scene.output, zoomed)
        if first_camera is not None and first != first_camera:
            raise GlueError(f"{stem}: its zoom cameras start at {first}, the other scene's at {first_camera}")
        first_camera = first
        res.zoom_cameras[stem] = cameras
        log(f"  scene {stem}: {cameras - first} zoom camera(s) and the zoom animations added")
    shutil.rmtree(work, ignore_errors=True)

    # --- the screens ---
    heads = core_race_heads(lambda rel: (h := client.find(rel)) and h[1], res.warnings)
    res.zoom_heads = {k: [round(v[0], 3), round(v[1], 3)] for k, v in heads.items()}
    log(f"  customize zoom: heads of {len(heads)} race/sex model(s) from the client")
    lua = open(os.path.join(UI, "CharacterCreate.lua"), encoding="utf-8").read()
    lua = raceglue.put_block(lua, "race icon atlas", lua_race_icons(RACE_ICONS, icon_coords))
    lua = raceglue.put_block(lua, "customize zoom", "\n".join(
        (zoom.lua_settings(first_camera), zoom.lua_heads(heads), zoom.lua_wide(ZOOM_WIDE))))
    client_lua = base(GLUE + "CharacterCreate.lua").decode("utf-8", "replace")
    base(GLUE + "CharacterCreate.xml")
    lua, carried = carry_race_icons(lua, client_lua)
    if carried:
        log(f"  race icons kept from the installed screen: {', '.join(t.split(' ', 1)[1] for t in carried)}")
    write(GLUE + "CharacterCreate.lua", _crlf(lua))
    for screen in SCREENS:
        if screen != "CharacterCreate":
            base(GLUE + screen + ".lua")
            base(GLUE + screen + ".xml")
            write(GLUE + screen + ".lua", _crlf(open(os.path.join(UI, screen + ".lua"), encoding="utf-8").read()))
        write(GLUE + screen + ".xml", _crlf(fill_atlas(xmls[screen], atlas)))
    parent = base(GLUE + "GlueParent.lua").decode("utf-8", "replace")
    write(GLUE + "GlueParent.lua", patch_glue_parent(parent).encode("utf-8"))

    res.owns += [ATLAS_DIR] + [ART + f + ".blp" for f in ART_FILES] + [f + ".blp" for f in SELECT_ART]
    _write_install(out_dir, res)
    write_verified(os.path.join(out_dir, "package.json"), res.model_dump_json(indent=1).encode("utf-8"))
    log(f"Done: {out_dir}")
    return res


def _crlf(text: str) -> bytes:
    """GlueXML as Blizzard ships it: CRLF, UTF-8."""
    return text.replace("\r\n", "\n").replace("\n", "\r\n").encode("utf-8")


def _write_install(out_dir: str, res: GlueBuildResult) -> None:
    lines = ["Character create and select laid out as Battle for Azeroth's - glue package built by retroport", "",
             "INSTALL: retroport install - it never overwrites a file you changed since this build, backs",
             "up everything it replaces, and writes an undo receipt:",
             f"  retroport install \"{out_dir}\" --client <WoW folder> --patch patch-H.MPQ --clean",
             "  (shows the plan; add --apply to install; undo: retroport uninstall <installs\\...json>)", "",
             "RACE PACKAGES: install this package first, then rebuild each race package on top of it",
             "  (`retroport race ... --reuse --replace`) and install that: the race adds its icon, text",
             "  and scene to the new screen. Until then an installed race keeps its icon (carried over).", "",
             "Built on:"]
    lines += [f"  {k}: {v['source']}" for k, v in res.bases.items()]
    if res.warnings:
        lines += ["", "Warnings:"] + [f"  - {w}" for w in res.warnings]
    write_verified(os.path.join(out_dir, "INSTALL.txt"), ("\r\n".join(lines) + "\r\n").encode("utf-8"))
