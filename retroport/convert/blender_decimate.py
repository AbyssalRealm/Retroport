"""Blender headless decimation for retroport poly-reduction.

Run by Blender, not by retroport's Python:

    blender --background --python blender_decimate.py -- \
        --input model.m2i --output model_lo.m2i --target-tris 21000 --hair-ratio 0.4

Primary path is the LunNova WoW Tools .m2i addon (Blender 2.9+/3.4): the standard
backport route is M2Mod .m2 -> .m2i, decimate the .m2i here, M2Mod .m2i -> .m2. WowTools
names each geoset object 'MeshNNNN' where NNNN is the geoset id and round-trips that id
on export, so decimation must NOT rename objects.

Per-geoset ratios: hair is geoset group 1 (ids 1xx). It's dense and its silhouette hides
heavy reduction, so with --hair-ratio set we crush hair and spend the rest of the budget
gently on the body - avoiding the "melted face" a flat ratio gives. --hair-ratio 0 = flat.

Also supports .m2 (WoW Blender Studio, WotLK) and .obj/.gltf (stock) for sanity checks.
"""

import argparse
import os
import sys

import bpy

WOWTOOLS_MODULE = "WowTools"       # LunNova .m2i addon (2.9+/3.4)
WBS_MODULE = "io_scene_wmo"        # WoW Blender Studio (.m2)
_LEGACY = bpy.app.version < (2, 80)


def _args():
    argv = sys.argv
    argv = argv[argv.index("--") + 1:] if "--" in argv else []
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    p.add_argument("--output", required=True)
    p.add_argument("--target-tris", type=int, default=21000)
    p.add_argument("--hair-ratio", type=float, default=0.4)
    return p.parse_args(argv)


def _enable(module):
    op = getattr(bpy.ops.wm, "addon_enable", None) or bpy.ops.preferences.addon_enable
    try:
        op(module=module)
    except Exception as exc:  # noqa
        raise SystemExit(f"[decimate] could not enable addon '{module}': {exc}")


def _set_active(obj):
    if _LEGACY:
        bpy.context.scene.objects.active = obj
    else:
        bpy.context.view_layer.objects.active = obj


def _apply(mod_name):
    if _LEGACY:
        bpy.ops.object.modifier_apply(apply_as="DATA", modifier=mod_name)
    else:
        bpy.ops.object.modifier_apply(modifier=mod_name)


def _clear():
    try:
        bpy.ops.object.select_all(action="SELECT")
        bpy.ops.object.delete()
    except RuntimeError:
        pass


def _import(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".m2i":
        _enable(WOWTOOLS_MODULE)
        bpy.ops.wow_tools.import_m2i(filepath=path)
    elif ext == ".m2":
        _enable(WBS_MODULE)
        bpy.context.scene.wow_scene.version = "2"
        bpy.context.scene.wow_scene.type = "M2"
        bpy.ops.import_mesh.m2(filepath=path)
    elif ext == ".obj":
        (bpy.ops.wm.obj_import if hasattr(bpy.ops.wm, "obj_import") else bpy.ops.import_scene.obj)(filepath=path)
    elif ext in (".gltf", ".glb"):
        bpy.ops.import_scene.gltf(filepath=path)
    else:
        raise SystemExit(f"[decimate] unsupported input extension: {ext}")


def _export(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".m2i":
        bpy.ops.export.m2i(filepath=path)
    elif ext == ".m2":
        bpy.context.scene.wow_scene.version = "2"
        bpy.ops.export_mesh.m2(filepath=path)
    elif ext == ".obj":
        (bpy.ops.wm.obj_export if hasattr(bpy.ops.wm, "obj_export") else bpy.ops.export_scene.obj)(filepath=path)
    elif ext in (".gltf", ".glb"):
        bpy.ops.export_scene.gltf(filepath=path)
    else:
        raise SystemExit(f"[decimate] unsupported output extension: {ext}")


def _meshes():
    return [o for o in bpy.context.scene.objects if o.type == "MESH"]


def _obj_tris(o):
    return sum(len(p.vertices) - 2 for p in o.data.polygons)


def _count_tris():
    return sum(_obj_tris(o) for o in _meshes())


def _geoset_group(o):
    """group = geoset id // 100 (1 = hair). WBS stores it as a property; LunNova .m2i
    encodes it in the object name 'MeshNNNN'."""
    try:
        return int(o.wow_m2_geoset.mesh_part_id) // 100
    except (AttributeError, ValueError, TypeError):
        pass
    n = o.name.split(".")[0].split("_")[0]
    if n.startswith("Mesh") and n[4:].isdigit():
        return int(n[4:]) // 100
    return None


def _decimate(o, ratio):
    if ratio >= 1.0:
        return
    _set_active(o)
    mod = o.modifiers.new(name="rp_decimate", type="DECIMATE")   # keeps the object name
    mod.decimate_type = "COLLAPSE"
    mod.ratio = max(0.02, ratio)
    if hasattr(mod, "use_collapse_triangulate"):
        mod.use_collapse_triangulate = True
    _apply(mod.name)


def main():
    args = _args()
    _clear()
    _import(args.input)

    meshes = _meshes()
    if not meshes or _count_tris() == 0:
        raise SystemExit("[decimate] no mesh triangles found after import")

    hair = [o for o in meshes if _geoset_group(o) == 1]
    body = [o for o in meshes if _geoset_group(o) != 1]
    hair_tris = sum(_obj_tris(o) for o in hair)
    body_tris = sum(_obj_tris(o) for o in body)
    total = hair_tris + body_tris

    if total <= args.target_tris:
        print(f"[decimate] {total} tris already <= {args.target_tris}; nothing to do")
        _export(args.output)
        return

    if args.hair_ratio and 0 < args.hair_ratio < 1 and hair and hair_tris > 0:
        hair_after = hair_tris * args.hair_ratio
        budget = args.target_tris - hair_after
        body_ratio = min(1.0, max(0.02, budget / body_tris)) if body_tris else 1.0
        for o in hair:
            _decimate(o, args.hair_ratio)
        for o in body:
            _decimate(o, body_ratio)
        print(f"[decimate] hair {hair_tris}->x{args.hair_ratio:.2f}, body {body_tris}->x{body_ratio:.2f}")
    else:
        ratio = min(1.0, args.target_tris / total)
        for o in meshes:
            _decimate(o, ratio)
        print(f"[decimate] flat ratio x{ratio:.3f}")

    after = _count_tris()
    print(f"[decimate] {total} -> {after} tris (target {args.target_tris})")
    _export(args.output)


if __name__ == "__main__":
    main()
