"""The QA player must report production phase and render through the real API."""
import importlib.util
import math
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("preview_motion", ROOT / "scripts/preview_motion.py")
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


@pytest.fixture(scope="module", autouse=True)
def qt():
    return QApplication.instance() or QApplication(["preview-motion-test"])


def test_phase_tracks_distance_wrap_and_stops():
    stride = preview.GAIT_STRIDE
    assert [preview.phase_for_distance(d*stride) for d in (0, .25, .5, .75, 1, 1.25)] == [0, .25, .5, .75, 0, .25]
    assert preview.phase_for_distance(0) == preview.phase_for_distance(-1) == 0
    with pytest.raises(ValueError):
        preview.phase_for_distance(math.nan)


def test_renderer_forwards_exact_production_controls():
    class RecordingArt:
        def paint(self, painter, rect, *args, **kwargs):
            self.args, self.kwargs, self.rect = args, kwargs, rect
    art = RecordingArt()
    image = preview.render_pose(art, "idle", 1, age=10, gait_phase=.6,
                                land_progress=.75, settling=True, turning=True,
                                settle_progress=.3, rise_progress=.7)
    assert (image.width(), image.height(), image.devicePixelRatio()) == (288, 224, 2)
    assert art.args == ("idle", 10, 1, "ginger")
    assert art.kwargs == {"gait_phase": .6, "land_progress": .75, "settling": True, "turning": True,
                          "settle_progress": .3, "rise_progress": .7, "takeoff_progress": None}
    assert (art.rect.width(), art.rect.height()) == (144, 112)


def test_payload_contains_real_distinct_walk_and_shared_landing_pose():
    payload, rendered = preview.build_payload(preview.CatArt())
    frames = payload["frames"]
    assert len({frames["-1:walk"+str(i)] for i in range(4)}) == 4
    assert frames["-1:standing"] != frames["-1:idle"]
    assert frames["-1:land0"] == frames["-1:land8"] == frames["-1:standing"]
    assert payload["stride"] == preview.GAIT_STRIDE
    assert payload["landing_seconds"] == preview.LANDING_SECONDS
    assert len(rendered) == 88
    assert len({frames["-1:takeoff"+str(i)] for i in range(9)}) == 3
    assert len({frames["-1:land"+str(i)] for i in range(9)}) == 3
    assert frames["-1:turning"] == frames["-1:standing"]
    assert frames["-1:settle0"] == frames["-1:standing"]
    assert frames["-1:settle8"] == frames["-1:idle"]
    assert frames["-1:rise0"] == frames["-1:idle"]
    assert len({frames["-1:settle"+str(i)] for i in range(9)}) == 4
    # No external images or source-image editing is involved in the player.
    assert all(url.startswith("data:image/png;base64,") for url in payload["images"])
