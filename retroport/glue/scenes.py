"""BfA's glue scenes for 3.3.5: the scene models character create and character select stand
characters in, converted from BfA's own.

A scene goes into ``Interface\\Glues\\Models\\UI_<Name>\\`` - the path 3.3.5's SetBackgroundModel
builds from a name (GlueParent.lua) - converted with wotlkconv and drawn as WarcraftXL draws BfA's
models (``helmets.redraw_scene``: reflections through a combiner table, particle emitters fixed).
Its textures come along: every one in the scene's own folder (the package's folder: an installed
package's are in the client already, and a rebuild must still hold them), and any other it names
that the client lacks.

Which scene a race stands in on character select is ChrRaces.SelectScreenFileDataID - BfA keeps
3.3.5's names for the ten races 3.3.5 shipped (``UI_Human`` ... ``UI_Draenei``) and adds one for
the Gnome and the Troll, which 3.3.5 stands in the Dwarf's and the Orc's (0x4E36C8).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from ..character.convert import BfaInstall
from ..convert import engine
from ..formats import blp
from ..mpq import ClientData
from ..race import helmets
from ..safeio import write_verified

MODELS = "Interface\\Glues\\Models\\"


class SceneError(ValueError):
    """A scene BfA doesn't have, or that won't convert."""


@dataclass
class Scene:
    stem: str                                   # UI_<Name>
    folder: str                                 # client-relative, no trailing backslash
    output: str                                 # the converted .m2
    files: list[str] = field(default_factory=list)
    lossy: list[str] = field(default_factory=list)      # the converter's lossy notes
    redrawn: int = 0                            # batches given 3.3.5 shaders
    emitters: int = 0                           # particle emitters fixed
    textures: int = 0                           # textures taken from BfA
    warnings: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return ("converted" + (f" ({', '.join(self.lossy)})" if self.lossy else "")
                + (f"; {self.redrawn} batch(es) given 3.3.5 shaders" if self.redrawn else "")
                + (f"; {self.emitters} particle emitter(s) fixed" if self.emitters else "")
                + f"; {self.textures} texture(s) from BfA")


def select_scene_fdid(bfa: BfaInstall, client_file_string: str) -> int:
    """BfA's select scene for a race (ChrRaces.SelectScreenFileDataID), 0 for none."""
    rows = [r for _i, r in bfa.table("ChrRaces")
            if str(r.get("ClientFileString", "")).lower() == client_file_string.lower()]
    return int(rows[0].get("SelectScreenFileDataID") or 0) if rows else 0


def scoped_listfile(bfa: BfaInstall, scenes: list[bytes], work: str, listfile: str):
    """The listfile names these scenes' files need (read once for all of them)."""
    return engine.scoped_listfile_for(scenes, [work], listfile, read_fdid=lambda f: bfa.casc.try_read_file_id(f)[0])


def convert_scene(bfa: BfaInstall, data: bytes, stem: str, *, client: ClientData, client_out: str, work: str,
                  lf, folder: str | None = None, own_folder: bool = True) -> Scene:
    """BfA's scene ``data`` as ``<client_out>\\Interface\\Glues\\Models\\<stem>\\<stem>.m2``, its skins
    and textures (module docstring). ``folder``: another client folder for it (a spell's effect
    model: ``Spells``); ``own_folder`` False: that folder is shared, so a texture in it is taken
    only when the client lacks it, like any other."""
    folder = folder or MODELS + stem
    er = engine.convert_with_wotlkconv(os.path.join(work, stem + ".m2"), os.path.join(client_out, *folder.split("\\")),
                                       output_stem=stem, casc=bfa.casc, data=data, listfile=lf)
    if not er.ok:
        raise SceneError(f"{stem}: {er.error}")
    scene = Scene(stem=stem, folder=folder, output=er.output, files=list(er.written),
                  lossy=sorted({n.code for n in er.notes if n.level != "info"}))
    scene.redrawn, scene.emitters = helmets.redraw_scene(er.output, data, bfa.read)
    with open(er.output, "rb") as fh:
        names = helmets.texture_names(fh.read())
    own = (folder + "\\").lower()
    for name in names:
        if not (own_folder and name.lower().startswith(own)) and client.exists(name):
            continue
        fdid = lf.id_for(name)
        if fdid is None:
            scene.warnings.append(f"{stem}: texture {name} is in neither the client nor the listfile")
            continue
        try:
            scene.files.append(write_verified(os.path.join(client_out, *name.replace("/", "\\").split("\\")),
                                              bfa.read(fdid), blp.verify_blp))
            scene.textures += 1
        except (blp.BLPError, ValueError, OSError) as e:
            scene.warnings.append(f"{stem}: texture {name}: {e}")
    return scene
