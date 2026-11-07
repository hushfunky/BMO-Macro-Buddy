"""Logic tests that run anywhere (no Windows, no Roblox):  python -m unittest discover tests"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import bmo_common as c  # noqa: E402

ADVS = c.build_adventures({})


class PlanParsing(unittest.TestCase):
    def test_chain(self):
        steps, bad = c.parse_plan("raid:5, challenges x3; lich 2 > farm", ADVS)
        self.assertEqual(steps, [("raid", 5), ("challenges", 3), ("raid", 2), ("world", 0)])
        self.assertEqual(bad, [])

    def test_missing_count_only_allowed_last(self):
        steps, bad = c.parse_plan("raid, challenges", ADVS)
        self.assertEqual(steps, [("challenges", 0)])
        self.assertEqual(len(bad), 1)

    def test_unknown_and_empty(self):
        self.assertEqual(c.parse_plan("", ADVS), ([], []))
        self.assertEqual(len(c.parse_plan("xmas", ADVS)[1]), 1)


class SafeFiles(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def test_atomic_write_replaces_and_leaves_no_temp(self):
        p = self.dir / "s.json"
        c.write_json_atomic(p, {"a": 1})
        c.write_json_atomic(p, {"a": 2})
        self.assertEqual(json.loads(p.read_text(encoding="utf-8")), {"a": 2})
        self.assertEqual([f.name for f in self.dir.iterdir()], ["s.json"])

    def test_read_json_missing_gives_default(self):
        self.assertEqual(c.read_json(self.dir / "nope.json", {"d": 1}), {"d": 1})

    def test_read_json_keeps_damaged_file_aside(self):
        p = self.dir / "s.json"
        p.write_text('{"half": ', encoding="utf-8")
        self.assertEqual(c.read_json(p, {}), {})
        self.assertFalse(p.exists())
        self.assertTrue((self.dir / "s.json.bad").exists())


class Routines(unittest.TestCase):
    def test_validation_flags_problems(self):
        r = {"steps": [{"do": "press"}, {"do": "nonsense"}, {"do": "sleep", "seconds": "abc"}]}
        msgs = " | ".join(t for _lvl, t in c.validate_routine(r, self.dir_for_templates()))
        self.assertIn("needs 'key'", msgs)
        self.assertIn("unknown action", msgs)
        self.assertIn("wrong kind of value", msgs)
        self.assertIn("never plays a match", msgs)

    def test_good_routine_is_clean(self):
        r = {"steps": [{"do": "press", "key": "e"}, {"do": "play_match", "kind": "challenge"}]}
        self.assertEqual(c.validate_routine(r, self.dir_for_templates()), [])

    def test_bad_routine_file_is_reported_not_fatal(self):
        d = Path(tempfile.mkdtemp())
        (d / "routines").mkdir()
        (d / "routines" / "broken.json").write_text("{nope", encoding="utf-8")
        (d / "routines" / "ok.json").write_text(json.dumps({"steps": []}), encoding="utf-8")
        routines, errors = c.load_routines(d)
        self.assertIn("ok", routines)
        self.assertEqual([n for n, _ in errors], ["broken.json"])

    @staticmethod
    def dir_for_templates():
        return Path(tempfile.mkdtemp())


if __name__ == "__main__":
    unittest.main()


class WalkingRecorder(unittest.TestCase):
    def test_single_key_hold(self):
        t = c.MoveTracker()
        t.press("w", 10.0)
        ev = t.release("w", 11.25)
        self.assertEqual(ev["keys"], [{"key": "w", "at": 0.0, "hold": 1.25}])

    def test_auto_repeat_is_ignored(self):
        t = c.MoveTracker()
        for i in range(6):                       # a held key keeps sending "pressed"
            t.press("w", 10.0 + i * 0.03)
        ev = t.release("w", 10.6)
        self.assertEqual(len(ev["keys"]), 1)
        self.assertAlmostEqual(ev["keys"][0]["hold"], 0.6, places=2)

    def test_diagonal_is_one_event(self):
        t = c.MoveTracker()
        t.press("w", 5.0)
        t.press("d", 5.3)
        self.assertIsNone(t.release("w", 6.0))   # d is still held: the walk isn't over
        ev = t.release("d", 6.4)
        self.assertEqual([(k["key"], k["at"], k["hold"]) for k in ev["keys"]], [("w", 0.0, 1.0), ("d", 0.3, 1.1)])

    def test_uppercase_and_other_keys(self):
        t = c.MoveTracker()
        self.assertTrue(t.is_move_key("W"))
        self.assertFalse(t.is_move_key("1"))
        self.assertIsNone(t.release("w", 1.0))   # a release we never saw press

    def test_split_moves_keeps_order(self):
        evs = [{"type": "key", "key": "1"}, {"type": "click"}, {"type": "move", "keys": []}, {"type": "key", "key": "2"}]
        parts = c.split_moves(evs)
        self.assertEqual([k for k, _ in parts], ["events", "move", "events"])
        self.assertEqual(len(parts[0][1]), 2)

    def test_old_style_move_event(self):
        self.assertEqual(c.move_keys_of({"type": "move", "key": "s", "hold": 0.5}), [("s", 0.0, 0.5)])
        self.assertEqual(c.move_keys_of({"type": "move"}), [])


class WallDetection(unittest.TestCase):
    def test_wall_after_moving(self):
        w = c.WallDetector(stall=0.5)
        t = 0.0
        for _ in range(5):                       # screen scrolling
            self.assertFalse(w.update(8.0, t))
            t += 0.1
        hit = False
        for _ in range(10):                      # then nothing changes
            hit = w.update(0.2, t) or hit
            t += 0.1
        self.assertTrue(hit)

    def test_flat_ground_is_not_a_wall(self):
        w = c.WallDetector(stall=0.5)
        self.assertFalse(any(w.update(0.1, i * 0.1) for i in range(40)))   # never saw it move

    def test_a_pause_in_motion_resets(self):
        w = c.WallDetector(stall=0.5)
        w.update(9.0, 0.0)
        w.update(0.1, 0.1)
        w.update(9.0, 0.3)                       # moving again before the stall time passed
        self.assertFalse(w.update(0.1, 0.5))


class ViewChain(unittest.TestCase):
    def test_translations_add_up(self):
        a, b = [[1, 0, 5], [0, 1, 7]], [[1, 0, -2], [0, 1, 1]]
        self.assertEqual(c.compose_affine(a, b), [[1.0, 0.0, 3.0], [0.0, 1.0, 8.0]])

    def test_applies_rightmost_first(self):
        scale2 = [[2, 0, 0], [0, 2, 0]]
        move = [[1, 0, 10], [0, 1, 0]]
        m = c.compose_affine(move, scale2)       # scale first, then move: x=1 -> 2 -> 12
        self.assertEqual(m[0][0] * 1 + m[0][2], 12.0)
