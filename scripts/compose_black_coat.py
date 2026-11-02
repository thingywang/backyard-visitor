"""Offline black-fur composition, with original or authored transparent edges.

Run with the bundled workspace Python (Pillow + numpy); neither is an app
runtime dependency. The original RGBA sprites and their exported keys are
never modified. Default output is a candidate set, not production assets.

Examples:
    python scripts/compose_black_coat.py --board 1
    python scripts/compose_black_coat.py --authored-edge --board 3
    python scripts/compose_black_coat.py
    python scripts/compose_black_coat.py --install

Every generated board must exist before installation. Frames requiring review
block installation unless --accept-reviewed is explicitly supplied after visual
inspection. That override is recorded; it never changes review metrics to pass.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

import numpy as np
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORK = ROOT / 'output/v09/black-cat'
DEFAULT_INSTALL = ROOT / 'backyard_visitor/assets/black-coat'
KEY_RE = re.compile(r'[0-9a-f]{64}\Z')
MISSING_LIMIT = .10
SUPPORT_ALPHA = 8.0 / 255.0
FILL_RGB = (18, 20, 23)
TRUSTED_ALPHA = 64.0 / 255.0
HIGH_ALPHA = 128
HIGH_ALPHA_DISTANCE_REVIEW = 6.0


def _hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def _main_bbox(alpha, *, with_mask=False, with_components=False):
    """Largest 8-connected nonzero-alpha component, using scanline runs.

    This rejects detached generation specks when finding the alignment box;
    all original pixels, including detached hairs, keep their original alpha.
    """
    parents, spans, previous = [], [], []

    def find(label):
        while parents[label] != label:
            parents[label] = parents[parents[label]]
            label = parents[label]
        return label

    for y, row in enumerate(alpha > 0):
        edges = np.diff(np.pad(row.astype(np.int8), (1, 1)))
        starts, ends = np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)
        current, cursor = [], 0
        for left, right in zip(starts.tolist(), ends.tolist()):
            label = len(parents)
            parents.append(label)
            while cursor < len(previous) and previous[cursor][1] < left:
                cursor += 1
            index = cursor
            while index < len(previous) and previous[index][0] <= right:
                parents[find(previous[index][2])] = find(label)
                index += 1
            current.append((left, right, label))
            spans.append((label, left, right, y))
        previous = current
    if not spans:
        if with_components:
            return []
        if with_mask:
            return None, 0.0, np.zeros(alpha.shape, dtype=bool), []
        return None, 0.0
    components = {}
    for label, left, right, y in spans:
        root = find(label)
        if root not in components:
            components[root] = [left, y, right, y + 1, 0]
        box = components[root]
        box[0], box[1] = min(box[0], left), min(box[1], y)
        box[2], box[3] = max(box[2], right), max(box[3], y + 1)
        box[4] += right - left
    if with_components:
        grouped = {key: {'bbox': tuple(value[:4]), 'pixels': value[4], 'spans': []}
                   for key, value in components.items()}
        for label, left, right, y in spans:
            grouped[find(label)]['spans'].append((left, right, y))
        return list(grouped.values())
    main = max(components, key=lambda key: components[key][4])
    body = components[main]
    fraction = body[4] / max(1, int(np.count_nonzero(alpha)))
    if with_mask:
        mask = np.zeros(alpha.shape, dtype=bool)
        for label, left, right, y in spans:
            if find(label) == main:
                mask[y, left:right] = True
        islands = sorted((item[4] for key, item in components.items() if key != main), reverse=True)
        return tuple(body[:4]), fraction, mask, islands
    return tuple(body[:4]), fraction



def _edt_1d(values):
    """Exact squared Euclidean distances and source indices in one dimension."""
    length = len(values)
    finite = np.flatnonzero(np.isfinite(values))
    if not len(finite):
        return np.full(length, np.inf), np.full(length, -1, dtype=np.int32)
    envelope = np.empty(length, dtype=np.int32)
    boundaries = np.empty(length + 1, dtype=np.float64)
    envelope[0], boundaries[0], boundaries[1] = finite[0], -np.inf, np.inf
    end = 0
    for point in finite[1:]:
        previous = envelope[end]
        crossing = ((values[point] + point * point) -
                    (values[previous] + previous * previous)) / (2.0 * (point - previous))
        while crossing <= boundaries[end]:
            end -= 1
            previous = envelope[end]
            crossing = ((values[point] + point * point) -
                        (values[previous] + previous * previous)) / (2.0 * (point - previous))
        end += 1
        envelope[end], boundaries[end], boundaries[end + 1] = point, crossing, np.inf
    positions = np.arange(length)
    owners = envelope[np.searchsorted(boundaries[1:end + 1], positions, side='left')]
    distances = (positions - owners) ** 2 + values[owners]
    return distances, owners


def _nearest_trusted(trusted):
    """EDT lookup of original generated RGB, without changing either alpha mask.

    scipy is optional and build-time only. The separable fallback computes
    exact Euclidean distance too; distances are source-canvas pixels, not
    neighbourhood-iteration counts that underestimate diagonal gaps.
    """
    try:
        from scipy.ndimage import distance_transform_edt
    except ImportError:
        height, width = trusted.shape
        vertical = np.empty((height, width), dtype=np.float64)
        nearest_y = np.empty((height, width), dtype=np.int32)
        for x in range(width):
            vertical[:, x], nearest_y[:, x] = _edt_1d(np.where(trusted[:, x], 0.0, np.inf))
        distances = np.empty_like(vertical)
        owners_x = np.empty((height, width), dtype=np.int32)
        for y in range(height):
            distances[y], owners_x[y] = _edt_1d(vertical[y])
        owners_y = nearest_y[np.arange(height)[:, None], owners_x]
        return np.sqrt(distances), owners_y, owners_x, 'separable_exact_edt'
    distances, indices = distance_transform_edt(~trusted, return_indices=True)
    return distances, indices[0], indices[1], 'scipy_exact_edt'


def _distance_stats(values):
    if not len(values):
        return {'count': 0, 'mean': None, 'p95': None, 'maximum': None}
    return {'count': int(len(values)), 'mean': float(np.mean(values)),
            'p95': float(np.percentile(values, 95)), 'maximum': float(np.max(values))}


def _extend_generated_rgb(rgb, mapped_alpha, source_alpha, missing):
    """Extend only RGB from nearby trustworthy generated fur into missing areas.

    Raw coverage and shape-overlap measurements are computed before this
    cosmetic repair and remain authoritative. No source/tabby RGB is sampled.
    """
    trusted = mapped_alpha >= TRUSTED_ALPHA
    high_alpha_missing = missing & (source_alpha >= HIGH_ALPHA)
    report = {'method': 'not_needed', 'trusted_generated_alpha_threshold': TRUSTED_ALPHA,
              'high_source_alpha_threshold': HIGH_ALPHA,
              'high_alpha_distance_review_threshold_pixels': HIGH_ALPHA_DISTANCE_REVIEW,
              'filled_pixel_count': 0, 'unfilled_pixel_count': int(np.count_nonzero(missing)),
              'distance_pixels': _distance_stats([]),
              'high_alpha_distance_pixels': _distance_stats([]),
              'source_rgb_used': False}
    if not np.any(missing):
        return report
    if not np.any(trusted):
        report['method'] = 'constant_black_no_trusted_generated_rgb'
        return report
    distances, nearest_y, nearest_x, method = _nearest_trusted(trusted)
    rgb[missing] = rgb[nearest_y[missing], nearest_x[missing]]
    report.update(method=method, filled_pixel_count=int(np.count_nonzero(missing)),
                  unfilled_pixel_count=0, distance_pixels=_distance_stats(distances[missing]),
                  high_alpha_distance_pixels=_distance_stats(distances[high_alpha_missing]))
    return report


def compose(source, generated):
    """Map generated RGB to the source body box; copy source alpha exactly."""
    original = np.asarray(source.convert('RGBA'), dtype=np.uint8)
    colours = np.asarray(generated.convert('RGBA'), dtype=np.uint8)
    source_alpha, generated_alpha = original[:, :, 3], colours[:, :, 3]
    source_box, source_main_fraction = _main_bbox(source_alpha)
    generated_box, generated_main_fraction = _main_bbox(generated_alpha)
    if source_box is None:
        raise ValueError('Original frame has no visible alpha; cannot align it.')
    height, width = source_alpha.shape
    mapped_alpha = np.zeros((height, width), dtype=np.float32)
    rgb = np.empty((height, width, 3), dtype=np.float32)
    rgb[:] = FILL_RGB
    transform = None
    reasons = []
    if generated_box is None:
        reasons.append('generated_subject_missing')
    else:
        sl, st, sr, sb = source_box
        gl, gt, gr, gb = generated_box
        sx, sy = (gr - gl) / (sr - sl), (gb - gt) / (sb - st)
        transform = (sx, 0.0, gl - sx * sl, 0.0, sy, gt - sy * st)
        ga = generated_alpha.astype(np.float32) / 255.0

        def sample(channel):
            return np.asarray(Image.fromarray(channel.astype(np.float32), mode='F').transform(
                (width, height), Image.Transform.AFFINE, transform,
                resample=Image.Resampling.BILINEAR, fillcolor=0.0), dtype=np.float32)

        mapped_alpha = np.clip(sample(ga), 0.0, 1.0)
        usable = mapped_alpha >= SUPPORT_ALPHA
        # Premultiply before sampling, then unpremultiply; invisible RGB cannot
        # darken generated fur at the boundary or bleed another cell into it.
        for channel in range(3):
            weighted = sample(colours[:, :, channel].astype(np.float32) * ga)
            rgb[:, :, channel][usable] = weighted[usable] / mapped_alpha[usable]
        if np.count_nonzero(generated_alpha) / generated_alpha.size > .98:
            reasons.append('generated_cell_has_no_transparent_margin')
        if max(sx / sy, sy / sx) > 1.35:
            reasons.append('body_box_aspect_ratio_mismatch')
    visible = source_alpha > 0
    usable = mapped_alpha >= SUPPORT_ALPHA
    missing = visible & ~usable
    missing_fraction = float(np.count_nonzero(missing) / np.count_nonzero(visible))
    alpha_weighted_missing = float(source_alpha[missing].astype(np.float64).sum()
                                   / source_alpha.astype(np.float64).sum())
    if missing_fraction > MISSING_LIMIT:
        reasons.append('missing_rgb_coverage_above_10_percent')
    core = source_alpha >= 8
    union = np.count_nonzero(core | usable)
    support_iou = float(np.count_nonzero(core & usable) / max(1, union))
    if generated_box is not None and support_iou < .60:
        reasons.append('aligned_subject_shape_overlap_low')
    extension = _extend_generated_rgb(rgb, mapped_alpha, source_alpha, missing)
    high_distance = extension['high_alpha_distance_pixels']['maximum']
    if high_distance is not None and high_distance > HIGH_ALPHA_DISTANCE_REVIEW:
        reasons.append('high_alpha_fill_distance_above_6_pixels')
    if extension['unfilled_pixel_count']:
        reasons.append('missing_rgb_cannot_extend_without_trusted_generation')
    result = np.zeros_like(original)
    result[:, :, :3] = np.rint(np.clip(rgb, 0, 255)).astype(np.uint8)
    result[:, :, 3] = source_alpha
    result[~visible, :3] = 0
    assert np.array_equal(result[:, :, 3], source_alpha)
    values = result[:, :, :3][core]
    luma = values @ np.array([.2126, .7152, .0722]) if len(values) else np.array([0.0])
    return Image.fromarray(result, 'RGBA'), {
        'source_subject_bbox': source_box,
        'generated_subject_bbox_in_cell': generated_box,
        'source_main_component_fraction': source_main_fraction,
        'generated_main_component_fraction': generated_main_fraction,
        'output_to_generated_affine': transform,
        'source_visible_pixel_count': int(np.count_nonzero(visible)),
        'missing_rgb_pixel_count': int(np.count_nonzero(missing)),
        'missing_rgb_fraction': missing_fraction,
        'alpha_weighted_missing_fraction': alpha_weighted_missing,
        'missing_rgb_review_limit': MISSING_LIMIT,
        'generated_support_alpha_threshold': SUPPORT_ALPHA,
        'missing_fill_rgb': FILL_RGB,
        'missing_rgb_metrics_are_pre_extension': True,
        'rgb_extension': extension,
        'support_iou': support_iou,
        'support_iou_is_pre_extension': True,
        'alpha_identical_to_original': True,
        'luma_percentiles_5_50_95': np.percentile(luma, [5, 50, 95]).tolist(),
        'review_required': bool(reasons),
        'review_reasons': reasons,
    }




def _authored_cell(board, nominal_bounds):
    """Recover whole cats crossing grid lines, without retaining neighbours.

    Generated board layouts only approximately follow a 3x3 grid. Use a 15%
    crop margin and choose the connected body with the most pixels inside the
    nominal cell; neighbour overflow never determines the body alignment box.
    """
    left, top, right, bottom = nominal_bounds
    margin_x, margin_y = round((right - left) * .15), round((bottom - top) * .15)
    expanded = (max(0, left - margin_x), max(0, top - margin_y),
                min(board.width, right + margin_x), min(board.height, bottom + margin_y))
    rgba = np.array(board.crop(expanded).convert('RGBA'), dtype=np.uint8)
    support = np.where(rgba[:, :, 3] >= 8, rgba[:, :, 3], 0)
    components = _main_bbox(support, with_components=True)
    if not components:
        raise ValueError('No generated body in expanded cell.')
    nl, nt, nr, nb = (left - expanded[0], top - expanded[1],
                      right - expanded[0], bottom - expanded[1])

    def score(component):
        return sum(max(0, min(r, nr) - max(l, nl)) for l, r, y in component['spans'] if nt <= y < nb)

    selected = max(components, key=score)
    selected_score = score(selected)
    if selected_score == 0:
        raise ValueError('Expanded generated body does not enter the requested cell.')
    keep = np.zeros(rgba.shape[:2], dtype=bool)
    for l, r, y in selected['spans']:
        keep[y, l:r] = True
    rgba[~keep] = 0
    bbox = selected['bbox']
    touches = bbox[0] == 0 or bbox[1] == 0 or bbox[2] == rgba.shape[1] or bbox[3] == rgba.shape[0]
    return Image.fromarray(rgba, 'RGBA'), {
        'nominal_cell_bounds_in_board': list(nominal_bounds),
        'expanded_sample_bounds_in_board': list(expanded),
        'sample_margin_fraction': .15,
        'selected_body_bbox_in_board': [bbox[0] + expanded[0], bbox[1] + expanded[1],
                                        bbox[2] + expanded[0], bbox[3] + expanded[1]],
        'selected_body_pixel_count': selected['pixels'],
        'selected_body_pixel_count_in_nominal_cell': selected_score,
        'recovered_body_pixels_outside_nominal_cell': selected['pixels'] - selected_score,
        'excluded_component_sizes': sorted((c['pixels'] for c in components if c is not selected), reverse=True)[:20],
        'subject_touches_expanded_sample_boundary': touches,
        'source_rgb_used': False,
    }


def _bounds(alpha):
    ys, xs = np.nonzero(alpha)
    return [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if len(xs) else None


def _alpha_centroid(alpha):
    weights = alpha.astype(np.float64)
    total = weights.sum()
    if not total:
        return None
    ys, xs = np.indices(alpha.shape)
    return [float((xs * weights).sum() / total), float((ys * weights).sum() / total)]



def _rear_tail_root(alpha, bbox):
    """First narrow-tail row after the lower, broad seated body."""
    widths = np.count_nonzero(alpha >= 128, axis=1)
    first = bbox[1] + round((bbox[3] - bbox[1]) * .45)
    broad = np.flatnonzero(widths[first:bbox[3]] > .45 * widths.max())
    if not len(broad):
        return None
    root = first + int(broad[-1]) + 1
    return root if root < bbox[3] - 8 else None


def compose_authored(source, generated, strict_metrics, *, rear_seat_y=None):
    """Use generated fur and alpha in the original frame, not the tabby outline.

    The strict composition above remains an independent reference. Its raw
    overlap/coverage and review flags are retained, even though missing source
    pixels are intentionally no longer filled or clipped to the old silhouette.
    Only generated pixels participate in alpha cleanup and edge RGB repair.
    """
    original_alpha = np.asarray(source.convert('RGBA'), dtype=np.uint8)[:, :, 3]
    colours = np.asarray(generated.convert('RGBA'), dtype=np.uint8)
    raw_alpha = colours[:, :, 3]
    # Ignore barely visible generation pepper. Keep the principal connected
    # alpha>=8 body (including connected whiskers), not isolated colour specks.
    supported = np.where(raw_alpha >= 8, raw_alpha, 0).astype(np.uint8)
    generated_box, main_fraction, body_mask, islands = _main_bbox(supported, with_mask=True)
    if generated_box is None:
        raise ValueError('Authored generated frame has no alpha>=8 subject.')
    cleaned_alpha = np.where(body_mask, supported, 0).astype(np.uint8)
    source_box = strict_metrics['source_subject_bbox']
    sl, st, sr, sb = source_box
    gl, gt, gr, gb = generated_box
    # Fit generated body into the same source-canvas body rectangle. Bottom
    # edge and horizontal centre are the existing offline alignment anchors.
    sx, sy = (gr - gl) / (sr - sl), (gb - gt) / (sb - st)
    transform = (sx, 0.0, gl - sx * sl, 0.0, sy, gt - sy * st)
    height, width = original_alpha.shape
    ga = cleaned_alpha.astype(np.float32) / 255.0
    rear_alignment = None
    if rear_seat_y is not None:
        source_root = _rear_tail_root(original_alpha, source_box)
        generated_root = _rear_tail_root(cleaned_alpha, generated_box)
        if source_root is None or generated_root is None or abs(source_root - rear_seat_y) > 3:
            raise ValueError('Rear body/tail split could not be located near the runtime seat anchor.')
        body_sy = (generated_root - gt) / (source_root - st)
        tail_sy = (gb - generated_root) / (sb - source_root)
        body_transform = (sx, 0.0, gl - sx * sl, 0.0, body_sy, gt - body_sy * st)
        tail_transform = (sx, 0.0, gl - sx * sl, 0.0, tail_sy, generated_root - tail_sy * source_root)
        rear_alignment = {'runtime_seat_y_px': rear_seat_y, 'source_tail_root_y_px': source_root,
                          'generated_tail_root_y_in_cell': generated_root,
                          'body_output_to_generated_affine': body_transform,
                          'tail_output_to_generated_affine': tail_transform,
                          'source_rgb_used': False}

    def sample(channel):
        image = Image.fromarray(channel.astype(np.float32), mode='F')
        def mapped(affine):
            return np.asarray(image.transform((width, height), Image.Transform.AFFINE, affine,
                              resample=Image.Resampling.BILINEAR, fillcolor=0.0), dtype=np.float32)
        if rear_alignment is None:
            return mapped(transform)
        upper, lower = mapped(body_transform), mapped(tail_transform)
        return np.where(np.arange(height)[:, None] < source_root, upper, lower)

    mapped_alpha = np.clip(sample(ga), 0.0, 1.0)
    alpha = np.rint(mapped_alpha * 255).astype(np.uint8)
    alpha[alpha < 8] = 0
    visible = alpha > 0
    rgb = np.zeros((height, width, 3), dtype=np.float32)
    for channel in range(3):
        weighted = sample(colours[:, :, channel].astype(np.float32) * ga)
        rgb[:, :, channel][visible] = weighted[visible] / mapped_alpha[visible]
    # Odd coloured fringe is alpha-matting noise, not black fur. Only repair
    # conspicuous chromatic/white pixels in the translucent boundary, using
    # nearby opaque neutral generated fur; retain eyes, ears and body shading.
    luma = rgb @ np.array([.2126, .7152, .0722])
    chroma = rgb.max(axis=2) - rgb.min(axis=2)
    odd_fringe = visible & (alpha < 128) & (((chroma > 24) & (rgb.max(axis=2) > 45)) | (luma > 110))
    trusted = (alpha >= 200) & (chroma <= 25) & (luma <= 120)
    fringe_distances = np.array([])
    fringe_unfilled = int(np.count_nonzero(odd_fringe))
    if fringe_unfilled and np.any(trusted):
        distances, yy, xx, _ = _nearest_trusted(trusted)
        rgb[odd_fringe] = rgb[yy[odd_fringe], xx[odd_fringe]]
        fringe_distances = distances[odd_fringe]
        fringe_unfilled = 0
    result = np.zeros((height, width, 4), dtype=np.uint8)
    result[:, :, :3] = np.rint(np.clip(rgb, 0, 255)).astype(np.uint8)
    result[:, :, 3] = alpha
    result[~visible, :3] = 0
    # Bilinear thresholding can put the opaque paws 1-3 pixels below the
    # original floor despite matching full-alpha body bounds. Correct only a
    # measured integer offset offline, without resampling RGB/alpha or
    # shifting the runtime anchor. A larger correction retains a review flag.
    source_core_box, _ = _main_bbox(np.where(original_alpha >= 128, original_alpha, 0))
    initial_core_box, _ = _main_bbox(np.where(alpha >= 128, alpha, 0))
    initial_box = _bounds(alpha)
    initial_bottom_delta = (initial_core_box[3] - source_core_box[3]
                            if initial_core_box and source_core_box else None)
    anchor_limit = max(3.0, .025 * (sb - st))
    anchor_adjustment = 0
    if initial_bottom_delta is not None and abs(initial_bottom_delta) <= max(8.0, .05 * (sb - st)):
        dy = -initial_bottom_delta
        if initial_box[1] + dy >= 0 and initial_box[3] + dy <= height:
            shifted = np.zeros_like(result)
            first, last = max(0, dy), min(height, height + dy)
            shifted[first:last] = result[first - dy:last - dy]
            result = shifted
            alpha = result[:, :, 3]
            anchor_adjustment = dy
    output_box, output_fraction, _, output_islands = _main_bbox(alpha, with_mask=True)
    output_core_box, _ = _main_bbox(np.where(alpha >= 128, alpha, 0))
    if rear_alignment is not None:
        rear_alignment['output_tail_root_y_px'] = _rear_tail_root(alpha, output_box)
        rear_alignment['output_root_delta_to_runtime_seat_px'] = rear_alignment['output_tail_root_y_px'] - rear_seat_y
    source_centre, output_centre = _alpha_centroid(original_alpha), _alpha_centroid(alpha)
    width_ratio = (output_box[2] - output_box[0]) / (sr - sl)
    height_ratio = (output_box[3] - output_box[1]) / (sb - st)
    centre_delta = [output_centre[i] - source_centre[i] for i in (0, 1)]
    bottom_delta = output_core_box[3] - source_core_box[3] if output_core_box and source_core_box else None
    supported_count = int(np.count_nonzero(supported))
    removed_count = int(np.count_nonzero((supported > 0) & ~body_mask))
    original_mass = float(original_alpha.astype(np.float64).sum())
    mass_ratio = float(alpha.astype(np.float64).sum() / original_mass)
    authored_reasons = []
    if max(sx / sy, sy / sx) > 1.35:
        authored_reasons.append('authored_body_box_aspect_ratio_mismatch')
    if not (.85 <= width_ratio <= 1.15 and .85 <= height_ratio <= 1.15):
        authored_reasons.append('authored_body_size_outside_15_percent')
    if any(abs(centre_delta[i]) > max(6.0, .10 * (sr-sl if i == 0 else sb-st)) for i in (0, 1)):
        authored_reasons.append('authored_alpha_centroid_shift_above_10_percent')
    if bottom_delta is None or abs(bottom_delta) > anchor_limit:
        authored_reasons.append('authored_bottom_anchor_shift')
    if initial_bottom_delta is not None and abs(initial_bottom_delta) > anchor_limit:
        authored_reasons.append('authored_bottom_anchor_adjustment_above_small_shift')
    if not (.65 <= mass_ratio <= 1.40):
        authored_reasons.append('authored_body_alpha_mass_mismatch')
    if removed_count / max(1, supported_count) > .02 or (islands and islands[0] > max(12, .01 * supported_count)):
        authored_reasons.append('authored_significant_detached_component_removed')
    if output_islands and output_islands[0] > 12:
        authored_reasons.append('authored_significant_detached_component_remaining')
    if fringe_unfilled or (len(fringe_distances) and float(fringe_distances.max()) > 6):
        authored_reasons.append('authored_fringe_colour_repair_needs_review')
    if rear_alignment is not None and abs(rear_alignment['output_root_delta_to_runtime_seat_px']) > 3:
        authored_reasons.append('authored_rear_seat_anchor_shift')
    if np.any(alpha[0]) or np.any(alpha[-1]) or np.any(alpha[:, 0]) or np.any(alpha[:, -1]):
        authored_reasons.append('authored_subject_touches_canvas_boundary')
    metrics = dict(strict_metrics)
    metrics.update({
        'composition_mode': 'authored_edge',
        'alpha_identical_to_original': bool(np.array_equal(alpha, original_alpha)),
        'alpha_difference_is_intentional': True,
        'alpha_changed_pixel_count': int(np.count_nonzero(alpha != original_alpha)),
        'strict_alpha_reference': strict_metrics,
        'inherited_coverage_metrics_describe_strict_reference': True,
        'luma_percentiles_5_50_95': np.percentile(
            result[:, :, :3][alpha >= 8] @ np.array([.2126, .7152, .0722]), [5, 50, 95]).tolist(),
        'authored_edge': {
            'source_rgb_used': False,
            'original_canvas_preserved': list(source.size) == [width, height],
            'rear_seat_alignment': rear_alignment,
            'generated_subject_bbox_in_cell': generated_box,
            'output_to_generated_affine': transform,
            'output_subject_bbox': output_box,
            'generated_alpha_main_fraction': main_fraction,
            'output_alpha_main_fraction': output_fraction,
            'width_ratio_to_original_bbox': width_ratio,
            'height_ratio_to_original_bbox': height_ratio,
            'source_alpha_centroid': source_centre,
            'output_alpha_centroid': output_centre,
            'alpha_centroid_delta_px': centre_delta,
            'source_high_alpha_body_bbox': source_core_box,
            'output_high_alpha_body_bbox': output_core_box,
            'high_alpha_bottom_anchor_delta_before_adjustment_px': initial_bottom_delta,
            'offline_bottom_anchor_adjustment_px': [0, anchor_adjustment],
            'high_alpha_bottom_anchor_delta_px': bottom_delta,
            'alpha_mass_ratio_to_original': mass_ratio,
            'minimum_retained_alpha': 8,
            'low_alpha_noise_removed_pixel_count': int(np.count_nonzero((raw_alpha > 0) & (raw_alpha < 8))),
            'detached_supported_pixel_count_removed': removed_count,
            'detached_component_sizes_removed': islands[:20],
            'output_detached_component_sizes': output_islands[:20],
            'fringe_rgb_repaired_pixel_count': int(np.count_nonzero(odd_fringe)) - fringe_unfilled,
            'fringe_rgb_unrepaired_pixel_count': fringe_unfilled,
            'fringe_rgb_repair_distance_px': _distance_stats(fringe_distances),
            'review_required': bool(authored_reasons),
            'review_reasons': authored_reasons,
        },
        'review_reasons': strict_metrics['review_reasons'] + authored_reasons,
        'review_required': bool(strict_metrics['review_reasons'] or authored_reasons),
    })
    return Image.fromarray(result, 'RGBA'), metrics


def _comparison_preview(path, frames):
    """Light-background strict/authored pairs at identical display scale."""
    canvas = Image.new('RGB', (1800, 1050), '#eeeae3')
    draw = ImageDraw.Draw(canvas)
    for index, strict, authored in frames:
        x, y = index % 3 * 600, index // 3 * 350
        for panel, (label, frame) in enumerate((('original alpha', strict), ('authored alpha', authored))):
            left = x + panel * 300
            draw.text((left + 8, y + 8), f'cell {index}: {label}', fill='#222222')
            thumb = frame.copy()
            thumb.thumbnail((288, 288), Image.Resampling.LANCZOS)
            canvas.paste(thumb, (left + (300 - thumb.width) // 2, y + 28 + (300 - thumb.height) // 2), thumb)
    canvas.save(path)


def _preview(path, frames):
    cell_width, cell_height = 600, 350
    canvas = Image.new('RGB', (3 * cell_width, 3 * cell_height), '#888888')
    draw = ImageDraw.Draw(canvas)
    for index, frame, metrics in frames:
        x, y = (index % 3) * cell_width, (index // 3) * cell_height
        for panel, background in enumerate(('#eeeae3', '#171a20')):
            left = x + panel * 300
            draw.rectangle((left, y + 28, left + 299, y + 326), fill=background)
            thumb = frame.copy()
            thumb.thumbnail((288, 288), Image.Resampling.LANCZOS)
            canvas.paste(thumb, (left + (300 - thumb.width) // 2,
                                y + 28 + (300 - thumb.height) // 2), thumb)
        if metrics.get('composition_mode') == 'authored_edge':
            label = f"cell {index}: authored alpha (intentional)"
            label += ' / REVIEW' if metrics['review_required'] else ' / candidate'
        else:
            label = f"cell {index}: missing {metrics['missing_rgb_fraction']:.1%}"
            label += ' / REVIEW' if metrics['review_required'] else ' / geometry preserved'
        draw.text((x + 8, y + 8), label, fill='white')
    canvas.save(path)


def _mapping(work):
    boards = json.loads((work / 'boards.json').read_text(encoding='utf-8'))
    if not isinstance(boards, list) or len(boards) != 6:
        raise ValueError('Expected six source boards in boards.json.')
    keys = []
    for board in boards:
        if len(board.get('size', [])) != 2 or len(board.get('cells', [])) != 9:
            raise ValueError('Each board must describe a complete 3x3 grid.')
        indices = set()
        for cell in board['cells']:
            key, index = cell['key'], cell['index']
            if not isinstance(key, str) or not KEY_RE.fullmatch(key):
                raise ValueError('Invalid exported source key.')
            if type(index) is not int or not 0 <= index < 9 or index in indices:
                raise ValueError('Invalid or duplicated cell index.')
            indices.add(index)
            keys.append(key)
    if len(keys) != len(set(keys)):
        raise ValueError('Duplicate source keys require explicit mapping review.')
    return boards


def _install(candidate, destination, manifest, work):
    """Replace the whole authored set, preserving any previous directory."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.black-coat-stage-', dir=destination.parent))
    backup = None
    try:
        for row in manifest['frames'].values():
            source = candidate / row['file']
            if _hash(source) != row['sha256']:
                raise ValueError('Candidate changed during installation.')
            shutil.copy2(source, stage / row['file'])
        _json(stage / 'manifest.json', manifest)
        if destination.exists():
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            backup = work / ('black-coat-before-generated-' + stamp)
            os.replace(destination, backup)
        try:
            os.replace(stage, destination)
        except BaseException:
            if backup is not None:
                os.replace(backup, destination)
            raise
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    return str(backup) if backup else None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--work-dir', type=Path, default=DEFAULT_WORK)
    parser.add_argument('--output-dir', type=Path,
                        help='Default: WORK_DIR/composed-candidates or WORK_DIR/authored-edge')
    parser.add_argument('--authored-edge', action='store_true',
                        help='Use generated black-fur alpha in the original aligned canvas; intentional outline change.')
    parser.add_argument('--board', action='append', type=int, choices=range(1, 7),
                        help='Compose only this board; may be repeated. No installation.')
    parser.add_argument('--install', action='store_true',
                        help='Compose all six boards and install the complete authored set.')
    parser.add_argument('--accept-reviewed', action='store_true',
                        help='Explicitly accept review flags for installation after visual review.')
    args = parser.parse_args(argv)
    work = args.work_dir.resolve()
    output = (args.output_dir or work / ('authored-edge' if args.authored_edge else 'composed-candidates')).resolve()
    destination = DEFAULT_INSTALL.resolve()
    if args.board and args.install:
        parser.error('--install requires the complete six-board set, not --board.')
    if args.accept_reviewed and not args.install:
        parser.error('--accept-reviewed is meaningful only with --install.')
    if output == destination or destination in output.parents:
        parser.error('Candidate output must be separate from production assets.')
    boards = _mapping(work)
    selected = sorted(set(args.board or range(1, 7)))
    missing = [str(work / f'black-board-{i}.png') for i in selected
               if not (work / f'black-board-{i}.png').is_file()]
    if missing:
        parser.error('Generated boards are not ready: ' + ', '.join(missing))
    # Preflight source presence and canvas sizes before writing candidates.
    for number in selected:
        for cell in boards[number - 1]['cells']:
            source = work / 'source-frames' / (cell['key'] + '.png')
            if not source.is_file():
                parser.error('Missing immutable original: ' + str(source))
            with Image.open(source) as image:
                if list(image.size) != cell['source_size']:
                    parser.error('Original size differs from exported mapping: ' + cell['key'])
    output.mkdir(parents=True, exist_ok=True)
    report = {'schema_version': 1, 'created_at': datetime.now(timezone.utc).isoformat(),
              'selected_boards': selected, 'all_six_boards': len(selected) == 6,
              'installed': False, 'frames': {}, 'boards': {}}
    if args.authored_edge:
        report['geometry_policy'] = 'authored-alpha-aligned'
        report['original_alpha_difference_is_intentional'] = True
        (output / 'strict-reference').mkdir(exist_ok=True)
    manifest = {'schema_version': 1, 'kind': 'backyard-visitor-black-coat', 'frames': {},
                'method': 'generated RGB aligned with nearest trustworthy generated colour extension; original alpha retained'}
    if args.authored_edge:
        manifest.update(geometry_policy='authored-alpha-aligned',
                        method='generated black-fur RGB and authored alpha aligned offline to original canvas and body anchors')
    for number in selected:
        board = boards[number - 1]
        generated_path = work / f'black-board-{number}.png'
        with Image.open(generated_path) as image:
            generated = image.convert('RGBA')
        width, height = generated.size
        previews, comparisons = [], []
        report['boards'][str(number)] = {'file': generated_path.name,
                                         'size': list(generated.size),
                                         'sha256': _hash(generated_path)}
        for cell in board['cells']:
            index, key = cell['index'], cell['key']
            col, row = index % 3, index // 3
            bounds = (round(col * width / 3), round(row * height / 3),
                      round((col + 1) * width / 3), round((row + 1) * height / 3))
            generated_cell = generated.crop(bounds)
            source_path = work / 'source-frames' / (key + '.png')
            with Image.open(source_path) as image:
                source = image.convert('RGBA')
            frame, metrics = compose(source, generated_cell)
            if args.authored_edge:
                strict_frame = frame
                strict_frame.save(output / 'strict-reference' / (key + '.png'))
                authored_cell, extraction = _authored_cell(generated, bounds)
                frame, metrics = compose_authored(
                    source, authored_cell, metrics,
                    rear_seat_y=224 if 'rear' in cell.get('uses', []) else None)
                metrics['authored_edge']['cell_extraction'] = extraction
                if extraction['subject_touches_expanded_sample_boundary']:
                    reason = 'authored_subject_touches_expanded_sample_boundary'
                    metrics['authored_edge']['review_reasons'].append(reason)
                    metrics['authored_edge']['review_required'] = True
                    metrics['review_reasons'].append(reason)
                    metrics['review_required'] = True
                comparisons.append((index, strict_frame, frame))
            metrics.update(board=number, cell=index, source_key=key,
                           source_png_sha256=_hash(source_path), source_size=list(source.size),
                           board_cell_bounds=bounds, original_board_xy=cell['board_xy'],
                           original_board_size=cell['board_size'], uses=cell.get('uses', []))
            output_path = output / (key + '.png')
            frame.save(output_path)
            # Re-read what will be installed, not just the in-memory pixels.
            with Image.open(output_path) as reread:
                actual = np.asarray(reread.convert('RGBA'))
                expected_alpha = np.asarray(frame if args.authored_edge else source)[:, :, 3]
                if reread.size != source.size or not np.array_equal(actual[:, :, 3], expected_alpha):
                    raise ValueError('Saved frame differs from intended canvas/alpha: ' + key)
            manifest['frames'][key] = {'file': key + '.png', 'size': list(frame.size),
                                      'sha256': _hash(output_path)}
            if args.authored_edge:
                manifest['frames'][key].update(
                    source_key=key,
                    alpha_sha256=hashlib.sha256(actual[:, :, 3].tobytes(order='C')).hexdigest(),
                    alpha_bounds=_bounds(actual[:, :, 3]), alignment_offset_px=[0, 0])
            report['frames'][key] = metrics
            previews.append((index, frame, metrics))
        _preview(output / f'preview-board-{number}.png', previews)
        if args.authored_edge:
            _comparison_preview(output / f'comparison-board-{number}.png', comparisons)
    report['review_keys'] = [key for key, data in report['frames'].items() if data['review_required']]
    report['review_required'] = bool(report['review_keys'])
    report['frame_count'] = len(manifest['frames'])
    manifest['partial_candidate'] = len(selected) != 6
    _json(output / 'manifest.json', manifest)
    _json(output / 'composition-report.json', report)
    if args.install:
        if report['review_required'] and not args.accept_reviewed:
            print(json.dumps({'state': 'review_required', 'installed': False,
                              'review_frames': len(report['review_keys']),
                              'report': str(output / 'composition-report.json')}))
            return 2
        report['explicit_review_override'] = bool(args.accept_reviewed)
        report['previous_assets_backup'] = _install(output, destination, manifest, work)
        report['installed'], report['installed_path'] = True, str(destination)
        _json(output / 'composition-report.json', report)
    print(json.dumps({'state': 'review_required' if report['review_required'] else 'candidate_ready',
                      'frames': report['frame_count'], 'installed': report['installed'],
                      'review_frames': len(report['review_keys']), 'output': str(output)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
