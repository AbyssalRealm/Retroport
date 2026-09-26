"""Command-line interface.

    retroport scan <dir> [--report] [--skel-only]   inventory / readiness / find .skel models
    retroport inspect <file>                         full classification of one file
    retroport deps <m2>                              list a model's .skel/.anim/.bone deps
    retroport reinline <m2> --out <dir> [--json]     re-inline a .skel model (Milestones A+B)
    retroport check <md20> [--json]                  WotLK render-blocker diagnostics on converted output

Designed to run today against any folder. Point it at a wow.export extraction
to get a real asset inventory and a list of what still needs conversion.
"""

from __future__ import annotations

import json
import os
import sys

# Windows consoles default to cp1252 and raise UnicodeEncodeError on non-ASCII output
# (e.g. a texture path with an odd byte). Prefer UTF-8 where the runtime allows it.
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")   # type: ignore[union-attr]
except (AttributeError, ValueError):
    pass
from collections import Counter

import typer
from rich.console import Console
from rich.table import Table

from .models import AssetType, Severity
from .scan.scanner import classify_file
from .validate.rules import validate
from .convert.skel_reinliner import reinline_file
from .convert.loaders import make_file_id_loader, make_manifest_loader, find_manifest
from .check import check_model, format_report
from .bugreport import build_report
from .texreport import build_texture_report
from .report import write_reports

app = typer.Typer(add_completion=False, help="Retroport modern WoW assets to 3.3.5a.")
console = Console()

_SEV_STYLE = {Severity.PASS: "green", Severity.WARN: "yellow", Severity.FAIL: "red"}


def _walk(root: str):
    if os.path.isfile(root):
        yield root
        return
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            yield os.path.join(dirpath, name)


@app.command()
def scan(
    path: str = typer.Argument(..., help="File or directory to scan."),
    report: bool = typer.Option(False, "--report", help="Also run readiness validation."),
    json_out: str = typer.Option(None, "--json", help="Write the full AssetInfo list to JSON."),
    show_unknown: bool = typer.Option(False, "--unknown", help="Include UNKNOWN files in the type table."),
    skel_only: bool = typer.Option(False, "--skel-only", help="List only .skel-based models (re-inliner targets)."),
) -> None:
    if not os.path.exists(path):
        console.print(f"[red]Path not found:[/] {path}")
        console.print("[dim]In Git Bash, use forward slashes (I:/WoWDownPort/...) or quote backslash paths.[/]")
        raise typer.Exit(code=2)

    infos = [classify_file(p) for p in _walk(path)]
    if not infos:
        console.print(f"[yellow]No files found under[/] {path}")
        raise typer.Exit(code=0)

    if skel_only:
        skels = [i for i in infos if i.uses_skel or i.type is AssetType.SKEL]
        if not skels:
            console.print(f"[yellow]No .skel-based models found under[/] {path}")
            console.print("[dim]Characters and Shadowlands+ creatures are the usual cases.[/]")
            raise typer.Exit(code=0)
        t = Table(title=f".skel-based models  ({len(skels)})  — feed an m2 row to `retroport reinline`")
        t.add_column("path", overflow="fold"); t.add_column("type"); t.add_column("version", justify="right")
        for i in sorted(skels, key=lambda x: x.path):
            t.add_row(i.path, i.type.value, str(i.version) if i.version else "-")
        console.print(t)
        raise typer.Exit(code=0)


    # Type breakdown
    by_type = Counter(i.type.value for i in infos)
    t = Table(title=f"Asset inventory  ({len(infos)} files)")
    t.add_column("type"); t.add_column("count", justify="right")
    t.add_column("chunked M2", justify="right"); t.add_column("uses .skel", justify="right")
    for atype, count in by_type.most_common():
        if atype == AssetType.UNKNOWN.value and not show_unknown:
            continue
        same = [i for i in infos if i.type.value == atype]
        chunked = sum(1 for i in same if i.chunked)
        skel = sum(1 for i in same if i.uses_skel)
        t.add_row(atype, str(count), str(chunked) if chunked else "-", str(skel) if skel else "-")
    console.print(t)

    # Expansion breakdown (M2 only, where the version word means something)
    exps = Counter(i.expansion for i in infos if i.expansion)
    if exps:
        e = Table(title="M2 by expansion")
        e.add_column("expansion"); e.add_column("count", justify="right")
        for name, count in exps.most_common():
            e.add_row(name, str(count))
        console.print(e)

    # Readiness summary
    not_ready = [i for i in infos if i.type is not AssetType.UNKNOWN and not i.wotlk_ready]
    ready = sum(1 for i in infos if i.wotlk_ready)
    console.print(f"[green]{ready}[/] already WotLK-ready, "
                  f"[yellow]{len(not_ready)}[/] need conversion, "
                  f"[dim]{by_type.get('unknown', 0)} unknown[/]")

    if report and not_ready:
        r = Table(title="Conversion blockers")
        r.add_column("file", overflow="fold"); r.add_column("type"); r.add_column("findings")
        for i in not_ready[:200]:
            rep = validate(i)
            issues = ", ".join(
                f"[{_SEV_STYLE[f.severity]}]{f.check}[/]" for f in rep.findings
                if f.severity is not Severity.PASS
            )
            r.add_row(os.path.basename(i.path), i.type.value, issues or "-")
        console.print(r)
        if len(not_ready) > 200:
            console.print(f"[dim]... and {len(not_ready) - 200} more[/]")

    if json_out:
        with open(json_out, "w", encoding="utf-8") as fh:
            json.dump([i.model_dump() for i in infos], fh, indent=2)
        console.print(f"[dim]Wrote {json_out}[/]")


@app.command()
def inspect(path: str = typer.Argument(..., help="A single file to classify.")) -> None:
    if not os.path.isfile(path):
        console.print(f"[red]File not found:[/] {path}")
        console.print("[dim]In Git Bash, use forward slashes (I:/...) or quote backslash paths.[/]")
        raise typer.Exit(code=2)
    info = classify_file(path)
    console.print_json(info.model_dump_json(indent=2))
    rep = validate(info)
    console.print(f"\nReadiness: worst = [{_SEV_STYLE[rep.worst]}]{rep.worst.value}[/]")
    for f in rep.findings:
        console.print(f"  [{_SEV_STYLE[f.severity]}]{f.severity.value:4}[/] {f.check}: {f.detail}")


def _emit_json(command: str, ok: bool, result, error: str | None = None) -> None:
    """Stable JSON contract for programmatic callers (e.g. the Warcrafter UI)."""
    payload = {
        "ok": ok,
        "command": command,
        "error": error,
        "result": result.model_dump(mode="json") if result is not None else None,
    }
    print(json.dumps(payload, indent=2))


def _resolve_loaders(model: str, source: str, meta: str | None, no_anim: bool):
    """Pick the manifest loader (preferred) or the directory id-scan loader.
    Returns (skel_loader, anim_loader, description)."""
    meta = meta or find_manifest(model)
    if meta and os.path.isfile(meta):
        loader = make_manifest_loader(meta)        # resolves any FileDataID -> bytes
        anim_loader = None if no_anim else loader
        return loader, anim_loader, f"manifest {os.path.basename(meta)}"
    skel_loader = make_file_id_loader(source, ".skel")
    anim_loader = None if no_anim else make_file_id_loader(source, ".anim")
    return skel_loader, anim_loader, f"directory scan of {source}"


@app.command()
def reinline(
    model: str = typer.Argument(..., help="The modern .m2 (chunked, .skel-based) to convert."),
    source: str = typer.Option(".", "--source", help="Extraction root for the .skel/.anim files (fallback when no manifest)."),
    out: str = typer.Option(..., "--out", help="Directory to write the converted .m2 into."),
    meta: str = typer.Option(None, "--meta", help="wow.export manifest .json (auto-detected next to the model if omitted)."),
    json_out: bool = typer.Option(False, "--json", help="Emit the JSON result envelope to stdout."),
    no_anim: bool = typer.Option(False, "--no-anim", help="Structural re-inline only (skip the animation pass)."),
) -> None:
    """Re-inline a .skel model into a self-contained M2 (Milestones A + B)."""
    if not os.path.isfile(model):
        if json_out:
            _emit_json("reinline", False, None, f"model not found: {model}")
        else:
            console.print(f"[red]Model not found:[/] {model}")
        raise typer.Exit(code=2)

    skel_loader, anim_loader, how = _resolve_loaders(model, source, meta, no_anim)
    cr = reinline_file(model, skel_loader, out, anim_loader=anim_loader)

    if json_out:
        _emit_json("reinline", cr.success, cr, cr.stderr)
        raise typer.Exit(code=0 if cr.success else 1)

    style = "green" if cr.success else "red"
    console.print(f"[{style}]{'OK' if cr.success else 'FAILED'}[/]  {os.path.basename(model)}  [dim]({how})[/]")
    if cr.output:
        console.print(f"  output: {cr.output}")
    if cr.stderr:
        console.print(f"  [red]{cr.stderr}[/]")
    if cr.report:
        for f in cr.report.findings:
            console.print(f"  [{_SEV_STYLE[f.severity]}]{f.severity.value:4}[/] {f.check}: {f.detail}")
    raise typer.Exit(code=0 if cr.success else 1)


@app.command()
def deps(
    model: str = typer.Argument(..., help="A .m2 whose dependency files you want to check."),
    meta: str = typer.Option(None, "--meta", help="wow.export manifest .json (auto-detected next to the model if omitted)."),
) -> None:
    """List a model's .skel/.anim/.bone dependencies and whether each is present."""
    if not os.path.isfile(model):
        console.print(f"[red]Model not found:[/] {model}")
        raise typer.Exit(code=2)

    manifest = meta or find_manifest(model)
    if not manifest or not os.path.isfile(manifest):
        console.print("[yellow]No manifest found next to the model.[/]")
        console.print("[dim]Re-export from wow.export with the .skel/.anim options on, or pass --meta.[/]")
        raise typer.Exit(code=1)

    with open(manifest, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    base = os.path.dirname(os.path.abspath(manifest))

    rows: list[tuple[str, dict]] = []
    if data.get("skeleton"):
        rows.append(("skel", data["skeleton"]))
    rows += [("anim", e) for e in data.get("skelAnims", [])]
    rows += [("bone", e) for e in data.get("skelBones", [])]

    t = Table(title=f"Dependencies for {os.path.basename(model)}  (manifest: {os.path.basename(manifest)})")
    t.add_column("kind"); t.add_column("fileDataID", justify="right")
    t.add_column("file", overflow="fold"); t.add_column("present", justify="center")
    present = 0
    for kind, e in rows:
        ok = os.path.isfile(os.path.join(base, e.get("file", "")))
        present += ok
        t.add_row(kind, str(e.get("fileDataID", "?")), e.get("file", "?"),
                  "[green]Y[/]" if ok else "[red]N[/]")
    console.print(t)
    missing = len(rows) - present
    style = "green" if missing == 0 else "yellow"
    console.print(f"[{style}]{present}/{len(rows)} present[/]"
                  + ("" if missing == 0 else f", [red]{missing} missing[/] — export the missing ids from wow.export"))
    if missing == 0:
        console.print("[dim]All dependencies present. Run: retroport reinline <model> --out <dir> --json[/]")


@app.command()
def check(
    model: str = typer.Argument(..., help="A converted (MD20) .m2 to check for WotLK compatibility."),
    skin_dir: str = typer.Option(None, "--skin-dir", help="Where the .skin files are (defaults to the model's folder)."),
    json_out: bool = typer.Option(False, "--json", help="Emit the JSON result envelope to stdout."),
) -> None:
    """Diagnose render blockers in a converted model: skin count, geosets, combiner flag, shaders, bones."""
    if not os.path.isfile(model):
        if json_out:
            _emit_json("check", False, None, f"model not found: {model}")
        else:
            console.print(f"[red]Model not found:[/] {model}")
        raise typer.Exit(code=2)

    try:
        res = check_model(model, skin_dir=skin_dir)
    except ValueError as exc:
        if json_out:
            _emit_json("check", False, None, str(exc))
        else:
            console.print(f"[red]{exc}[/]")
        raise typer.Exit(code=2)

    if json_out:
        rep = res.report()
        ok = all(f.severity is not Severity.FAIL for f in rep.findings)
        _emit_json("check", ok, rep)
        raise typer.Exit(code=0 if ok else 1)

    console.print(f"[bold]{os.path.basename(model)}[/]")
    console.print(format_report(res))
    console.print()
    for f in res.report().findings:
        console.print(f"  [{_SEV_STYLE[f.severity]}]{f.severity.value:4}[/] {f.check}: {f.detail}")


@app.command()
def bugreport(
    model: str = typer.Argument(..., help="The model to report on (raw modern .m2 for WXL testing)."),
    error: str = typer.Option("", "--error", help="The WoWError text (inline)."),
    error_file: str = typer.Option("", "--error-file", help="Read the WoWError from a file."),
    skin_dir: str = typer.Option(None, "--skin-dir", help="Where the .skin files are (defaults to the model's folder)."),
    out: str = typer.Option("", "--out", help="Write the report to a file as well as printing it."),
) -> None:
    """Build a #Bug-Report-ready summary (check diagnosis + WoWError) for WarcraftXL testing."""
    if not os.path.isfile(model):
        console.print(f"[red]Model not found:[/] {model}")
        raise typer.Exit(code=2)
    err = error
    if error_file:
        try:
            with open(error_file, "r", encoding="utf-8", errors="replace") as fh:
                err = fh.read()
        except OSError as exc:
            console.print(f"[red]Could not read error file:[/] {exc}")
            raise typer.Exit(code=2)

    report = build_report(model, error_text=err, skin_dir=skin_dir)
    print(report)                      # plain print: report is markdown, not rich markup
    if out:
        try:
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(report + "\n")
            console.print(f"\n[green]Written:[/] {out}")
        except OSError as exc:
            console.print(f"[red]Could not write:[/] {exc}")
            raise typer.Exit(code=2)


@app.command()
def texreport(
    model: str = typer.Argument(..., help="The model whose textures to report on."),
    manifest: str = typer.Option(None, "--manifest", help="wow.export .manifest.json (auto-found next to the model if omitted)."),
    out: str = typer.Option("", "--out", help="Write the report to a file as well as printing it."),
) -> None:
    """Report which textures come from CharSections (runtime) vs the model (hardcoded)."""
    if not os.path.isfile(model):
        console.print(f"[red]Model not found:[/] {model}")
        raise typer.Exit(code=2)
    report = build_texture_report(model, manifest_path=manifest)
    print(report)                      # plain print: report is markdown, not rich markup
    if out:
        try:
            with open(out, "w", encoding="utf-8") as fh:
                fh.write(report + "\n")
            console.print(f"\n[green]Written:[/] {out}")
        except OSError as exc:
            console.print(f"[red]Could not write:[/] {exc}")
            raise typer.Exit(code=2)


@app.command()
def report(
    model: str = typer.Argument(..., help="The model to report on."),
    out_dir: str = typer.Option("reports", "--out-dir", "-o", help="Folder to write reports into (a per-model subfolder is created)."),
    error: str = typer.Option("", "--error", help="WoWError text (inline) for the bug report."),
    error_file: str = typer.Option("", "--error-file", help="Read the WoWError from a file."),
    manifest: str = typer.Option(None, "--manifest", help="wow.export .manifest.json (auto-found next to the model if omitted)."),
    skin_dir: str = typer.Option(None, "--skin-dir", help="Where the .skin files are (defaults to the model's folder)."),
) -> None:
    """Write check + texture + bug reports for a model into one folder (for comparisons)."""
    if not os.path.isfile(model):
        console.print(f"[red]Model not found:[/] {model}")
        raise typer.Exit(code=2)
    err = error
    if error_file:
        try:
            with open(error_file, "r", encoding="utf-8", errors="replace") as fh:
                err = fh.read()
        except OSError as exc:
            console.print(f"[red]Could not read error file:[/] {exc}")
            raise typer.Exit(code=2)

    written = write_reports(model, out_dir, error_text=err, manifest_path=manifest, skin_dir=skin_dir)
    console.print(f"[green]Wrote {len(written)} file(s):[/]")
    for p in written:
        console.print(f"  {p}")
    console.print(f"\nCombined report: [bold]{written[0]}[/]")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
