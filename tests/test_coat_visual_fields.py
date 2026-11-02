"""Quality guards for coherent fields; final appearance also needs visual review."""
from dataclasses import replace

import pytest
from backyard_visitor.coat_traits import CoatField, CoatSpec


def _components(points):
    points = set(points)
    groups = []
    while points:
        initial = points.pop()
        group, todo = {initial}, [initial]
        while todo:
            x, y = todo.pop()
            for neighbour in ((x-1, y), (x+1, y), (x, y-1), (x, y+1)):
                if neighbour in points:
                    points.remove(neighbour)
                    group.add(neighbour)
                    todo.append(neighbour)
        groups.append(group)
    return groups


@pytest.mark.parametrize("seed", [0, 8, 39, 73, 123])
def test_mackerel_stripes_have_connected_arcs_and_only_a_few_substantial_broken_marks(seed):
    coat = CoatField(CoatSpec(seed, dark_pattern="mackerel"))
    # Exclude the spine so each individual arc has to be connected in itself.
    mask = {(x, y) for y in range(65) for x in range(81)
            if coat.sample("body", x/80, .12+y/64*.85).dark_tabby > .5}
    groups = _components(mask)
    # Photo-referenced forked/broken pencillings may have a lower segment at
    # this contrast threshold. They must be few, substantial arcs, not a cloud
    # of tiny detached scraps. Most area remains connected to the upper flank.
    assert 6 <= len(groups) <= 10
    lower = [group for group in groups if min(y for _, y in group) > 0]
    assert len(lower) <= 2
    assert sum(map(len, lower)) < len(mask)*.08
    assert all(len(group) >= 18 for group in groups)


@pytest.mark.parametrize("seed", [0, 8, 39, 73, 123])
def test_classic_has_broad_flank_courses_and_a_lighter_ventral_region(seed):
    coat = CoatField(CoatSpec(seed, dark_pattern="classic"))
    side = [coat.sample("body", x/100, .60).dark_tabby for x in range(101)]
    belly = [coat.sample("body", x/100, .93).dark_tabby for x in range(101)]
    assert min(side) < .05 and max(side) > .9
    # Real blotched tabbies have broad solid dark areas, not a sparse spiral
    # stroke. This replaces the rejected narrow-scroll occupancy constraint.
    assert .28 < sum(side)/len(side) < .70
    # Continuous real-cat courses may extend towards the underside. The old
    # near-zero belly rule belonged to the sparse badge/pillar layout; retain
    # a lighter underside rather than severing each curve to satisfy it.
    assert sum(belly) < sum(side)*.5
    assert sum(belly)/len(belly) < .25


@pytest.mark.parametrize("seed", [0, 49, 73, 123])
@pytest.mark.parametrize("channel", ["white", "orange"])
def test_pigment_patch_contours_have_no_high_frequency_edge_perturbations(seed, channel):
    spec = CoatSpec(seed, pigment="tortie", white_mode="spotting", white_coverage=.45)
    coat = CoatField(spec)
    # Rounded shared-patch envelopes can have different raw-score curvature
    # where same-colour lobes overlap. Guard the actual normalized contour:
    # a few broad bends, no fine oscillation, and continuity at each crossing.
    for side in ("left", "right"):
        for axis in (0, 1):
            for cross in (.1, .3, .5, .7, .9):
                def value(position):
                    u, v = (position, cross) if axis == 0 else (cross, position)
                    return getattr(coat.sample("body", u, v, side), channel)
                row = [value(i/200) for i in range(201)]
                slopes = [b-a for a, b in zip(row, row[1:])]
                signs = [1 if slope > 0 else -1 for slope in slopes if abs(slope) > 1e-5]
                assert sum(a != b for a, b in zip(signs, signs[1:])) <= 6
                for i, (a, b) in enumerate(zip(row, row[1:])):
                    if a == b or not (min(a, b) <= .5 <= max(a, b)):
                        continue
                    lo, hi = i/200, (i+1)/200
                    for _ in range(16):
                        mid = (lo+hi)/2
                        if (value(mid) < .5) == (a < .5):
                            lo = mid
                        else:
                            hi = mid
                    crossing = (lo+hi)/2
                    assert abs(value(min(1., crossing+1e-6))
                               - value(max(0., crossing-1e-6))) < .005


@pytest.mark.parametrize("side", ["left", "right"])
def test_pigment_at_attached_part_roots_matches_its_stable_body_anchor(side):
    spec = CoatSpec(120, pigment="tortie", white_mode="spotting", white_coverage=.45)
    coat = CoatField(spec)
    pairs = (("head", 1., .5, 0., .5),
             ("tail", 0., .5, 1., .33))
    for part, u, v, body_u, body_v in pairs:
        assert coat.sample(part, u, v, side)[2:] == pytest.approx(
            coat.sample("body", body_u, body_v, side)[2:], abs=1e-12)
    # Limb charts now inherit a small real attachment neighbourhood. The
    # old .07/.91 point anchors predated the corrected body-longitude map.
    for part, center in (("front_leg", .24), ("hind_leg", .82)):
        nearby = [coat.sample("body", u, v, side) for u, v in
                  ((center-.015, .70), (center, .685), (center, .715), (center+.015, .70))]
        root = coat.sample(part, 0., .5, side)
        for channel in ("orange", "white"):
            assert min(getattr(point, channel) for point in nearby)-1e-12 <= getattr(root, channel)
            assert getattr(root, channel) <= max(getattr(point, channel) for point in nearby)+1e-12


def test_visual_correction_keeps_seed_and_trait_record_and_independent_colour_channels():
    spec = CoatSpec(73, pigment="tortie", white_mode="spotting", white_coverage=.45)
    before = spec.to_dict()
    a, b = CoatField(spec), CoatField(replace(spec, dark_pattern="classic", dilute=True))
    for part in ("body", "head", "front_leg", "hind_leg", "tail"):
        for side in ("left", "right"):
            for u, v in ((.1, .2), (.5, .5), (.9, .8)):
                assert a.sample(part, u, v, side)[2:] == b.sample(part, u, v, side)[2:]
    assert spec.to_dict() == before
    assert CoatSpec.from_dict(before) == spec


def test_classic_seed_changes_curve_shape_after_removing_translation():
    # Compare centred flank silhouettes: simply shifting one G-shaped stamp
    # cannot pass. Shape variation must survive alignment of their centroids.
    shapes = []
    for seed in (8, 39, 73, 123):
        coat = CoatField(CoatSpec(seed, dark_pattern="classic"))
        points = {(x, y) for y in range(8, 58) for x in range(16, 65)
                  if coat.sample("body", x/64, y/64).dark_tabby > .5}
        assert len(points) > 180
        cx = round(sum(x for x, _ in points)/len(points))
        cy = round(sum(y for _, y in points)/len(points))
        shapes.append({(x-cx, y-cy) for x, y in points})
    # Wide filled areas share more pixels than the former thin scrolls.
    # Require both a meaningful minimum and substantial average diversity,
    # rather than making one outlier pair artificially jagged to pass.
    distances = [len(a ^ b)/len(a | b)
                 for i, a in enumerate(shapes) for b in shapes[i+1:]]
    assert min(distances) > .20
    assert sum(distances)/len(distances) > .40


@pytest.mark.parametrize("seed", [0, 42, 122, 551])
def test_calico_and_tortie_have_coherent_visible_pigment_islands_without_confetti(seed):
    for coverage in (.4, 0.):
        coat = CoatField(CoatSpec(seed, pigment="tortie",
                                  white_mode="spotting" if coverage else "none",
                                  white_coverage=coverage))
        samples = {(x, y): coat.sample("body", x/80, y/64)
                   for y in range(65) for x in range(81)}
        visible = {point for point, sample in samples.items() if sample.white < .5}
        for orange in (False, True):
            mask = {point for point in visible if (samples[point].orange > .5) == orange}
            # White-covered pigment is not part of the visible composition.
            # Both colours must occupy substantial parts of the side, rather
            # than satisfying a colour quota with hidden pigment or tiny dots.
            low, high = (.30, .70) if coverage else (.20, .80)
            assert low < len(mask)/len(visible) < high
            areas = [len(group) for group in _components(mask)]
            # A line can cross two broad curved islands four times; that
            # does not make the islands finer than an interleaved tortie.
            # Use 2-D connected area instead. Calico allows a few large patches;
            # tortie allows more interleaving, but neither may become confetti.
            small_limit = max(20, len(visible)*.01)
            substantial = [area for area in areas if area >= small_limit]
            assert 1 <= len(substantial) <= (4 if coverage else 12)
            fragments = sum(area for area in areas if area < small_limit)
            assert fragments/len(mask) < (.01 if coverage else .03)


@pytest.mark.parametrize("coverage", [0., .4])
def test_orange_region_distribution_varies_by_seed_and_is_not_a_fixed_saddle(coverage):
    masks, regions = [], []
    for seed in (0, 42, 122, 551):
        coat = CoatField(CoatSpec(seed, pigment="tortie",
                                  white_mode="spotting" if coverage else "none",
                                  white_coverage=coverage))
        rows = [[coat.sample("body", x/60, y/30)
                 for x in range(61)] for y in range(31)]
        # A calico's complete three-colour layout includes its white bays.
        # Comparing orange hidden under white discarded the very placement
        # variation that now creates separate, complete pigment islands.
        masks.append([2 if value.white>.5 else int(value.orange>.5)
                      for row in rows for value in row])
        regions.append([sum(value.orange*(1-value.white)
                            for row in rows for value in row[a:b]) /
                        max(1e-9, sum(1-value.white for row in rows for value in row[a:b]))
                        for a,b in ((0,20),(20,41),(41,61))])
    # Keep the original >20% diversity guard on the final visible colours.
    # The warmest region must also move across this fixed seed set.
    for i,a in enumerate(masks):
        for b in masks[i+1:]:
            assert sum(x != y for x,y in zip(a,b))/len(a) > .20
    assert len({max(range(3), key=row.__getitem__) for row in regions}) >= 2
    assert any(row[1] < max(row[0],row[2]) for row in regions)


@pytest.mark.parametrize("kind", ["spotted", "ticked"])
def test_spotted_and_ticked_keep_face_leg_and_tail_markings(kind):
    coat = CoatField(CoatSpec(122, dark_pattern=kind))
    for part in ("head", "front_leg", "hind_leg", "tail"):
        values = [coat.sample(part,x/100,.45).dark_tabby for x in range(101)]
        assert min(values) < .05
        assert max(values) > .5
    if kind == "ticked":
        values = [coat.sample("body",x/100,.45).dark_tabby for x in range(101)]
        assert max(values)-min(values) < .17
