"""Write all of retroport's diagnostic reports for a model into one folder.

Runs check, texreport and (optionally) bugreport, and writes each to
<out_dir>/<model-stem>/. Produces a single combined markdown too, which is the file
to hand to someone doing a side-by-side comparison. Everything is ASCII so it survives
a Windows cp1252 console/redirect.
"""

from __future__ import annotations

import os

from .check import check_model, format_report
from .texreport import build_texture_report
from .bugreport import build_report as build_bug_report


def write_reports(model_path, out_dir, error_text="", manifest_path=None, skin_dir=None):
    """Write check/texture/bug reports for one model. Returns the list of files written."""
    stem = os.path.splitext(os.path.basename(model_path))[0]
    dest = os.path.join(out_dir, stem)
    os.makedirs(dest, exist_ok=True)

    # --- check (plain-text formatter) ---
    try:
        check_txt = format_report(check_model(model_path, skin_dir=skin_dir))
    except (OSError, ValueError) as exc:
        check_txt = f"check failed: {exc}"

    tex_txt = build_texture_report(model_path, manifest_path=manifest_path)
    bug_txt = build_bug_report(model_path, error_text=error_text, skin_dir=skin_dir)

    written = []
    for name, body in (("check.md", check_txt), ("textures.md", tex_txt), ("bugreport.md", bug_txt)):
        path = os.path.join(dest, name)
        with open(path, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(body.rstrip() + "\n")
        written.append(path)

    combined = os.path.join(dest, f"{stem}_report.md")
    with open(combined, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(f"# retroport report: {os.path.basename(model_path)}\n\n")
        fh.write(f"Source: `{model_path}`\n\n")
        fh.write("## Check (WotLK compatibility)\n\n")
        fh.write(check_txt.rstrip() + "\n\n")
        fh.write("## Texture composition\n\n")
        fh.write(tex_txt.rstrip() + "\n\n")
        fh.write("## Bug report (crash triage)\n\n")
        fh.write(bug_txt.rstrip() + "\n")
    written.insert(0, combined)
    return written
