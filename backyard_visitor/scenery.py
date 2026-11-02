"""Validated wallpaper catalog; PNG dimensions are read without decoding pixels."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import re
import struct
import zlib

_IDENTIFIER = re.compile(r'[a-z0-9][a-z0-9_-]{0,95}\Z')
_PHASES = ('morning', 'dusk')


def _identifier(value):
    return isinstance(value, str) and _IDENTIFIER.fullmatch(value) is not None


def _coordinate(value):
    return type(value) in (int, float) and 0 <= value <= 1 and math.isfinite(value)


def _point(value):
    return isinstance(value, list) and len(value) == 2 and all(_coordinate(n) for n in value)


def _png_canvas(path):
    with path.open('rb') as source:
        header = source.read(33)
    if (len(header) != 33 or header[:8] != b'\x89PNG\r\n\x1a\n'
            or header[8:16] != b'\0\0\0\rIHDR'
            or zlib.crc32(header[12:29]) != struct.unpack('>I', header[29:33])[0]):
        raise ValueError('Invalid PNG dimension header')
    width, height = struct.unpack('>II', header[16:24])
    if not all(1 <= n <= 32768 for n in (width, height)):
        raise ValueError('Invalid PNG canvas')
    return [width, height]


def _layout_geometry(value):
    if value is None:
        return {'supports': [], 'anchors': {}}
    if not isinstance(value, dict):
        raise ValueError('Invalid layout geometry')
    supports, anchors = value.get('supports', []), value.get('anchors', {})
    if not isinstance(supports, list) or len(supports) > 64 or not isinstance(anchors, dict) or len(anchors) > 128:
        raise ValueError('Invalid layout geometry collection')
    cleaned, ids = [], set()
    for support in supports:
        if (not isinstance(support, dict) or not _identifier(support.get('surface_id'))
                or support['surface_id'] in ids
                or not all(_coordinate(support.get(name)) for name in ('left', 'right', 'y'))
                or support['left'] >= support['right']):
            raise ValueError('Invalid normalized support')
        ids.add(support['surface_id'])
        cleaned.append({'surface_id': support['surface_id'],
                        **{name: float(support[name]) for name in ('left', 'right', 'y')}})
    if any(not _identifier(name) or not _point(point) for name, point in anchors.items()):
        raise ValueError('Invalid normalized anchor')
    return {'supports': sorted(cleaned, key=lambda row: row['surface_id']),
            'anchors': {name: [float(n) for n in point] for name, point in sorted(anchors.items())}}


def _cycle_metadata(item):
    fields = ('theme_id', 'phase', 'layout_id', 'cycle_pair')
    if not any(name in item for name in fields):
        return {}, 'No day/night pair declared'
    pair = item.get('cycle_pair')
    if (not _identifier(item.get('theme_id')) or not _identifier(item.get('layout_id'))
            or item.get('phase') not in _PHASES or not isinstance(pair, dict)
            or set(pair) != set(_PHASES) or not all(_identifier(pair[name]) for name in _PHASES)
            or pair['morning'] == pair['dusk'] or pair[item['phase']] != item['id']):
        return {}, 'Invalid day/night metadata'
    return {name: dict(pair) if name == 'cycle_pair' else item[name] for name in fields}, None


def _pair_is_complete(scene, scenes):
    pair = scene.get('cycle_pair')
    if not isinstance(pair, dict):
        return False
    return all((other := scenes.get(pair[phase])) is not None
               and other.get('theme_id') == scene['theme_id']
               and other.get('orientation') == scene['orientation']
               and other.get('phase') == phase and other.get('cycle_pair') == pair
               for phase in _PHASES)


def same_layout_geometry(first, second):
    """Only actual validated geometry can preserve support across a switch.

    Equal names or two background-only images do not establish safe anchors.
    This metadata comparison cannot verify photographic alignment; authors must
    review the artwork before adding interaction geometry in a later stage.
    """
    return (isinstance(first, dict) and isinstance(second, dict)
            and first.get('geometry_available') is True and second.get('geometry_available') is True
            and bool(first.get('layout_id')) and first.get('layout_id') == second.get('layout_id')
            and bool(first.get('geometry_fingerprint'))
            and first.get('geometry_fingerprint') == second.get('geometry_fingerprint'))


def load_scenes(assets: Path) -> dict[str, dict]:
    scenes = {}
    for path in sorted(assets.glob('backyard-*.json')):
        try:
            if path.stat().st_size > 65_536:
                continue
            item = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(item, dict) or item.get('id') != path.stem:
                continue
            name, filename = item.get('title'), item.get('file')
            if not isinstance(name, str) or not name or len(name) > 80:
                continue
            if filename != path.stem + '.png' or not (assets / filename).is_file() or item.get('fit') != 'stretch':
                continue
            orientation = item.get('orientation', 'landscape')
            if orientation not in ('landscape', 'portrait'):
                continue
            canvas = _png_canvas(assets / filename)
            declared = item.get('canvas', canvas)
            if (not isinstance(declared, list) or len(declared) != 2
                    or any(type(n) is not int for n in declared) or declared != canvas
                    or (orientation == 'portrait') != (canvas[1] > canvas[0])):
                continue
            occluders = []
            for region in item.get('occluders', [])[:32]:
                polygon = region.get('polygon', []) if isinstance(region, dict) else []
                if not isinstance(polygon, list) or not 3 <= len(polygon) <= 256:
                    continue
                if all(_point(point) for point in polygon):
                    entry = {'polygon': [[float(n) for n in point] for point in polygon]}
                    if 'hiding_point' in region:
                        if not _point(region['hiding_point']):
                            continue
                        entry['hiding_point'] = [float(n) for n in region['hiding_point']]
                    occluders.append(entry)
            layout = _layout_geometry(item.get('layout_geometry'))
            walking_y = item.get('walking_plane_y')
            if walking_y is not None and not _coordinate(walking_y):
                continue
            walking_y = float(walking_y) if walking_y is not None else None
            geometry = {'canvas': canvas, 'fit': 'stretch', 'orientation': orientation,
                        'occluders': occluders, 'layout_geometry': layout, 'walking_plane_y': walking_y}
            available = bool(occluders or layout['supports'] or layout['anchors'])
            fingerprint = (hashlib.sha256(json.dumps(geometry, sort_keys=True,
                separators=(',', ':'), allow_nan=False).encode()).hexdigest() if available else None)
            metadata, error = _cycle_metadata(item)
            scenes[item['id']] = {'id': item['id'], 'title': name, 'file': filename,
                'fit': 'stretch', 'orientation': orientation, 'canvas': canvas, 'occluders': occluders,
                'layout_geometry': layout, 'geometry_available': available,
                'geometry_fingerprint': fingerprint, 'walking_plane_y': walking_y,
                **metadata, 'cycle_ready': False, 'cycle_error': error}
        except (OSError, ValueError, TypeError, AttributeError, KeyError, OverflowError, struct.error):
            continue
    for scene in scenes.values():
        if scene.get('cycle_pair'):
            scene['cycle_ready'] = _pair_is_complete(scene, scenes)
            scene['cycle_error'] = None if scene['cycle_ready'] else 'Day/night partner is missing or inconsistent'
    return scenes
