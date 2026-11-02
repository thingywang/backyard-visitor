"""Pure geometry checks: no desktop permission, Qt instance, or native polling."""

import math
import unittest

from backyard_visitor.desktop import DesktopTarget, Rect, visible_window_edges


def target(identity, x, y, width, height):
    return DesktopTarget(identity, identity, Rect(x, y, width, height))


def edges_for(windows, identity="back", screen=Rect(0, 0, 1000, 800), **sizes):
    return [
        edge for edge in visible_window_edges(windows, screen, **sizes)
        if edge[0].startswith(f"{identity}:top:")
    ]


class VisibleWindowEdgesTests(unittest.TestCase):
    def test_front_to_back_order_and_split_edges(self):
        front = target("front", 400, 200, 200, 300)
        back = target("back", 0, 400, 1000, 300)
        self.assertEqual(visible_window_edges([front, back], Rect(0, 0, 1000, 800)), [
            ("front:top:0", 400, 600, 200),
            ("back:top:0", 0, 400, 400),
            ("back:top:1", 600, 1000, 400),
        ])
        self.assertEqual(edges_for([back, front]), [("back:top:0", 0, 1000, 400)])

    def test_foreground_above_edge_still_blocks_sprite_body(self):
        self.assertEqual(edges_for([
            target("front", 250, 300, 300, 50),
            target("back", 0, 400, 1000, 300),
        ]), [("back:top:0", 0, 250, 400), ("back:top:1", 550, 1000, 400)])

    def test_touching_top_of_sprite_band_does_not_block(self):
        self.assertEqual(edges_for([
            target("front", 250, 200, 300, 88),
            target("back", 0, 400, 1000, 300),
        ]), [("back:top:0", 0, 1000, 400)])

    def test_touching_support_edge_from_above_blocks_body(self):
        self.assertEqual(edges_for([
            target("front", 250, 300, 300, 100),
            target("back", 0, 400, 1000, 300),
        ]), [("back:top:0", 0, 250, 400), ("back:top:1", 550, 1000, 400)])

    def test_same_top_covers_edge_but_window_below_does_not(self):
        back = target("back", 0, 400, 1000, 300)
        self.assertEqual(edges_for([target("front", 0, 400, 1000, 50), back]), [])
        self.assertEqual(edges_for([target("front", 0, 400.01, 1000, 50), back]),
                         [("back:top:0", 0, 1000, 400)])

    def test_horizontal_boundary_contact_does_not_block(self):
        self.assertEqual(edges_for([
            target("left", 0, 300, 200, 200),
            target("right", 800, 300, 200, 200),
            target("back", 200, 400, 600, 300),
        ]), [("back:top:0", 200, 800, 400)])

    def test_overlapping_and_touching_occluders_are_merged(self):
        self.assertEqual(edges_for([
            target("a", 200, 300, 200, 250),
            target("b", 350, 350, 200, 250),
            target("c", 550, 350, 100, 250),
            target("back", 0, 500, 1000, 300),
        ]), [("back:top:0", 0, 200, 500), ("back:top:1", 650, 1000, 500)])

    def test_too_narrow_foreground_still_blocks(self):
        windows = [target("front", 400, 200, 20, 300), target("back", 0, 400, 1000, 300)]
        self.assertEqual(edges_for(windows, "front"), [])
        self.assertEqual(edges_for(windows),
                         [("back:top:0", 0, 400, 400), ("back:top:1", 420, 1000, 400)])

    def test_foreground_without_headroom_still_blocks(self):
        self.assertEqual(visible_window_edges([
            target("front", 100, -20, 800, 400),
            target("back", 0, 300, 1000, 300),
        ], Rect(0, 0, 1000, 800)), [])

    def test_partial_offscreen_windows_are_clipped(self):
        self.assertEqual(visible_window_edges([
            target("partial", -50, 200, 200, 900),
            target("small", 900, 500, 300, 300),
            target("outside", -500, 300, 300, 200),
        ], Rect(0, 0, 1000, 800)), [("partial:top:0", 0, 150, 200)])

    def test_exact_width_and_headroom_fit(self):
        self.assertEqual(visible_window_edges([
            target("fit", 10, 112, 144, 10),
            target("too-high", 300, 111.99, 144, 10),
            target("too-thin", 500, 300, 143.99, 10),
            target("below", 0, 800, 1000, 20),
        ], Rect(0, 0, 1000, 800)), [("fit:top:0", 10, 154, 112)])

    def test_negative_monitor_coordinates_and_fractional_points(self):
        windows = [target("back", -1100.25, -688.25, 800.5, 400)]
        self.assertEqual(edges_for(windows, screen=Rect(-1200.5, -800.25, 1200, 800)),
                         [("back:top:0", -1100.25, -299.75, -688.25)])
        self.assertEqual(edges_for(windows, screen=Rect(0, 0, 1920, 1080)), [])

    def test_sprite_dimensions_use_logical_points_without_pixel_scaling(self):
        windows = [target("back", 20.5, 70.25, 96.5, 50)]
        screen = Rect(0.5, 10.25, 500, 400)
        self.assertEqual(edges_for(windows, screen=screen, sprite_width=96.5, sprite_height=60),
                         [("back:top:0", 20.5, 117, 70.25)])
        self.assertEqual(edges_for(windows, screen=screen), [])

    def test_segment_ids_survive_translation(self):
        windows = [target("front", 400, 200, 200, 300), target("back", 0, 400, 1000, 300)]
        moved = [
            target(w.id, w.rect.x + 10, w.rect.y + 20, w.rect.width, w.rect.height)
            for w in windows
        ]
        screen = Rect(-100, -100, 1500, 1000)
        before, after = visible_window_edges(windows, screen), visible_window_edges(moved, screen)
        self.assertEqual(after, [(name, left + 10, right + 10, top + 20)
                                 for name, left, right, top in before])

    def test_filtering_short_gap_does_not_renumber_other_gap(self):
        back = target("back", 0, 400, 1000, 300)
        self.assertEqual(edges_for([target("front", 140, 200, 200, 300), back]),
                         [("back:top:1", 340, 1000, 400)])
        self.assertEqual(edges_for([target("front", 150, 200, 200, 300), back]),
                         [("back:top:0", 0, 150, 400), ("back:top:1", 350, 1000, 400)])

    def test_invalid_rectangles_do_not_become_occluders(self):
        windows = [
            target("nan", 0, 200, math.nan, 300),
            target("negative", 0, 200, -100, 300),
            target("flat", 0, 200, 1000, 0),
            target("inf", math.inf, 200, 1000, 300),
            target("overflow", 1e308, 200, 1e308, 300),
            target("back", 0, 400, 1000, 300),
        ]
        self.assertEqual(edges_for(windows), [("back:top:0", 0, 1000, 400)])
        self.assertEqual(visible_window_edges(windows, Rect(0, 0, math.nan, 800)), [])

    def test_invalid_sprite_sizes_return_no_edges(self):
        windows = [target("back", 0, 400, 1000, 300)]
        for dimension in ("sprite_width", "sprite_height"):
            for invalid in (0, -1, math.nan, math.inf, "invalid", None):
                with self.subTest(dimension=dimension, value=invalid):
                    self.assertEqual(edges_for(windows, **{dimension: invalid}), [])

    def test_duplicate_window_identity_does_not_duplicate_edges(self):
        window = target("back", 0, 400, 1000, 300)
        self.assertEqual(edges_for([window, window]), [("back:top:0", 0, 1000, 400)])


if __name__ == "__main__":
    unittest.main()
