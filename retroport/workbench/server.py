"""The workbench's local web server: the browser page, a JSON API over the catalogs, icons.

Listens on 127.0.0.1 only. Catalogs load in the background, so the page is up at once and shows
their progress. Settings (the installs, the 3.3.5 client, WoWDBDefs, the listfile, where the
workbench keeps its cache and basket) live in ``%USERPROFILE%\\.retroport\\workbench.json``; the
``retroport workbench`` options update them.

API (GET unless noted):
  /api/status                    sources and their state, the loading log
  /api/meta                      names: slots, armour and weapon types, expansions, classes, qualities
  /api/items?source=&q=&slot=&armour=&weapon=&expansion=&quality=&cls=&looks=&sort=&page=
  /api/item?source=&id=
  /api/sets?source=&q=&expansion=&cls=&armour=&kind=transmog|itemset&page=
  /api/set?source=&id=&kind=
  /api/basket                    GET the basket; POST replaces it
  /api/basket/plan               what installing it builds: each entry's looks, overlaps, changes
  /api/kit                       the kit project's state; POST /api/kit/run {action: go|build|rerun|undo|stop...}
  /api/settings                  GET the Settings view; POST saves it (a changed source is read again)
  POST /api/pick {folder, title, start}   Windows' folder/file picker on this machine -> {path}
  /api/races                     the races the 3.3.5 client plays (the character view's choices)
  /api/preview?source=&display=&race=&sex=     a display's models (meshes, Stand) and body textures
  /api/body?race=&sex=           a BfA body: mesh, skin layers, layout, hair, default geosets
  /api/outfit?source=&race=&sex=&items=<display>:<slot>,...   what each piece puts on that body
  /icon/<source>/<fdid>.png      an icon from the source's files (cached as PNG); /tex/... a texture
"""

from __future__ import annotations

import gzip
import io
import json
import mimetypes
import os
import threading
import time
import traceback
import urllib.parse
from dataclasses import asdict, dataclass, field
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import catalog as C
from . import character as CH
from . import kit as K
from . import models as M
from . import pick as P

STATIC = os.path.join(os.path.dirname(__file__), "static")
SETTINGS_PATH = os.path.join(os.path.expanduser("~"), ".retroport", "workbench.json")
PORT = 8335


# What a fresh install reads: retail's items from wago.tools at its newest build, and the BfA
# bodies the character view dresses from wago at 8.3.7 (no BfA item tab unless asked for).
DEFAULT_SOURCES = [{"key": "retail", "label": "Retail", "kind": "wago", "product": "wow"},
                   {"key": "bfa", "label": "BfA", "kind": "wago", "build": "8.3.7.35435", "browse": False}]


@dataclass
class Settings:
    sources: list[dict] = field(default_factory=list)     # SourceSpec fields (DEFAULT_SOURCES where unset)
    client: str = ""                                        # the 3.3.5 client folder
    dbd: str = ""                                           # WoWDBDefs definitions folder (local installs)
    listfile: str = ""                                      # the community listfile (local installs)
    data: str = ""                                          # cache + basket
    kit: dict = field(default_factory=dict)                 # kit.KitSettings over the Backport Helper's

    @classmethod
    def load(cls, path: str = SETTINGS_PATH) -> "Settings":
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
            return cls(**{k: v for k, v in raw.items() if k in cls.__dataclass_fields__})
        except (OSError, ValueError, TypeError):
            return cls()

    def save(self, path: str = SETTINGS_PATH) -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".part"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, indent=2)
        os.replace(tmp, path)

    def set_source(self, key: str, label: str, path: str, product: str | None = None, cdn: bool = False) -> None:
        """A local install for ``key`` (the command line's --retail / --bfa)."""
        self.sources = [s for s in self.sources if s.get("key") != key]
        self.sources.append(asdict(C.SourceSpec(key, label, path, product, cdn)))

    def effective_sources(self) -> list[C.SourceSpec]:
        """Retail, BfA (each as set, else its default), then any other source set."""
        have = {s.get("key"): s for s in self.sources}
        specs = [have.get(d["key"], d) for d in DEFAULT_SOURCES] + \
                [s for s in self.sources if s.get("key") not in ("retail", "bfa")]
        return [C.SourceSpec(**{k: v for k, v in s.items() if k in C.SourceSpec.__dataclass_fields__}) for s in specs]

    @property
    def data_dir(self) -> str:
        return self.data or os.path.join(os.path.dirname(SETTINGS_PATH), "workbench")

    def source_problem(self, spec: C.SourceSpec) -> str:
        """Why a source can't be read ("" when it can)."""
        if spec.kind != "local":
            return ""
        if not spec.path or not os.path.isfile(os.path.join(spec.path, ".build.info")):
            return f"{spec.label}: {spec.path or '(no folder)'} is not a game install (no .build.info)"
        if not self.dbd or not os.path.isdir(self.dbd):
            return f"{spec.label}: a local install needs the WoWDBDefs definitions folder"
        if not self.listfile or not os.path.isfile(self.listfile):
            return f"{spec.label}: a local install needs the community listfile"
        return ""

    def problems(self) -> list[str]:
        out = []
        if not self.client:
            out.append("choose your 3.3.5 client folder in Settings")
        elif not os.path.isdir(os.path.join(self.client, "Data")):
            out.append(f"{self.client} has no Data folder - is it the 3.3.5 client?")
        out += [p for p in map(self.source_problem, self.effective_sources()) if p]
        return out


class Workbench:
    """The sources, their catalogs (loaded in the background) and the basket."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.sources: dict[str, object] = {}            # the sources whose items are browsed
        self.catalogs: dict[str, C.Catalog] = {}
        self.state: dict[str, str] = {}
        self.log: list[str] = []
        self.in_client: set[int] = set()
        self._displays: dict[str, M.DisplayIndex] = {}
        self._races: list[dict] | None = None
        self._meshes: dict[tuple, dict] = {}
        self._race_names: dict[str, dict[int, str]] = {}
        self._specs: dict[str, C.SourceSpec] = {}
        self._client_seen = None
        self.bodies: CH.Bodies | None = None
        self.basket_path = os.path.join(settings.data_dir, "basket.json")
        self._lock = threading.Lock()
        self._loading = threading.Lock()
        self.last_hello = self.last_bye = 0.0         # the page's comings and goings (app.py)

        def save_kit(values: dict) -> None:
            settings.kit = values
            settings.save()
        self.kit = K.Kit(settings.kit, None, settings.dbd, settings.listfile, settings.data_dir, save_kit)
        self.configure()

    def configure(self) -> None:
        """The sources as the settings now say: a source whose settings changed is read again (its
        catalog too); the others keep what they loaded."""
        st, cache = self.settings, os.path.join(self.settings.data_dir, "cache")
        specs = {sp.key: sp for sp in st.effective_sources()}
        for key in list(self._specs):
            if specs.get(key) != self._specs[key]:
                for d in (self.sources, self.catalogs, self._displays, self.state, self._race_names):
                    d.pop(key, None)
        readers = {}
        for key, sp in specs.items():
            if key in self.sources:
                readers[key] = self.sources[key]
            else:
                readers[key] = C.make_source(sp, st.dbd, st.listfile, cache)
                if sp.browse:
                    self.sources[key] = readers[key]
                    self.state[key] = "waiting"
        bfa = specs.get("bfa")
        if bfa and (self._specs.get("bfa") != bfa or self.bodies is None):
            self.bodies = CH.Bodies(readers["bfa"])
        self._specs = specs
        self._races = None
        self._meshes.clear()
        retail = specs.get("retail")
        self.kit.retail = asdict(retail) if retail and retail.kind == "local" else None
        self.kit.dbd, self.kit.listfile = st.dbd, st.listfile

    def say(self, msg: str) -> None:
        with self._lock:
            self.log.append(f"{time.strftime('%H:%M:%S')} {msg}")
            del self.log[:-200]

    def load(self) -> None:
        """The client's item ids (marks what 3.3.5 already has), then each waiting source's catalog
        and the tables its 3D previews read. Runs again after the settings change."""
        with self._loading:
            client = self.settings.client
            if client != self._client_seen:
                self._client_seen = client
                self.in_client = set()
                if client and os.path.isdir(client):
                    try:
                        self.in_client = C.client_item_ids(client)
                        self.say(f"3.3.5 client: {len(self.in_client)} item ids")
                    except Exception as e:
                        self.say(f"3.3.5 client: {e}")
                for cat in self.catalogs.values():
                    cat.in_client = self.in_client
            for key, src in list(self.sources.items()):
                if self.state.get(key) != "waiting":
                    continue
                why = self.settings.source_problem(self._specs[key])
                if why:
                    self.state[key] = "failed"
                    self.say(why)
                    continue
                self.state[key] = "loading"
                try:
                    cat = C.load_catalog(src, self.say)
                    cat.in_client = self.in_client
                    if self.sources.get(key) is not src:      # the settings changed meanwhile
                        continue
                    self.catalogs[key] = cat
                    self.state[key] = "ready"
                    self.say(f"{src.spec.label} {cat.build}: {len(cat.items)} items, {len(cat.sets)} sets")
                except Exception as e:
                    if self.sources.get(key) is src:
                        self.state[key] = "failed"
                    hint = " - wago.tools may be down: try again later, or read a local install (Settings)" \
                        if src.spec.kind == "wago" else ""
                    self.say(f"{src.spec.label}: {e}{hint}")
                    traceback.print_exc()
            for key in list(self.catalogs):              # the 3D previews' tables, ready before they're asked for
                try:
                    self.displays(key)
                except Exception as e:
                    self.say(f"{self.sources[key].spec.label} previews: {e}")

    def start(self) -> None:
        threading.Thread(target=self.load, name="catalogs", daemon=True).start()

    def page_seen(self, open_: bool) -> None:
        """A page says it's open (now and then) or that it's closing: the app ends with its last page."""
        with self._lock:
            if open_:
                self.last_hello = time.time()
            else:
                self.last_bye = time.time()

    # -- answers ----------------------------------------------------------------
    def status(self) -> dict:
        return {"sources": [{"key": k, "label": s.spec.label, "path": s.spec.path, "state": self.state.get(k),
                             "build": self.catalogs[k].build if k in self.catalogs else "",
                             "items": len(self.catalogs[k].items) if k in self.catalogs else 0,
                             "sets": len(self.catalogs[k].sets) if k in self.catalogs else 0}
                            for k, s in self.sources.items()],
                "client": self.settings.client, "in_client": len(self.in_client), "log": self.log[-40:],
                "problems": self.settings.problems(), "kit": {"bp": self.kit.cfg.bp, "work": self.kit.cfg.work},
                "setup": not self.settings.client}

    @staticmethod
    def meta() -> dict:
        return {"slots": C.SLOT_NAMES, "slot_groups": list(C.SLOT_GROUPS), "armour": C.ARMOUR_NAMES,
                "armour_types": list(C.ARMOUR_TYPES), "weapons": C.WEAPON_TYPES, "expansions": C.EXPANSIONS,
                "classes": C.CLASSES, "qualities": {k: {"name": n, "color": c} for k, (n, c) in C.QUALITIES.items()},
                "modifiers": C.MODIFIERS, "item_fields": C.ITEM_FIELDS,
                "item_geosets": {str(k): v for k, v in CH.ITEM_GEOSETS.items()}}

    def reader(self, key: str):
        """A source's reader by key - the BfA one too when it's read for its bodies only."""
        if key in self.sources:
            return self.sources[key]
        if key == "bfa" and self.bodies is not None:
            return self.bodies.src
        return None

    def catalog(self, key: str) -> C.Catalog:
        cat = self.catalogs.get(key) or (next(iter(self.catalogs.values())) if not key and self.catalogs else None)
        if cat is None:
            raise LookupError(f"source {key!r} is not loaded ({self.state.get(key, 'unknown')})")
        return cat

    def icon(self, key: str, fdid: int) -> bytes:
        return self.image("icons", key, fdid)

    def image(self, kind: str, key: str, fdid: int) -> bytes:
        """A PNG of a BLP FileDataID (an icon, a texture), cached on disk - a FileDataID is the same
        file in every build. From the source asked for, else from any other: BfA 8.3.7 keeps the
        art of its last sets encrypted - retail has it open."""
        path = os.path.join(self.settings.data_dir, kind, f"{fdid}.png")
        if os.path.isfile(path):
            with open(path, "rb") as fh:
                return fh.read()
        from ..formats import blp
        first = self.reader(key)
        order = ([first] if first else []) + [s for k, s in self.sources.items() if s is not first]
        last = None
        for src in order:
            try:
                img = blp.decode_bytes(src.read(fdid), f"{kind} {fdid}")
                break
            except Exception as e:                      # encrypted, absent, unreadable: try the next
                last = e
        else:
            raise LookupError(f"{kind} {fdid}: {last}")
        buf = io.BytesIO()
        img.save(buf, "PNG", compress_level=3)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + f".{threading.get_ident()}.part"
        with open(tmp, "wb") as fh:
            fh.write(buf.getvalue())
        os.replace(tmp, path)
        return buf.getvalue()

    def displays(self, key: str) -> M.DisplayIndex:
        src = self.sources.get(key)
        if src is None:
            raise LookupError(f"no source {key!r}")
        if key not in self._displays:
            self._displays[key] = M.load_index(src, os.path.join(self.settings.data_dir, "cache"), self.say)
        return self._displays[key]

    # -- races, bodies, previews ----------------------------------------------------
    def races(self) -> list[dict]:
        """The races the 3.3.5 client plays, one per prefix, named as BfA names them (else the
        retail source), with their ids in BfA (the body) and in each item source."""
        if self._races is None:
            out = []
            if self.settings.client and os.path.isdir(self.settings.client):
                names = {}
                for key in self.sources:
                    try:
                        names.update({p: i for p, i in self.displays(key).prefixes.items() if p not in names})
                    except Exception:
                        pass
                bfa_races = self.bodies.races() if self.bodies else {}
                for r in CH.client_races(self.settings.client):
                    p = r["prefix"].lower()
                    b = bfa_races.get(p)
                    out.append({"key": p, "name": (b[1].get("Name_lang") if b else "") or r["prefix"], "client": r["ids"],
                                "bfa": b[0] if b else 0,
                                "sources": {k: self.displays(k).prefixes.get(p, 0) for k in self.catalogs}})
            if len(self.catalogs) < len(self.sources):
                return out                              # sources still loading: their ids come later
            self._races = out
        return self._races

    def race_ids(self, key: str, race: str) -> tuple[int, int]:
        """A race (prefix, or a number for that source) as (its id in the item source, its BfA id)."""
        if race.isdigit():
            return int(race), int(race)
        r = next((x for x in self.races() if x["key"] == race.lower()), None)
        if r is None:
            raise LookupError(f"no race {race!r}")
        return r["sources"].get(key) or r["bfa"] or 1, r["bfa"]

    def race_name(self, key: str, race: int) -> str:
        """A race id of a source by its ChrRaces name."""
        names = self._race_names.get(key)
        if names is None:
            try:
                names = {i: r.get("Name_lang") or r.get("ClientPrefix") or str(i) for i, r in self.reader(key).table("ChrRaces")}
            except Exception:
                names = {}
            self._race_names[key] = names
        return names.get(race, f"race {race}")

    def mesh(self, key: str, fdid: int, textures: dict) -> dict:
        k = (key, fdid, tuple(sorted(textures.items())))
        if k not in self._meshes:
            if len(self._meshes) > 200:
                self._meshes.clear()
            self._meshes[k] = M.mesh(self.reader(key).read, fdid, textures)
        return self._meshes[k]

    def preview(self, key: str, display: int, race: str, sex: int) -> dict:
        """An item display's models (as meshes, with Stand) and body textures, for that race and sex."""
        idx = self.displays(key)
        item_race, _bfa = self.race_ids(key, race)
        out = {"display": display, "race": race, "sex": sex, "models": [], "problems": [],
               "body": idx.body_textures(display, item_race, sex), "row": idx.row(display), "made_for": "", "shown_as": ""}
        found = idx.models(display, item_race, sex)
        races = idx.model_races(display)
        if not found and races:
            # no model for this race (heritage armour is made for one): show another race's - one the
            # 3.3.5 client plays first - and say whose
            played = {r["sources"].get(key) for r in self.races()}
            other = next((r for r in races if r in played), races[0])
            found = idx.models(display, other, sex)
            out["shown_as"] = self.race_name(key, other)
            if len(races) <= 3:
                out["made_for"] = ", ".join(self.race_name(key, r) for r in races)
        for m in found:
            try:
                out["models"].append(m | {"mesh": self.mesh(key, m["fdid"], m["textures"])})
            except Exception as e:                      # one bad model never hides the rest
                out["problems"].append(f"model {m['fdid']}: {type(e).__name__}: {e}")
        return out

    def body(self, race: str, sex: int) -> dict:
        if self.bodies is None:
            raise LookupError("the character view needs BfA's bodies (Settings: BfA from wago.tools or a local install)")
        _item, bfa = self.race_ids("bfa", race)
        return self.bodies.body(bfa, sex)

    def outfit(self, key: str, race: str, sex: int, items: list[tuple[int, int]]) -> dict:
        """What each (display, inventory type) puts on the body: its geoset values, section textures,
        the body groups a helm hides, and its models - attached at a body point, or a collection
        skinned to the body."""
        idx = self.displays(key)
        item_race, _bfa = self.race_ids(key, race)
        pieces, problems = [], []
        for display, slot in items:
            row = idx.row(display)
            if row is None:
                continue
            piece = {"display": display, "slot": slot, "row": row, "body": idx.body_textures(display, item_race, sex),
                     "hides": idx.helmet_hidden(display, item_race, sex) if slot == 1 else [], "models": [],
                     # a cloak paints the body's cape geosets (M2 texture type 2) with its material
                     "cape": idx.texture(row["materials"][0], item_race, sex) if slot == 16 and row["materials"][0] else 0}
            for m in idx.models(display, item_race, sex):
                if m["type"] == M.ATTACHED:
                    m = m | {"attach": CH.SHOULDER_POINTS.get(m["position"], 6) if slot == 3
                             else CH.ATTACH_POINTS.get(slot, -1)}
                try:
                    piece["models"].append(m | {"mesh": self.mesh(key, m["fdid"], m["textures"])})
                except Exception as e:
                    problems.append(f"display {display} model {m['fdid']}: {type(e).__name__}: {e}")
            races = idx.model_races(display)
            if not piece["models"] and races:               # heritage armour of another race
                piece["made_for"] = ", ".join(self.race_name(key, r) for r in races) if len(races) <= 3 else \
                    "other races"
            pieces.append(piece)
        return {"race": race, "sex": sex, "pieces": pieces, "problems": problems,
                "item_geosets": {str(k): v for k, v in CH.ITEM_GEOSETS.items()}, "dress_order": CH.DRESS_ORDER}

    def run_kit(self, action: str, step: str = "") -> None:
        k = self.kit
        if action == "stop":
            return k.stop()
        jobs = {"go": (k.go, self.basket()), "create": (k.create,), "select": (k.select, self.basket()),
                "scan": (k.scan,), "build": (k.build,), "rerun": (k.build, step), "install": (k.install,),
                "undo": (k.undo,)}
        if action not in jobs:
            raise ValueError(f"no kit action {action!r}")
        if action == "rerun" and step not in ("scan", *K.STEPS):
            raise ValueError(f"no step {step!r}")
        fn, *args = jobs[action]
        k.start(action if action != "rerun" else f"rerun {step}", fn, *args)

    # -- settings (the page's Settings view) ---------------------------------------
    def settings_view(self) -> dict:
        st, kc = self.settings, self.kit.cfg
        specs = {sp.key: sp for sp in st.effective_sources()}
        src = lambda k: {"kind": specs[k].kind, "path": specs[k].path, "browse": specs[k].browse}
        return {"client": st.client, "server_dbc": kc.server_dbc, "retail": src("retail"), "bfa": src("bfa"),
                "dbd": st.dbd, "listfile": st.listfile,
                "kit": {"work": kc.work, "bp": kc.bp, "tools": kc.tools, "local": kc.local, "armor_pieces": kc.armor_pieces},
                "checks": self.checks(), "first_run": not st.client, "data_dir": st.data_dir, "picker": P.available()}

    def checks(self) -> dict:
        """Each setting's folder or file looked at: {name: [ok, what was found]}."""
        st, kc = self.settings, self.kit.cfg
        out: dict[str, list] = {}

        def put(name, ok, text):
            out[name] = [ok, text]
        c = st.client
        if c:
            data = os.path.isdir(os.path.join(c, "Data"))
            exe = any(os.path.isfile(os.path.join(c, n)) for n in ("Wow.exe", "wow.exe", "WoW.exe"))
            put("client", data, ("3.3.5 client" + ("" if exe else " (no Wow.exe here)") +
                                 (f": {len(self.in_client):,} items" if self.in_client else "")) if data else "no Data folder here")
        if kc.server_dbc:
            ok = os.path.isfile(os.path.join(kc.server_dbc, "Item.dbc"))
            put("server_dbc", ok, "Item.dbc and its tables are here" if ok else "no Item.dbc here")
        for sp in st.effective_sources():
            if sp.kind == "local":
                why = st.source_problem(sp)
                build = ""
                if os.path.isfile(os.path.join(sp.path or "", ".build.info")):
                    try:
                        from pathlib import Path
                        from wotlkconv.casc.config import BuildInfo
                        build = BuildInfo.load(Path(sp.path), sp.product).version
                    except Exception:
                        pass
                put(sp.key, not why, why.split(": ", 1)[-1] if why else f"{sp.label} {build}".strip())
        if st.dbd:
            ok = os.path.isfile(os.path.join(st.dbd, "ChrRaces.dbd"))
            put("dbd", ok, "WoWDBDefs definitions" if ok else "no .dbd files here")
        if st.listfile:
            put("listfile", os.path.isfile(st.listfile), "listfile" if os.path.isfile(st.listfile) else "no such file")
        if kc.work:
            prj = self.kit.project()
            put("work", True, f"the kit's project (retail {prj.get('build')})" if prj else "Install makes a new project here")
        if kc.bp:
            put("bp", os.path.isfile(kc.bp), "the kit" if os.path.isfile(kc.bp) else "no bp.py here")
        if kc.tools:
            ok = os.path.isfile(os.path.join(kc.tools, "backport_to_equipplus.py"))
            put("tools", ok, "Equip Plus's tools" if ok else "no backport_to_equipplus.py here")
        return out

    def apply_settings(self, v: dict) -> None:
        """The Settings view saved: the sources, the client and the kit's folders; a changed source
        is read again."""
        if self.kit.busy():
            raise RuntimeError("the kit is busy - change the settings when its job is done")
        st = self.settings
        norm = lambda p: os.path.normpath(str(p).strip().strip('"')) if str(p or "").strip() else ""
        st.client = norm(v.get("client", st.client))
        for key, label, product in (("retail", "Retail", "wow"), ("bfa", "BfA", None)):
            x = v.get(key) or {}
            default = next(d for d in DEFAULT_SOURCES if d["key"] == key)
            if x.get("kind") == "local":
                spec = asdict(C.SourceSpec(key, label, norm(x.get("path")), product, key == "retail", "local",
                                           browse=key == "retail" or bool(x.get("browse"))))
            else:
                spec = dict(default, browse=key == "retail" or bool(x.get("browse")))
            st.sources = [s for s in st.sources if s.get("key") != key] + [spec]
        st.dbd, st.listfile = norm(v.get("dbd", st.dbd)), norm(v.get("listfile", st.listfile))
        kit = dict(v.get("kit") or {})
        kit["server_dbc"] = norm(v.get("server_dbc", self.kit.cfg.server_dbc))
        if st.client:
            kit["client_data"] = os.path.join(st.client, "Data")
        for f in ("work", "bp", "tools"):
            if f in kit:
                kit[f] = norm(kit[f])
        self.kit.set_settings(kit)                       # saves the settings, the workbench's with them
        st.save()
        self.configure()
        self.start()

    def basket_plan(self) -> dict:
        """The basket as the next install builds it, against what the last one put in."""
        last = self.kit.last_install()
        plan = C.plan_basket(self.catalogs, self.basket(), last.get("basket") if last else None)
        plan["installed_at"] = last["time"] if last else ""
        return plan

    def basket(self) -> list:
        try:
            with open(self.basket_path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return []

    def save_basket(self, entries: list) -> None:
        if not isinstance(entries, list):
            raise ValueError("the basket is a list")
        os.makedirs(os.path.dirname(self.basket_path), exist_ok=True)
        tmp = self.basket_path + ".part"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(entries, fh, indent=1)
        os.replace(tmp, self.basket_path)


def _int(v: str | None) -> int | None:
    try:
        return int(v) if v not in (None, "") else None
    except ValueError:
        return None


def make_handler(wb: Workbench):
    class Handler(BaseHTTPRequestHandler):
        server_version = "RetroportWorkbench/1"

        def log_message(self, fmt, *args):            # quiet: the page polls
            pass

        def _send(self, body: bytes, ctype: str, status: int = 200, cache: bool = False) -> None:
            zipped = len(body) > 2048 and ctype.startswith(("application/json", "text/")) and \
                "gzip" in (self.headers.get("Accept-Encoding") or "")
            if zipped:
                body = gzip.compress(body, 5)
            self.send_response(status)
            self.send_header("Content-Type", ctype)
            if zipped:
                self.send_header("Content-Encoding", "gzip")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _json(self, obj, status: int = 200) -> None:
            self._send(json.dumps(obj, separators=(",", ":")).encode("utf-8"), "application/json", status)

        def do_GET(self):
            url = urllib.parse.urlsplit(self.path)
            q = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
            try:
                self._route(url.path, q)
            except LookupError as e:
                self._json({"error": str(e)}, HTTPStatus.NOT_FOUND)
            except Exception as e:                      # one bad request never stops the server
                traceback.print_exc()
                self._json({"error": f"{type(e).__name__}: {e}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def do_POST(self):
            url = urllib.parse.urlsplit(self.path)
            try:
                size = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(size) or b"null")
                if url.path == "/api/basket":
                    wb.save_basket(body)
                    self._json({"ok": True, "count": len(body)})
                elif url.path == "/api/kit/settings":
                    wb.kit.set_settings(body or {})
                    self._json(wb.kit.status(wb.basket()))
                elif url.path in ("/api/hello", "/api/bye"):     # a page open / closing (the app's lifetime)
                    wb.page_seen(url.path.endswith("hello"))
                    self._json({"ok": True})
                elif url.path == "/api/settings":
                    wb.apply_settings(body or {})
                    self._json(wb.settings_view())
                elif url.path == "/api/pick":
                    b = body or {}
                    patterns = [tuple(x) for x in b.get("patterns") or []] or None
                    self._json({"path": P.pick(bool(b.get("folder", True)), b.get("title", ""), b.get("start", ""), patterns)})
                elif url.path == "/api/kit/run":
                    wb.run_kit((body or {}).get("action", ""), (body or {}).get("step", ""))
                    self._json(wb.kit.status(wb.basket()))
                else:
                    self._json({"error": "not found"}, HTTPStatus.NOT_FOUND)
            except (ValueError, TypeError) as e:
                self._json({"error": str(e)}, HTTPStatus.BAD_REQUEST)
            except RuntimeError as e:                   # busy, or not set up
                self._json({"error": str(e)}, HTTPStatus.CONFLICT)
            except Exception as e:                      # one bad request never stops the server
                traceback.print_exc()
                self._json({"error": f"{type(e).__name__}: {e}"}, HTTPStatus.INTERNAL_SERVER_ERROR)

        def _route(self, path: str, q: dict) -> None:
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path.startswith("/static/"):
                return self._static(path[len("/static/"):])
            if path.startswith("/tex/"):
                parts = path.split("/")                 # /tex/<source>/<fdid>.png
                if len(parts) != 4 or not parts[3].endswith(".png") or not parts[3][:-4].isdigit():
                    raise LookupError("bad texture path")
                return self._send(wb.image("textures", parts[2], int(parts[3][:-4])), "image/png", cache=True)
            if path == "/api/preview":
                return self._json(wb.preview(q.get("source", "") or next(iter(wb.catalogs), ""), _int(q.get("display")) or 0,
                                             q.get("race", "hu"), _int(q.get("sex")) or 0))
            if path == "/api/races":
                return self._json(wb.races())
            if path == "/api/body":
                return self._json(wb.body(q.get("race", "hu"), _int(q.get("sex")) or 0))
            if path == "/api/outfit":
                items = []
                for part in (q.get("items") or "").split(","):
                    d, _sep, slot = part.partition(":")
                    if d.isdigit():
                        items.append((int(d), int(slot) if slot.isdigit() else 0))
                return self._json(wb.outfit(q.get("source", "") or next(iter(wb.catalogs), ""), q.get("race", "hu"),
                                            _int(q.get("sex")) or 0, items))
            if path.startswith("/icon/"):
                parts = path.split("/")                 # /icon/<source>/<fdid>.png
                if len(parts) != 4 or not parts[3].endswith(".png") or not parts[3][:-4].isdigit():
                    raise LookupError("bad icon path")
                return self._send(wb.icon(parts[2], int(parts[3][:-4])), "image/png", cache=True)
            if path == "/api/status":
                return self._json(wb.status())
            if path == "/api/meta":
                return self._json(wb.meta())
            if path == "/api/basket":
                return self._json(wb.basket())
            if path == "/api/settings":
                return self._json(wb.settings_view())
            if path == "/api/basket/plan":
                return self._json(wb.basket_plan())
            if path == "/api/kit":
                return self._json(wb.kit.status(wb.basket()))
            if path == "/api/kit/log":
                return self._json(wb.kit.log_since(_int(q.get("since")) or 0))
            if path.startswith("/kit/sheet/"):
                return self._kit_sheet(urllib.parse.unquote(path[len("/kit/sheet/"):]))
            cat = wb.catalog(q.get("source", ""))
            page = _int(q.get("page")) or 0
            if path == "/api/items":
                total, rows = cat.search_items(
                    q=q.get("q", ""), slot=q.get("slot", ""), armour=q.get("armour", ""), weapon=_int(q.get("weapon")),
                    expansion=_int(q.get("expansion")), quality=_int(q.get("quality")), cls=_int(q.get("cls")),
                    looks=q.get("looks") == "1", sort=q.get("sort", ""), page=page, junk=q.get("junk") == "1")
                return self._json({"total": total, "page": page, "source": cat.source,
                                   "items": [cat.row_dict(r) for r in rows]})
            if path == "/api/item":
                d = cat.item_detail(_int(q.get("id")) or 0)
                if d is None:
                    raise LookupError("no such item")
                return self._json(d | {"source": cat.source})
            if path == "/api/sets":
                if q.get("kind") == "itemset":
                    total, rows = cat.search_item_sets(q.get("q", ""), page=page)
                else:
                    total, rows = cat.search_sets(q.get("q", ""), expansion=_int(q.get("expansion")),
                                                  cls=_int(q.get("cls")), armour=q.get("armour", ""), page=page,
                                                  junk=q.get("junk") == "1")
                return self._json({"total": total, "page": page, "source": cat.source, "sets": rows})
            if path == "/api/set":
                sid = _int(q.get("id")) or 0
                d = cat.item_set_detail(sid) if q.get("kind") == "itemset" else cat.set_detail(sid)
                if d is None:
                    raise LookupError("no such set")
                return self._json(d | {"source": cat.source, "kind": q.get("kind") or "transmog"})
            raise LookupError(f"no route {path}")

        def _kit_sheet(self, name: str) -> None:
            if name not in wb.kit.sheets():
                raise LookupError(name)
            with open(os.path.join(wb.kit.cfg.work, "sheets", name), "rb") as fh:
                self._send(fh.read(), "image/png")

        def _static(self, rel: str) -> None:
            full = os.path.realpath(os.path.join(STATIC, rel))
            if not full.startswith(os.path.realpath(STATIC) + os.sep) or not os.path.isfile(full):
                raise LookupError(rel)
            with open(full, "rb") as fh:
                ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
                if ctype.startswith("text/") or ctype.endswith("javascript"):
                    ctype += "; charset=utf-8"
                self._send(fh.read(), ctype)

    return Handler


class _Server(ThreadingHTTPServer):
    # one workbench per port: Windows lets a second server bind a port the first still listens on,
    # and the first goes on answering - with its older code
    allow_reuse_address = os.name != "nt"
    daemon_threads = True


def start_server(settings: Settings, port: int = PORT) -> tuple[_Server, Workbench]:
    """The workbench listening on ``port`` (OSError when it's taken), its catalogs loading; the
    caller runs ``serve_forever``."""
    wb = Workbench(settings)
    httpd = _Server(("127.0.0.1", port), make_handler(wb))
    wb.start()
    return httpd, wb


def serve(settings: Settings, port: int = PORT, open_browser: bool = True, log=print) -> None:
    url = f"http://127.0.0.1:{port}/"
    wb = Workbench(settings)
    try:
        httpd = _Server(("127.0.0.1", port), make_handler(wb))
    except OSError as e:
        log(f"Port {port} is in use ({e.strerror or e}): the workbench is probably running already at {url} - "
            "close its window to restart it.")
        if open_browser:
            import webbrowser
            webbrowser.open(url)
        return
    wb.start()
    log(f"Retroport Workbench: {url}  (Ctrl+C stops it)")
    if open_browser:
        import webbrowser
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
