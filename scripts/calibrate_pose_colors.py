"""Offline color-only pose candidates, using a stable standing reference.

User authorized programmatic color correction. This writes candidates only;
never call against an output directory containing production source assets.
Originals must be retained before installation to avoid cumulative correction.
"""
from pathlib import Path
import argparse
import hashlib
import json
import numpy as np
from PIL import Image
from calibrate_cat_color import ROOT, lab, rgb, ramp, selection

NAMES = ('run', 'activity', 'posture', 'posture-belly')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def calibrate(source_path, reference_path, destination):
    source = np.array(Image.open(source_path).convert('RGBA'))
    reference = np.array(Image.open(reference_path).convert('RGBA'))
    sl, rl = lab(source[..., :3]), lab(reference[..., :3])
    sm, rm = selection(source, sl), selection(reference, rl)
    offsets, bins = [], []
    for lo in range(25, 75, 10):
        ss = sm & (sl[..., 0] >= lo) & (sl[..., 0] < lo + 10)
        rr = rm & (rl[..., 0] >= lo) & (rl[..., 0] < lo + 10)
        if min(ss.sum(), rr.sum()) < 100:
            continue
        a, b = np.median(sl[ss, 1:], axis=0), np.median(rl[rr, 1:], axis=0)
        offsets.append(b - a)
        bins.append({'L_range': [lo, lo + 10], 'source_ab': a.tolist(),
                     'reference_ab': b.tolist()})
    if len(offsets) < 3:
        raise ValueError('Insufficient comparable fur samples')
    delta = np.clip(np.median(offsets, axis=0) * .8, [-4, -5], [4, 5])
    L, a, b = np.moveaxis(sl, -1, 0)
    weight = (ramp(a, 1, 5) * ramp(b, 4, 12) * ramp(b-a, .5, 6)
              * ramp(L, 8, 25) * (1-ramp(L, 82, 96)))
    weight = np.where(source[..., 3] > 0, weight, 0)
    corrected = sl.copy()
    corrected[..., 1:] += weight[..., None] * delta
    candidate = source.copy()
    changed = weight > 0
    candidate[changed, :3] = rgb(corrected)[changed]
    destination.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(candidate).save(destination)
    saved = np.array(Image.open(destination).convert('RGBA'))
    assert saved.shape == source.shape
    assert np.array_equal(saved[..., 3], source[..., 3])
    assert np.array_equal(saved[~changed], source[~changed])
    assert np.array_equal(saved[source[..., 3] == 0], source[source[..., 3] == 0])
    actual = lab(saved[..., :3])
    err = np.abs(actual[..., 0] - sl[..., 0])[changed]
    for row in bins:
        lo, hi = row['L_range']
        ss = sm & (sl[..., 0] >= lo) & (sl[..., 0] < hi)
        row['candidate_ab'] = np.median(actual[ss, 1:], axis=0).tolist()
    return dict(source=str(source_path), reference=str(reference_path),
                candidate=str(destination), source_sha256=digest(source_path),
                reference_sha256=digest(reference_path), candidate_sha256=digest(destination),
                applied_ab_offset=delta.tolist(), matched_lightness_bins=bins,
                width=saved.shape[1], height=saved.shape[0], alpha_identical=True,
                protected_pixels_identical=True, fully_transparent_rgba_identical=True,
                lightness_error_mean=float(err.mean()), lightness_error_max=float(err.max()),
                spatial_resampling=False, production_replaced=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', type=Path, default=ROOT/'output/experiments/color/original-assets')
    parser.add_argument('--output-dir', type=Path, default=ROOT/'output/experiments/color/pose-calibration')
    parser.add_argument('--sources', nargs='+', choices=NAMES, default=list(NAMES))
    args = parser.parse_args()
    if args.source_dir.resolve() == args.output_dir.resolve():
        parser.error('Output must differ from source directory')
    if not args.output_dir.resolve().is_relative_to((ROOT/'output').resolve()):
        parser.error('Candidates must be written inside project output directory')
    records = []
    for name in args.sources:
        record = calibrate(args.source_dir/(name+'.png'), args.source_dir/'standing.png',
                           args.output_dir/(name+'.png'))
        records.append(record)
    report = dict(authorized_by_user=True, method='Bounded Lab chroma correction at fixed lightness; one offset per atlas',
                  visual_acceptance_complete=False, production_replaced=False, assets=records)
    (args.output_dir/'report.json').write_text(json.dumps(report, indent=2)+'\n', encoding='utf-8')
    print(json.dumps([dict(asset=Path(r['source']).name, offset=r['applied_ab_offset'],
                           alpha_identical=r['alpha_identical'], max_lightness_error=r['lightness_error_max'])
                      for r in records]))


if __name__ == '__main__':
    main()
