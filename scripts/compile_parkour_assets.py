"""Offline, anchor-driven parkour cutting; Pillow/numpy are build-only.

Default: --layout output/v09/parkour/layout.json -> output/v09/parkour/candidates.
Use --write-template to prepare six poses without inventing anatomical anchors.
Only --accept-reviewed --install together may replace assets/parkour; the old
asset directory is preserved in output/v09/parkour/install-backups.

nominal_rect is [left, top, right, bottom] in source-board physical pixels,
with right/bottom exclusive. source_anchor and mouth also use global board
pixels. scale is ONE uniform output-pixels/source-pixels factor. target_anchor
uses output physical pixels. Mouth metadata is mapped by that same affine,
then divided by DPR2. No original RGB, alpha, or source file is modified.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import shutil
import sys
import tempfile

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.compose_black_coat import _authored_cell

WORK = ROOT / "output/v09/parkour"
DESTINATION = ROOT / "backyard_visitor/assets/parkour"
PIXEL_SIZE = (288, 224)
KINDS = ("jump_up", "jump_down", "climb")
VARIANTS = ("base", "black")
GEOMETRY_MODE = "authored-alpha-uniform-anchor"


def _hash_bytes(raw):
    return hashlib.sha256(raw).hexdigest()


def _hash(path):
    return _hash_bytes(path.read_bytes())


def _json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _point(value, name):
    if not isinstance(value, list) or len(value) != 2 or any(not _finite(n) for n in value):
        raise ValueError(f"{name} must contain two finite coordinates; fill the reviewed layout anchors first")
    return tuple(float(n) for n in value)


def _bounds(alpha):
    yy, xx = np.nonzero(alpha)
    return [int(xx.min()), int(yy.min()), int(xx.max()) + 1, int(yy.max()) + 1] if len(xx) else None


def _resolve_source(value, layout):
    if not isinstance(value, str) or not value:
        raise ValueError("Source path is required")
    path = Path(value).expanduser()
    return (path if path.is_absolute() else layout.parent / path).resolve()


def _require_output(path):
    path = path.resolve()
    if not path.is_relative_to((ROOT / "output").resolve()):
        raise ValueError("Candidate/report output must stay inside this project's output directory")
    return path


def write_template(path):
    if path.exists():
        raise ValueError(f"Refusing to replace an existing layout: {path}")
    frames = []
    for ordinal, (kind, index) in enumerate((k, i) for k in KINDS for i in (0, 1)):
        variants = {}
        for coat in VARIANTS:
            source = WORK / (("parkour-" if ordinal < 4 else "climb-") + coat + ".png")
            nominal = None
            if source.is_file():
                with Image.open(source) as image:
                    cols, rows, cell = (3, 2, ordinal) if ordinal < 4 else (2, 1, index)
                    col, row = cell % cols, cell // cols
                    nominal = [round(col * image.width / cols), round(row * image.height / rows),
                               round((col+1) * image.width / cols), round((row+1) * image.height / rows)]
            variants[coat] = dict(path=str(source), nominal_rect=nominal,
                                  source_anchor=None, mouth=None, scale=None, target_anchor=None)
        frames.append(dict(kind=kind, index=index, variants=variants))
    _json(path, dict(schema_version=1, kind="backyard-visitor-parkour-layout", frames=frames,
                    coordinate_notes={"nominal_rect": "global source [L,T,R,B], right/bottom exclusive",
                                      "source_anchor_and_mouth": "global source pixels; manual anatomical review required",
                                      "target_anchor": "output physical pixels on 288x224 canvas",
                                      "scale": "one positive uniform output/source ratio; no independent x/y scale",
                                      "climb_contact": "native left wall at output physical x176 (logical x88); mirror logical x56",
                                      "review": "grid cells are nominal only; anchors and final acceptance are not inferred"}))
    return path


def _layout_rows(data):
    if data.get("schema_version") != 1 or data.get("kind") != "backyard-visitor-parkour-layout":
        raise ValueError("Unsupported parkour layout schema")
    frames = data.get("frames")
    if not isinstance(frames, list) or len(frames) != 6:
        raise ValueError("Layout must contain all six poses")
    seen = set()
    for row in frames:
        kind, index = row.get("kind"), row.get("index")
        if kind not in KINDS or type(index) is not int or index not in (0, 1) or (kind, index) in seen:
            raise ValueError("Layout has an invalid or duplicated pose")
        if not isinstance(row.get("variants"), dict) or set(row["variants"]) != set(VARIANTS):
            raise ValueError("Each pose must contain base and independently authored black sources")
        seen.add((kind, index))
    return frames


def compose_frame(info, layout):
    """Return image, runtime metadata, and exact source/affine provenance."""
    source = _resolve_source(info.get("path"), layout)
    raw = source.read_bytes()
    with Image.open(BytesIO(raw)) as opened:
        source_mode = opened.mode
        if "A" not in opened.getbands() and "transparency" not in opened.info:
            raise ValueError(f"Source has no alpha channel: {source.name}")
        board = opened.convert("RGBA")
    board_alpha = np.asarray(board, dtype=np.uint8)[:, :, 3]
    board_opaque_fraction = float(np.mean(board_alpha >= 250))
    if board_opaque_fraction >= .98:
        raise ValueError(f"Source has at least 98% solid background: {source.name}")
    rect = info.get("nominal_rect")
    if (not isinstance(rect, list) or len(rect) != 4 or any(type(n) is not int for n in rect)
            or not 0 <= rect[0] < rect[2] <= board.width
            or not 0 <= rect[1] < rect[3] <= board.height):
        raise ValueError("nominal_rect must be an in-bounds [L,T,R,B] source rectangle")
    anchor = _point(info.get("source_anchor"), "source_anchor")
    mouth = _point(info.get("mouth"), "mouth")
    target = _point(info.get("target_anchor"), "target_anchor")
    if any(not (0 <= point[0] <= board.width and 0 <= point[1] <= board.height) for point in (anchor, mouth)):
        raise ValueError("Source anchor/mouth must lie within the source board")
    if not 0 <= target[0] <= PIXEL_SIZE[0] or not 0 <= target[1] <= PIXEL_SIZE[1]:
        raise ValueError("target_anchor must lie within the output canvas")
    scale = info.get("scale")
    if not _finite(scale) or not 0 < scale <= 8:
        raise ValueError("scale must be a positive finite uniform factor <= 8")
    cell, crop = _authored_cell(board, tuple(rect))
    if crop["subject_touches_expanded_sample_boundary"]:
        raise ValueError("Selected body touches the 15% expanded sample boundary; clipping requires a revised layout/source")
    expanded = crop["expanded_sample_bounds_in_board"]
    # Pillow's AFFINE maps output -> input; coordinates here refer to the
    # expanded crop. The same forward mapping is used for the mouth below.
    affine = (1/scale, 0., anchor[0]-expanded[0]-target[0]/scale,
              0., 1/scale, anchor[1]-expanded[1]-target[1]/scale)
    forward = (scale, 0., target[0]-scale*anchor[0], 0., scale, target[1]-scale*anchor[1])
    global_bbox = crop["selected_body_bbox_in_board"]
    projected = [target[0]+scale*(global_bbox[0]-anchor[0]),
                 target[1]+scale*(global_bbox[1]-anchor[1]),
                 target[0]+scale*(global_bbox[2]-anchor[0]),
                 target[1]+scale*(global_bbox[3]-anchor[1])]
    if not 0 <= projected[0] < projected[2] <= PIXEL_SIZE[0] or not 0 <= projected[1] < projected[3] <= PIXEL_SIZE[1]:
        raise ValueError("Uniform mapping clips the selected body outside the output canvas")
    # Render a padded canvas and inspect the outside, catching interpolation
    # fringes as well as the geometric bbox. Never silently crop a paw or tail.
    pad = max(8, math.ceil(4*scale))
    padded_affine = (affine[0], 0., affine[2]-pad/scale, 0., affine[4], affine[5]-pad/scale)
    padded = cell.convert("RGBa").transform((PIXEL_SIZE[0]+2*pad, PIXEL_SIZE[1]+2*pad),
                Image.Transform.AFFINE, padded_affine, resample=Image.Resampling.BICUBIC,
                fillcolor=(0, 0, 0, 0)).convert("RGBA")
    padded_alpha = np.asarray(padded, dtype=np.uint8)[:, :, 3]
    outside = padded_alpha.copy()
    outside[pad:pad+PIXEL_SIZE[1], pad:pad+PIXEL_SIZE[0]] = 0
    clipped_pixels = int(np.count_nonzero(outside))
    if clipped_pixels:
        raise ValueError(f"Uniform mapping clips {clipped_pixels} interpolated alpha pixels at the canvas edge")
    output = padded.crop((pad, pad, pad+PIXEL_SIZE[0], pad+PIXEL_SIZE[1]))
    alpha = np.asarray(output, dtype=np.uint8)[:, :, 3]
    bounds = _bounds(alpha)
    if bounds is None or float(np.mean(alpha >= 250)) >= .98:
        raise ValueError("Output must have a nonempty transparent silhouette and less than 98% solid coverage")
    mapped_mouth = [(target[i]+scale*(mouth[i]-anchor[i]))/2 for i in (0, 1)]
    if not 0 <= mapped_mouth[0] <= 144 or not 0 <= mapped_mouth[1] <= 112:
        raise ValueError("Mapped mouth lies outside the logical output canvas")
    alpha_sha = _hash_bytes(alpha.tobytes(order="C"))
    metadata = dict(alpha_bounds=bounds, mouth_anchor=mapped_mouth)
    report = dict(source_path=str(source), source_sha256=_hash_bytes(raw), source_mode=source_mode,
                  source_size=list(board.size), source_has_alpha=True,
                  source_board_opaque_fraction=board_opaque_fraction, source_anchor=list(anchor),
                  source_mouth=list(mouth), target_anchor=list(target), uniform_scale=scale,
                  geometry_mode=GEOMETRY_MODE, premultiplied_mode="RGBa", output_mode="RGBA",
                  output_to_crop_affine=list(affine), source_to_output_affine=list(forward),
                  projected_subject_bounds=projected, crop=crop,
                  selected_crop_alpha_sha256=_hash_bytes(np.asarray(cell, dtype=np.uint8)[:, :, 3].tobytes(order="C")),
                  output_alpha_sha256=alpha_sha, output_alpha_bounds=bounds,
                  output_nonzero_alpha_pixels=int(np.count_nonzero(alpha)),
                  output_opaque_fraction=float(np.mean(alpha >= 250)),
                  canvas_clipped_alpha_pixels=clipped_pixels, mapped_mouth_logical=mapped_mouth,
                  recolored=False, nonuniform_scale=False, source_files_modified=False)
    return output, metadata, report


def _install(candidate, manifest):
    destination = DESTINATION
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".parkour-stage-", dir=destination.parent))
    backup = None
    try:
        for row in manifest["frames"]:
            for info in row["variants"].values():
                source = candidate / info["file"]
                if _hash(source) != info["sha256"]:
                    raise ValueError("Candidate changed before installation")
                shutil.copy2(source, stage / source.name)
                if _hash(stage / source.name) != info["sha256"]:
                    raise ValueError("Staged PNG checksum mismatch")
        _json(stage / "manifest.json", manifest)
        if destination.exists():
            backups = WORK / "install-backups"
            backups.mkdir(parents=True, exist_ok=True)
            backup = Path(tempfile.mkdtemp(prefix=datetime.now().strftime("%Y%m%d-%H%M%S-"), dir=backups))
            backup.rmdir()
            destination.rename(backup)
        try:
            stage.rename(destination)
        except BaseException:
            if backup is not None:
                backup.rename(destination)
            raise
        return str(backup) if backup is not None else None
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def compile_layout(layout, output, *, accept_reviewed=False, install=False):
    layout = Path(layout).resolve()
    output = _require_output(Path(output))
    if install and not accept_reviewed:
        raise ValueError("Installation requires --accept-reviewed AND --install after visual review")
    layout_bytes = layout.read_bytes()
    data = json.loads(layout_bytes)
    frames = _layout_rows(data)
    for row in frames:
        for info in row["variants"].values():
            if _resolve_source(info.get("path"), layout).is_relative_to(output):
                raise ValueError("Candidate output must not contain any original source image")
    output.mkdir(parents=True, exist_ok=True)
    manifest = dict(schema_version=1, kind="backyard-visitor-parkour", accepted=False,
                    display_size=[144, 112], pixel_size=list(PIXEL_SIZE), pixel_ratio=2,
                    native_facing=-1, geometry_mode=GEOMETRY_MODE, frames=[])
    report = dict(schema_version=1, created_at=datetime.now(timezone.utc).isoformat(),
                  layout_path=str(layout), layout_sha256=_hash_bytes(layout_bytes),
                  geometry_mode=GEOMETRY_MODE, visual_review_acknowledged=accept_reviewed,
                  install_requested=install, installed=False, frame_count=0, errors=[], frames=[])
    for row in frames:
        variants = {}
        for coat in VARIANTS:
            label = f"{row['kind']}-{row['index']:02d}-{coat}"
            try:
                image, metadata, evidence = compose_frame(row["variants"][coat], layout)
                name = label + "@2x.png"
                image.save(output / name, "PNG")
                metadata.update(file=name, sha256=_hash(output / name),
                                climb_contact_x=88 if row["kind"] == "climb" else None)
                variants[coat] = metadata
                report["frames"].append(dict(kind=row["kind"], index=row["index"], variant=coat,
                                             output_file=name, output_sha256=metadata["sha256"], **evidence))
            except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
                report["errors"].append(dict(frame=label, error=str(error)))
        if len(variants) == 2:
            manifest["frames"].append(dict(kind=row["kind"], index=row["index"], variants=variants))
    report["frame_count"] = len(report["frames"])
    complete = not report["errors"] and len(manifest["frames"]) == 6 and report["frame_count"] == 12
    manifest["accepted"] = bool(complete and accept_reviewed)
    report["complete"] = complete
    _json(output / "manifest.json", manifest)
    _json(output / "compile-report.json", report)
    if not complete:
        raise ValueError(f"Candidate incomplete: {report['frame_count']}/12 PNGs; see {output / 'compile-report.json'}")
    if install:
        report["previous_assets_backup"] = _install(output, manifest)
        report["installed"] = True
        _json(output / "compile-report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--layout", type=Path, default=WORK / "layout.json")
    parser.add_argument("--output", type=Path, default=WORK / "candidates")
    parser.add_argument("--write-template", nargs="?", const=WORK / "layout.template.json", type=Path)
    parser.add_argument("--accept-reviewed", action="store_true")
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    if args.install and not args.accept_reviewed:
        parser.error("--install requires --accept-reviewed after visual review")
    try:
        if args.write_template is not None:
            if args.install or args.accept_reviewed:
                raise ValueError("Template creation cannot approve or install artwork")
            print(write_template(_require_output(args.write_template)))
        else:
            report = compile_layout(args.layout, args.output, accept_reviewed=args.accept_reviewed, install=args.install)
            print(json.dumps({"frame_count": report["frame_count"], "complete": report["complete"],
                              "reviewed": args.accept_reviewed, "installed": report["installed"],
                              "output": str(args.output.resolve())}))
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as error:
        parser.exit(1, f"Parkour compile failed: {error}\n")


if __name__ == "__main__":
    main()
