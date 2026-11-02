"""Packaged perspective identity, route choice, and existing coat consistency."""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import shutil

import pytest
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QApplication

from backyard_visitor.art import CatArt
from backyard_visitor.depth_art import ASSETS, COUNTS, DepthArt, select_depth_pose


# Independently pinned original frame hashes keep this audit portable.
PINNED_DEPTH_SHA256 = {'away-00@2x.png': '981b1bdb8389a2f82a2d4f7f18c0aedb8859e9eaabc9bfad4935253a915ae3fc',
 'away-01@2x.png': '3b9fe555fa78d81bb016feb3a048daf975fc8c9e129225ca231f410ff77a2250',
 'away-02@2x.png': 'ae1ff2b0e674cfeeec4078a9bb2cb05fcd7d00a018ef3f7da6e106346c97398f',
 'away-03@2x.png': '1a132ac8f15ce69bd34adb1c36bee251fdb0014b18aa275d0f68606a5cd4e7a5',
 'away-04@2x.png': '35b9d194af53c99139d7a9df7e64ae34b8f3c29a7dc7d274cef59a50a920c1ee',
 'away-05@2x.png': '95efd89ebfb39636e5c1e70b08ee494ba6fb1c96a1c4ff5b2f9d08568471520a',
 'away-06@2x.png': '06dcb0d8814c1e5800e0925fec89c74d47bf8ec8b3f6c547d80eca64db05bf78',
 'away-07@2x.png': '73a10ed126d694008ae88979b39e187a3b730fd21c3657cc0eccdc43e864f70a',
 'front-00@2x.png': '186079acf58895a263b8bc10cfaa5c41fb65b4b3a00159e28c5518051149ca75',
 'toward-00@2x.png': '003aad575fcad9a0242e7e1571ed2ae46bbb96c430cd294766a1020e9283f8a3',
 'toward-01@2x.png': 'f7a71c97298cf862cd20066411cf877435ca25ba280378429043dab794813a4f',
 'toward-02@2x.png': '9859a583b71666ebeaed944abdaab7718b440556b397577ea77dc055ea9be31a',
 'toward-03@2x.png': '7b90267e2cfdec3ca9468c3d04bfcfc200032ee47ed2fb7c485d3341722d7847'}


@pytest.fixture(scope="module")
def qt():
    return QApplication.instance() or QApplication(["depth-art-assets-test"])


def test_original_thirteen_sources_are_packaged_unchanged_and_have_shared_canvas(qt):
    manifest = json.loads((ASSETS / "manifest.json").read_text())
    art = DepthArt()
    assert art.available and art.load_error is None
    assert len(manifest["frames"]) == 13
    for row in manifest["frames"]:
        packed = ASSETS / row["file"]
        # Historical source paths are provenance only; no local output/ tree is required.
        assert hashlib.sha256(packed.read_bytes()).hexdigest() == row["sha256"]
        assert row["sha256"] == PINNED_DEPTH_SHA256[row["file"]]
        frame = art.frame(row["kind"], row["index"])
        assert not frame.isNull() and frame.hasAlphaChannel()
        assert (frame.width(), frame.height(), frame.devicePixelRatio()) == (288, 224, 2)
    assert art.frame("side_left") is None


def test_eight_away_four_toward_slots_are_driven_by_route_phase():
    for kind, phase, count in [("away", "recede", 8), ("toward", "advance", 4)]:
        choices = [select_depth_pose({"phase": phase, "facing": 1,
                                      "gait_phase": (i + .1) / count})
                   for i in range(count)]
        assert choices == [{"kind": kind, "index": i} for i in range(count)]
    snapshot = {"phase": "recede", "facing": 1, "gait_phase": .3}
    assert select_depth_pose(snapshot, corridor_supported=False) is None
    assert select_depth_pose(dict(snapshot, facing=-1)) is None
    assert select_depth_pose(dict(snapshot, gait_phase=float("nan"))) is None
    assert select_depth_pose(dict(snapshot, phase="rear_return")) is None


def test_turn_uses_actual_views_and_delegates_side_pose_to_catart():
    turn = dict(phase="turn", gait_phase=.51, turn_from_degrees=0.,
                turn_to_degrees=150., turn_motion_progress=0.)
    assert select_depth_pose(turn) == {"kind": "side_right", "index": 0}
    assert select_depth_pose(dict(turn, turn_motion_progress=.5)) == {"kind": "front", "index": 0}
    assert select_depth_pose(dict(turn, turn_motion_progress=.9)) == {"kind": "toward", "index": 2}
    # A small turn to the receding view does not pass through a face-on sprite.
    assert select_depth_pose(dict(turn, turn_to_degrees=-30., turn_motion_progress=.9)) == {"kind": "away", "index": 4}


@pytest.mark.parametrize("coat", ["ginger", "gray", "tuxedo"])
def test_coats_match_actual_catart_renderer_for_same_pixels(qt, coat):
    depth = DepthArt()
    original = depth.frame("toward", 1)
    # Supply this new artwork to the real existing coat renderer, without loading
    # unrelated atlases. This catches drift between the two production rules.
    cats = CatArt.__new__(CatArt)
    cats.frames = [QPixmap(original)]
    cats.states = {"test_depth_pose": [0]}
    cats.facing = -1
    cats.cache = OrderedDict()
    cats.cache_limit = 16
    cats._blink_windows = {}
    expected = cats.frame("test_depth_pose", 0, -1, coat)
    actual = depth.frame("toward", 1, coat)
    assert actual.toImage().convertToFormat(QImage.Format.Format_RGBA8888) == expected.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    assert actual.devicePixelRatio() == expected.devicePixelRatio() == 2
    assert depth.frame("toward", 1, coat).cacheKey() == actual.cacheKey()


@pytest.mark.parametrize("damage", ["checksum", "anchor", "missing_pose"])
def test_incomplete_or_changed_pack_fails_without_partial_art(qt, tmp_path, damage):
    folder = tmp_path / "depth-sprites"
    shutil.copytree(ASSETS, folder)
    path = folder / "manifest.json"
    data = json.loads(path.read_text())
    if damage == "checksum":
        item = data["frames"][-1]
        (folder / item["file"]).write_bytes(b"not a verified PNG")
    elif damage == "anchor":
        data["foot_anchor"] = [72, 110]
    else:
        data["frames"].pop()
    path.write_text(json.dumps(data))
    art = DepthArt(folder)
    assert not art.available
    assert art.load_error
    assert art.frame("away", 0) is None
