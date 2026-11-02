import json
import math
import unittest

from backyard_visitor.model import (Engine, Gift, PET_TRUST, SPRITE_WIDTH, GIFT_SIZE,
                                    EATING_MUZZLE_LEFT, EATING_MUZZLE_RIGHT, TAKEOFF_SECONDS, names_match)


def advance(engine, seconds, step=0.25):
    for _ in range(math.ceil(seconds / step)):
        engine.tick(step)


def present(engine, cat_id="amber", x=350):
    engine.open_gate()
    engine.invite(cat_id)
    visitor = engine.visitors[cat_id]
    visitor.x = visitor.target_x = x
    visitor.y = visitor.target_y = engine.floor_y
    visitor.state = "idle"
    visitor.stay_remaining = 1000
    visitor.parkour_cooldown = 1000
    engine._visit_countdown = 1000
    return visitor


class EngineTests(unittest.TestCase):
    def test_gate_is_required_and_restarts_closed(self):
        engine = Engine(seed=1)
        self.assertIsNone(engine.invite())
        engine.open_gate()
        self.assertIsNotNone(engine.invite())
        restored = Engine(engine.export())
        self.assertFalse(restored.gate_open)
        self.assertFalse(restored.visitors)
        advance(restored, 400)
        self.assertFalse(restored.visitors)

    def test_entry_and_exit_use_continuous_edges(self):
        engine = Engine(seed=4)
        engine.open_gate()
        engine.invite("amber")
        visitor = engine.visitors["amber"]
        self.assertTrue(visitor.x < 0 or visitor.x + SPRITE_WIDTH > 1280)
        previous = visitor.x
        for _ in range(160):
            engine.tick(0.25)
            self.assertLessEqual(abs(visitor.x - previous), 66 * 0.25 + 1e-8)
            previous = visitor.x
            if visitor.state == "idle":
                break
        self.assertEqual(visitor.state, "idle")
        self.assertGreaterEqual(visitor.x, 0)
        self.assertLessEqual(visitor.x + SPRITE_WIDTH, 1280)
        engine.open_gate(False)
        previous = visitor.x
        while "amber" in engine.visitors:
            engine.tick(0.25)
            self.assertLessEqual(abs(visitor.x - previous), 66 * 0.25 + 1e-8)
            previous = visitor.x
        self.assertTrue(visitor.x + SPRITE_WIDTH < 0 or visitor.x > 1280)

    def test_capacity_and_no_duplicate_visitor(self):
        engine = Engine(seed=2)
        engine.open_gate()
        self.assertEqual(engine.invite("amber"), "amber")
        self.assertIsNone(engine.invite("amber"))
        self.assertEqual(engine.invite("mist"), "mist")
        self.assertEqual(engine.invite("ink"), "ink")
        self.assertIsNone(engine.invite("onyx"))
        self.assertEqual(len(engine.visitors), 3)

    def test_weighted_visits_and_adoption(self):
        engine = Engine(seed=9)
        engine.open_gate()
        self.assertEqual(engine.invite("amber"), "amber")
        self.assertEqual(engine.invite("mist"), "mist")
        engine.open_gate(False)
        advance(engine, 40)
        self.assertFalse(engine.visitors)
        base = engine.visit_weights()
        engine.cats["amber"].trust = 80
        self.assertGreater(engine.visit_weights()["amber"], base["amber"])
        self.assertFalse(engine.adopt("amber"))
        engine.cats["amber"].trust = 100
        self.assertTrue(engine.adopt("amber"))
        weights = engine.visit_weights()
        self.assertGreater(weights["amber"] / weights["mist"], 100)
        engine.cats["mist"].trust = 100
        self.assertFalse(engine.adopt("mist"))
        self.assertEqual(engine.resident_id, "amber")

    def test_food_reduces_wait_without_opening_gate(self):
        engine = Engine(seed=4)
        self.assertTrue(engine.feed())
        self.assertFalse(engine.gate_open)
        engine.open_gate()
        engine._visit_countdown = 200
        engine.feed()
        self.assertLessEqual(engine._visit_countdown, 200)
        self.assertGreaterEqual(engine._visit_countdown, 156)
        engine.feed()
        self.assertFalse(engine.feed())
        self.assertEqual(engine.food, 3)

    def test_meal_grooming_trust_and_no_repeat_meal(self):
        engine = Engine(seed=4)
        visitor = present(engine)
        engine.set_bowl(visitor.x + SPRITE_WIDTH / 2, visitor.y + 97)
        initial_stay = visitor.stay_remaining
        engine.feed()
        advance(engine, 6)
        self.assertTrue(visitor.has_eaten)
        self.assertEqual(engine.food, 0)
        self.assertEqual(engine.cats["amber"].trust, 22)
        self.assertEqual(visitor.state, "grooming")
        self.assertGreater(visitor.stay_remaining, initial_stay)
        engine.feed()
        advance(engine, 8)
        self.assertEqual(engine.food, 1)
        self.assertEqual(engine.cats["amber"].trust, 22)

    def test_two_cats_cannot_consume_the_same_portion(self):
        engine = Engine(seed=2)
        first = present(engine, "amber", 500)
        second = present(engine, "mist", 500)
        engine.set_bowl(572, engine.floor_y + 97)
        engine.feed()
        advance(engine, 6)
        self.assertEqual(engine.food, 0)
        self.assertEqual(sum(v.has_eaten for v in (first, second)), 1)
        self.assertEqual(sum(c.trust > 0 for c in engine.cats.values()), 1)

    def test_name_gate_and_secret_never_in_events_or_export(self):
        engine = Engine(seed=5)
        present(engine)
        self.assertEqual(engine.name_cat("amber", "木木"), "unfamiliar")
        self.assertEqual(engine.call_cat("amber"), "ignored")
        engine.cats["amber"].trust = 35
        self.assertEqual(engine.call_cat("amber"), "recognized")
        self.assertEqual(engine.invite("mist"), "mist")
        # This checks formal-neighbour naming; guest restrictions have their
        # own lifecycle acceptance test.
        engine.cats["mist"].neighbor_status = "neighbor"
        secret = engine.cats["mist"].true_name
        self.assertNotIn(secret, repr(engine.cats["mist"]))
        self.assertNotIn(secret, json.dumps(engine.export(), ensure_ascii=False))
        self.assertNotIn(secret, json.dumps(engine.drain_events(), ensure_ascii=False))
        self.assertEqual(engine.name_cat("mist", secret), "true_name")
        self.assertEqual(engine.cats["mist"].trust, 100)
        self.assertEqual(engine.name_cat("mist", "  !!!  "), "invalid")

    def test_conservative_fuzzy_names(self):
        self.assertTrue(names_match(" ＭＡＲＩＧＯＬＤ! ", "Marigold"))
        self.assertTrue(names_match("Marigld", "Marigold"))
        self.assertFalse(names_match("Marigldx", "Marigold"))
        self.assertFalse(names_match("Milo", "Mila"))
        self.assertFalse(names_match("栗于", "栗子"))
        self.assertFalse(names_match("", ""))

    def test_pet_gates_and_cooldown(self):
        engine = Engine(seed=9)
        visitor = present(engine)
        self.assertFalse(engine.pet("amber"))
        self.assertEqual(engine.cats["amber"].trust, 0)
        engine.cats["amber"].trust = PET_TRUST
        advance(engine, 4.5)
        self.assertTrue(engine.pet("amber"))
        self.assertEqual(visitor.state, "purring")
        self.assertFalse(engine.pet("amber"))
        self.assertEqual(engine.cats["amber"].trust, PET_TRUST + 2)

    def test_startle_only_low_trust_and_near_fast_cursor(self):
        engine = Engine(seed=9)
        visitor = present(engine)
        self.assertFalse(engine.cursor_moved(visitor.x + 70, visitor.y + 40, 300))
        self.assertFalse(engine.cursor_moved(1200, 0, 1500))
        self.assertTrue(engine.cursor_moved(visitor.x + 70, visitor.y + 40, 1500))
        self.assertEqual(visitor.state, "fleeing")
        self.assertFalse(engine.startle("amber"))
        engine2 = Engine(seed=9)
        present(engine2)
        engine2.cats["amber"].trust = PET_TRUST
        self.assertFalse(engine2.startle("amber"))

    def test_carry_release_gate_close_does_not_teleport(self):
        engine = Engine(seed=4)
        visitor = present(engine)
        self.assertTrue(engine.carry("amber"))
        old_x = visitor.x
        advance(engine, 3)
        self.assertEqual(visitor.x, old_x)
        engine.open_gate(False)
        self.assertEqual(visitor.state, "carried")
        self.assertTrue(engine.release("amber", 500, 100))
        self.assertEqual((visitor.x, visitor.y), (500, 100))
        self.assertEqual(visitor.state, "jumping")
        self.assertEqual(visitor.route_motion, "leaving")
        self.assertEqual(visitor.takeoff_remaining, 0)
        engine.tick(0.25)
        progress = .25/visitor.jump_duration
        self.assertAlmostEqual(visitor.x, 500+(visitor.target_x-500)*progress)
        self.assertAlmostEqual(visitor.y, 100+(visitor.target_y-100)*progress-visitor.jump_arc*4*progress*(1-progress))
        self.assertLess(math.hypot(visitor.x-500, visitor.y-100), 30)

    def test_release_near_edge_and_invalid_coordinates(self):
        engine = Engine(seed=2)
        visitor = present(engine)
        engine.carry("amber")
        self.assertFalse(engine.release("amber", float("nan"), 0))
        self.assertTrue(engine.release("amber", 1, 300))
        self.assertEqual(visitor.state, "jumping")
        self.assertEqual(visitor.route_motion, "leaving")
        self.assertEqual(visitor.takeoff_remaining, 0)

    def test_fight_intervention_fur_and_exit(self):
        engine = Engine(seed=7)
        present(engine, "amber", 400)
        present(engine, "mist", 440)
        self.assertTrue(engine.trigger_fight())
        self.assertEqual({v.state for v in engine.visitors.values()}, {"fighting"})
        self.assertTrue(engine.intervene("mist"))
        self.assertFalse(engine.intervene("amber"))
        self.assertEqual(engine.cats["mist"].trust, 4)
        self.assertEqual({v.state for v in engine.visitors.values()}, {"fleeing"})
        self.assertEqual([g.kind for g in engine.gifts], ["fur"])
        saved = Engine(engine.export())
        self.assertEqual([g.kind for g in saved.gifts], ["fur"])
        self.assertTrue(saved.collect_gift(saved.gifts[0].id))
        self.assertFalse(saved.gifts)

    def test_fight_finishes_without_intervention(self):
        engine = Engine(seed=7)
        present(engine, "amber", 400)
        present(engine, "mist", 440)
        engine.trigger_fight()
        advance(engine, 5.25)
        self.assertEqual(len(engine.gifts), 1)
        self.assertEqual({v.state for v in engine.visitors.values()}, {"fleeing"})

    def test_gifts_collect_once_discard_without_reward(self):
        engine = Engine(seed=1)
        engine.gifts = [Gift("gift-1", "feather", 10, 20, "amber"), Gift("gift-2", "toy_mouse", 30, 40, "amber")]
        self.assertTrue(engine.collect_gift("gift-1"))
        self.assertFalse(engine.collect_gift("gift-1"))
        self.assertEqual(engine.cats["amber"].trust, 3)
        self.assertTrue(engine.discard_gift("gift-2"))
        self.assertEqual(engine.cats["amber"].trust, 3)

    def test_resident_does_not_time_out_but_respects_closed_gate(self):
        engine = Engine(seed=2)
        visitor = present(engine)
        engine.cats["amber"].trust = 100
        engine.adopt("amber")
        visitor.stay_remaining = 0.1
        advance(engine, 2)
        self.assertNotIn(visitor.state, {"leaving", "fleeing"})
        engine.open_gate(False)
        self.assertEqual(visitor.state, "leaving")
        self.assertEqual(visitor.takeoff_remaining, 0)

    def test_deterministic_simulation(self):
        first, second = Engine(seed=71, demo=True), Engine(seed=71, demo=True)
        for engine in (first, second):
            engine.open_gate()
            engine.feed()
            advance(engine, 120)
        self.assertEqual(first.export(), second.export())
        self.assertEqual(first.visitors, second.visitors)
        self.assertEqual(first.drain_events(), second.drain_events())

    def test_negative_desktop_coordinates_and_paths(self):
        engine = Engine(seed=2)
        engine.set_bounds(-1920, -100, 1920, 1080)
        self.assertEqual(engine.floor_y, 868)
        engine.set_paths([(-1500, 200), (10, 10), (float("nan"), 10)])
        self.assertEqual(engine.paths, [(-1500.0, 200.0)])
        engine.open_gate()
        engine.invite("ink")
        visitor = engine.visitors["ink"]
        self.assertTrue(visitor.x < -1920 or visitor.x + SPRITE_WIDTH > 0)

    def test_negative_screen_restart_preserves_bowl_and_gifts(self):
        engine = Engine(seed=3)
        engine.set_bounds(-1920, -100, 1920, 1080)
        engine.set_bowl(-1400, 800)
        engine.gifts = [Gift("saved", "feather", -1500, 850, "amber")]
        restored = Engine(engine.export())
        restored.set_bounds(-1920, -100, 1920, 1080)
        self.assertEqual(restored.bowl, (-1400.0, 800.0))
        self.assertEqual((restored.gifts[0].x, restored.gifts[0].y), (-1500.0, 850.0))
        restored.set_bounds(0, 0, 1920, 1080)
        self.assertEqual(restored.bowl, (520.0, 900.0))
        self.assertEqual((restored.gifts[0].x, restored.gifts[0].y), (420.0, 950.0))
        for malformed in ([0, 0, -1, 1000], [float("nan"), 0, 1920, 1080], "bad"):
            self.assertEqual(Engine({"bounds": malformed}).bounds, (0.0, 0.0, 1280.0, 720.0))

    def test_tick_validation_and_sleep_resume_cap(self):
        engine = Engine(seed=2)
        visitor = present(engine)
        engine.tick(1_000_000)
        self.assertEqual(visitor.age, 5)
        for value in (-1, float("nan"), float("inf"), "4"):
            with self.assertRaises(ValueError):
                engine.tick(value)

    def test_corrupt_state_fields_are_sanitized(self):
        engine = Engine({"version": 1, "cats": {"amber": {"trust": float("nan"), "nickname": ["bad"], "adopted": True},
                                                "mist": {"trust": 500, "adopted": True, "visits": 1},
                                                "ink": {"trust": 100, "adopted": True, "visits": 1}},
                         "food": -10, "bowl": [None, "oops"],
                         "gifts": [{"id": "gift-1", "kind": "fur", "cat_id": "amber", "x": "bad", "y": float("inf")},
                                   {"id": "gift-1", "kind": "fur", "cat_id": "amber"},
                                   {"id": "gift-2", "kind": "danger", "cat_id": "amber"},
                                   {"id": "gift-3", "kind": [], "cat_id": "amber"},
                                   {"id": "gift-4", "kind": "fur", "cat_id": []}]})
        self.assertEqual(engine.cats["amber"].trust, 0)
        self.assertEqual(engine.cats["amber"].nickname, "")
        self.assertEqual(engine.resident_id, "mist")
        self.assertEqual(engine.food, 0)
        self.assertEqual(len(engine.gifts), 1)
        self.assertEqual(engine.gifts[0].x, 0)
        json.dumps(engine.export(), allow_nan=False)

    def test_legacy_and_unknown_future_save_versions(self):
        legacy = Engine({"cats": [{"id": "amber", "trust_level": 60, "nickname": "团子"}], "food": 2})
        self.assertEqual(legacy.cats["amber"].trust, 60)
        self.assertEqual(legacy.cats["amber"].nickname, "团子")
        future = Engine({"version": 999, "food": 3})
        self.assertEqual(future.food, 0)
        for broken in (None, [], "invalid", {"cats": 10}, {"gifts": 10}):
            self.assertFalse(Engine(broken).gate_open)

    def test_calm_mode_persists_and_suppresses_conflicts(self):
        engine = Engine(seed=4)
        first = present(engine, "amber", 400)
        present(engine, "mist", 440)
        first.state = "playing"
        engine.calm_mode = True
        self.assertFalse(engine.trigger_fight())
        engine.tick(0.25)
        self.assertEqual(first.state, "idle")
        self.assertTrue(Engine(engine.export()).calm_mode)

    def test_window_jump_follows_arc_and_finishes_on_edge(self):
        engine = Engine(seed=4)
        visitor = present(engine)
        start_x, start_y = visitor.x, visitor.y
        engine._walk(visitor, start_x + 100, start_y - 100)
        self.assertEqual(visitor.state, "jumping")
        engine.tick(TAKEOFF_SECONDS)
        self.assertEqual((visitor.x, visitor.y), (start_x, start_y))
        engine.tick(visitor.jump_duration / 2)
        self.assertAlmostEqual(visitor.x, start_x + 32)
        self.assertLess(visitor.y, start_y - 50)
        engine.tick(visitor.jump_duration / 2)
        self.assertAlmostEqual(visitor.x, start_x + 64)
        self.assertEqual(visitor.route_stage, "finish")
        advance(engine, 1.2, .02)
        self.assertAlmostEqual(visitor.x, start_x + 100)
        self.assertAlmostEqual(visitor.y, start_y - 100)
        self.assertEqual(visitor.state, "idle")

    def test_demo_scenes_are_separate_and_preserve_arrival(self):
        self.assertFalse(Engine().demo_scene("trust"))
        engine = Engine(seed=4, demo=True)
        self.assertTrue(engine.demo_scene("gift"))
        visitor = next(iter(engine.visitors.values()))
        self.assertEqual(visitor.state, "peeking")
        self.assertEqual(visitor.carrying_gift, "feather")
        self.assertFalse(engine.gifts)
        advance(engine, 25)
        self.assertTrue(any(g.kind == "feather" for g in engine.gifts))
        engine = Engine(seed=4, demo=True)
        self.assertTrue(engine.demo_scene("fight"))
        advance(engine, 40)
        kinds = [event["kind"] for event in engine.drain_events()]
        self.assertIn("fight_start", kinds)
        self.assertIn("fur", kinds)

    def test_meal_muzzle_aligns_from_both_sides_and_very_close(self):
        for center_delta in (-300, 300, -1, 1, 0):
            with self.subTest(center_delta=center_delta):
                engine = Engine(seed=4)
                engine.set_bounds(-1920, -100, 1920, 1080)
                bowl_x = -1000
                visitor = present(engine, x=bowl_x - SPRITE_WIDTH / 2 + center_delta)
                expected_facing = 1 if center_delta <= 0 else -1
                # Near the bowl, repositioning moves opposite to the eventual
                # eating direction; arrival must still turn the muzzle to food.
                visitor.facing = -expected_facing
                engine.set_bowl(bowl_x, engine.floor_y + 97)
                engine.feed()
                for _ in range(300):
                    engine.tick(0.1)
                    if visitor.state == "eating":
                        break
                self.assertEqual(visitor.state, "eating")
                self.assertEqual(visitor.facing, expected_facing)
                muzzle = EATING_MUZZLE_LEFT if visitor.facing < 0 else EATING_MUZZLE_RIGHT
                self.assertAlmostEqual(visitor.x + muzzle, engine.bowl[0])
                self.assertGreaterEqual(visitor.x, engine.bounds[0])
                self.assertLessEqual(visitor.x + SPRITE_WIDTH, engine.bounds[0] + engine.bounds[2])

    def test_meal_at_screen_sides_keeps_muzzle_and_body_inside(self):
        for bowl_x, start_x, facing in [(-1896, -1400, -1), (-24, -600, 1)]:
            with self.subTest(bowl_x=bowl_x):
                engine = Engine(seed=4)
                engine.set_bounds(-1920, -100, 1920, 1080)
                visitor = present(engine, x=start_x)
                engine.set_bowl(bowl_x, engine.floor_y + 97)
                engine.feed()
                for _ in range(300):
                    engine.tick(0.1)
                    if visitor.state == "eating":
                        break
                self.assertEqual(visitor.state, "eating")
                self.assertEqual(visitor.facing, facing)
                muzzle = EATING_MUZZLE_LEFT if facing < 0 else EATING_MUZZLE_RIGHT
                self.assertAlmostEqual(visitor.x + muzzle, engine.bowl[0])
                self.assertGreaterEqual(visitor.x, -1920)
                self.assertLessEqual(visitor.x + SPRITE_WIDTH, 0)

    def test_floor_arrival_gifts_and_fight_fur_stay_fully_inside(self):
        for scene in ("gift", "fight"):
            with self.subTest(scene=scene):
                engine = Engine(seed=7)
                engine.set_bounds(-1920, -100, 1920, 1080)
                visitor = present(engine, x=-1500)
                if scene == "gift":
                    visitor.carrying_gift = "feather"
                    engine._walk(visitor, visitor.x, visitor.y)
                    engine.tick(0.1)
                else:
                    present(engine, "mist", -1460)
                    self.assertTrue(engine.trigger_fight())
                    advance(engine, 5.25)
                self.assertEqual(len(engine.gifts), 1)
                gift = engine.gifts[0]
                self.assertEqual(gift.kind, "feather" if scene == "gift" else "fur")
                self.assertGreaterEqual(gift.x, -1920)
                self.assertLessEqual(gift.x + GIFT_SIZE, 0)
                self.assertGreaterEqual(gift.y, -100)
                self.assertLessEqual(gift.y + GIFT_SIZE, 980)
                self.assertEqual(gift.y, 980 - GIFT_SIZE)

    def test_gifts_clamp_on_drop_and_when_switching_to_smaller_screen(self):
        engine = Engine(seed=3)
        engine.set_bounds(-1920, -100, 1920, 1080)
        engine._drop("feather", -1, 979, "amber")
        engine._drop("fur", -3000, -500, "amber")
        self.assertEqual((engine.gifts[0].x, engine.gifts[0].y), (-GIFT_SIZE, 980 - GIFT_SIZE))
        self.assertEqual((engine.gifts[1].x, engine.gifts[1].y), (-1920, -100))
        engine.set_bounds(100, -600, 400, 300)
        self.assertEqual((engine.gifts[0].x, engine.gifts[0].y), (500 - GIFT_SIZE, -300 - GIFT_SIZE))
        self.assertEqual((engine.gifts[1].x, engine.gifts[1].y), (100, -600))


    def test_playful_cat_pursues_slow_cursor_at_bounded_speed(self):
        engine = Engine(seed=3)
        visitor = present(engine, x=350)
        engine.cats["amber"].trust = 80
        engine._state(visitor, "playing")
        visitor.next_action = 100
        cursor = (visitor.x + SPRITE_WIDTH / 2 + 180, visitor.y + 56)
        self.assertFalse(engine.cursor_moved(*cursor, 12))
        initial_distance = math.hypot(cursor[0] - visitor.x - SPRITE_WIDTH / 2,
                                      cursor[1] - visitor.y - 56)
        for _ in range(12):
            before = (visitor.x, visitor.y)
            engine.tick(0.1)
            self.assertLessEqual(math.hypot(visitor.x - before[0], visitor.y - before[1]), 9 + 1e-8)
            self.assertGreaterEqual(visitor.x, engine.bounds[0])
            self.assertLessEqual(visitor.x + SPRITE_WIDTH, engine.bounds[0] + engine.bounds[2])
            self.assertGreaterEqual(visitor.y, engine.bounds[1])
            self.assertLessEqual(visitor.y, engine.floor_y)
        self.assertLess(math.hypot(cursor[0] - visitor.x - SPRITE_WIDTH / 2,
                                   cursor[1] - visitor.y - 56), initial_distance)
        self.assertGreater(visitor.x, 350)

    def test_play_cursor_target_is_sampled_without_rapid_direction_jitter(self):
        engine = Engine(seed=3)
        visitor = present(engine, x=350)
        engine.cats["amber"].trust = 80
        engine._state(visitor, "playing")
        visitor.next_action = 100
        engine.cursor_moved(602, visitor.y + 56, 0)
        engine.tick(0.1)
        first_target = (visitor.target_x, visitor.target_y)
        engine.cursor_moved(322, visitor.y + 56, 0)
        for _ in range(5):
            engine.tick(0.1)
            self.assertEqual((visitor.target_x, visitor.target_y), first_target)
        advance(engine, 0.4, step=0.1)
        self.assertNotEqual((visitor.target_x, visitor.target_y), first_target)
        self.assertEqual(visitor.facing, -1)

    def test_play_far_cursor_lounges_and_negative_screen_stays_contained(self):
        engine = Engine(seed=3)
        engine.set_bounds(-1920, -100, 1920, 1080)
        visitor = present(engine, x=-1900)
        engine.cats["amber"].trust = 80
        engine._state(visitor, "playing")
        visitor.next_action = 100
        engine.cursor_moved(10000, 10000, 0)
        engine.tick(0.1)
        self.assertEqual((visitor.route_final_x, visitor.route_final_y), (-1032, 384))
        # A cursor near the top-left border yields a contained sprite target,
        # even though centering the unbounded sprite on it would cross the edge.
        visitor.x, visitor.y = -1910, -90
        engine._state(visitor, "playing")
        engine.cursor_moved(-1919, -99, 0)
        for _ in range(10):
            engine.tick(0.1)
            self.assertGreaterEqual(visitor.x, -1920)
            self.assertGreaterEqual(visitor.y, -100)
            self.assertLessEqual(visitor.x + SPRITE_WIDTH, 0)
            self.assertLessEqual(visitor.y + 112, 980)

    def test_play_stops_for_calm_carry_and_low_trust_startle(self):
        for condition in ("calm", "carry", "startle", "graceful"):
            with self.subTest(condition=condition):
                engine = Engine(seed=3)
                cid = "ink" if condition == "graceful" else "amber"
                visitor = present(engine, cid, 350)
                engine._state(visitor, "playing")
                visitor.next_action = 100
                position = (visitor.x, visitor.y)
                if condition == "calm":
                    engine.calm_mode = True
                elif condition == "carry":
                    self.assertTrue(engine.carry(cid))
                speed = 1000 if condition == "startle" else 0
                startled = engine.cursor_moved(visitor.x + 90, visitor.y + 56, speed)
                self.assertEqual(startled, condition == "startle")
                engine.tick(0.1)
                if condition == "startle":
                    self.assertEqual(visitor.state, "fleeing")
                else:
                    self.assertEqual((visitor.x, visitor.y), position)
                    self.assertEqual(visitor.state, "carried" if condition == "carry" else "idle")

    def test_gift_arrival_prefers_permitted_icon_spot_without_teleport(self):
        engine = Engine(seed=4, demo=True)
        engine.set_bounds(-1920, -100, 1920, 1080)
        engine.set_gift_spots([(-1200, 200), (float("nan"), 10), [1], "bad", (-1200, 200)])
        self.assertEqual(engine.gift_spots, [(-1200.0, 200.0)])
        self.assertTrue(engine.demo_scene("gift"))
        visitor = engine.visitors["amber"]
        self.assertEqual(visitor.state, "peeking")
        self.assertTrue(visitor.x < -1920 or visitor.x + SPRITE_WIDTH > 0)
        self.assertEqual(visitor.gift_spot, (-1200, 200))
        self.assertFalse(engine.gifts)
        for _ in range(200):
            before = (visitor.x, visitor.y)
            speed = (math.hypot(visitor.target_x-visitor.jump_start_x,
                     abs(visitor.target_y-visitor.jump_start_y)+4*visitor.jump_arc)/visitor.jump_duration
                     if visitor.state == "jumping" else 66)
            engine.tick(0.25)
            self.assertLessEqual(math.dist(before, (visitor.x, visitor.y)), speed*.25+1e-8)
            if engine.gifts:
                break
        self.assertEqual(len(engine.gifts), 1)
        self.assertEqual((engine.gifts[0].x, engine.gifts[0].y), (-1200, 200))
        self.assertIsNone(visitor.carrying_gift)
        self.assertIsNone(visitor.gift_spot)
        self.assertNotIn("gift_spots", engine.export())
        self.assertNotIn("cursor", engine.export())

    def test_new_gift_spots_do_not_interrupt_pending_meal(self):
        engine = Engine(seed=3)
        visitor = present(engine, x=350)
        engine.set_bowl(800, engine.floor_y + 97)
        engine.feed()
        engine.tick(0.1)
        self.assertEqual(visitor.arrival_state, "eating")
        visitor.carrying_gift = "feather"
        pending = (visitor.state, visitor.target_x, visitor.target_y,
                   visitor.arrival_state, visitor.arrival_facing)
        engine.set_gift_spots([(100, 100)])
        self.assertEqual((visitor.state, visitor.target_x, visitor.target_y,
                          visitor.arrival_state, visitor.arrival_facing), pending)
        self.assertIsNone(visitor.gift_spot)

    def test_gift_spots_clamp_and_clear_on_permission_or_screen_change(self):
        engine = Engine(seed=3)
        engine.set_gift_spots([(-100, -100), (9000, 9000)])
        self.assertEqual(engine.gift_spots, [(0, 0), (1280 - GIFT_SIZE, 720 - GIFT_SIZE)])
        engine.demo = True
        engine.demo_scene("gift")
        visitor = engine.visitors["amber"]
        self.assertIsNotNone(visitor.gift_spot)
        engine.set_gift_spots([])
        self.assertIsNone(visitor.gift_spot)
        self.assertEqual(visitor.target_y, engine.floor_y)
        engine.set_gift_spots([(300, 300)])
        engine.cursor_moved(600, 600, 0)
        engine.set_bounds(-1920, 0, 1920, 1080)
        self.assertFalse(engine.gift_spots)
        self.assertIsNone(visitor.gift_spot)
        self.assertIsNone(engine._cursor)



if __name__ == "__main__":
    unittest.main()
