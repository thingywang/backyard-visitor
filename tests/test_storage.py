import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backyard_visitor.storage import MAX_SAVE_BYTES, load_state, save_state


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "nested" / "state.json"

    def test_atomic_roundtrip_unicode_and_backup(self):
        first = {"version": 1, "cats": {"amber": {"nickname": "团子"}}, "food": 1}
        save_state(self.path, first)
        self.assertEqual(load_state(self.path), first)
        second = {**first, "food": 2}
        save_state(self.path, second)
        self.assertEqual(load_state(self.path), second)
        self.assertEqual(json.loads(self.path.with_suffix(".json.bak").read_text()), first)
        self.assertFalse(list(self.path.parent.glob("*.tmp")))

    def test_missing_corrupt_oversize_and_nonfinite_files(self):
        self.assertIsNone(load_state(self.path))
        self.path.parent.mkdir()
        for content in (b"{broken", b"[]", b"\xff\xfe", b'{"food": NaN}', b" " * (MAX_SAVE_BYTES + 1)):
            self.path.write_bytes(content)
            self.assertIsNone(load_state(self.path))
            self.assertEqual(self.path.read_bytes(), content)

    def test_corrupt_primary_recovers_previous_good_snapshot(self):
        save_state(self.path, {"food": 1})
        save_state(self.path, {"food": 2})
        self.path.write_text("{interrupted")
        self.assertEqual(load_state(self.path), {"food": 1})
        save_state(self.path, {"food": 3})
        self.assertEqual(load_state(self.path), {"food": 3})
        self.assertEqual(json.loads(self.path.with_suffix(".json.bak").read_text()), {"food": 1})

    def test_serialization_failure_does_not_damage_save(self):
        save_state(self.path, {"food": 1})
        original = self.path.read_bytes()
        with self.assertRaises(ValueError):
            save_state(self.path, {"food": float("nan")})
        with self.assertRaises(ValueError):
            save_state(self.path, {"too_big": "x" * MAX_SAVE_BYTES})
        with self.assertRaises(TypeError):
            save_state(self.path, [])
        self.assertEqual(self.path.read_bytes(), original)

    def test_interrupted_replace_retains_valid_original_and_removes_temp(self):
        save_state(self.path, {"food": 1})
        with patch("backyard_visitor.storage.os.replace", side_effect=OSError("disk unavailable")):
            with self.assertRaises(OSError):
                save_state(self.path, {"food": 2})
        self.assertEqual(load_state(self.path), {"food": 1})
        self.assertFalse(list(self.path.parent.glob("*.tmp")))

    def test_file_as_parent_returns_save_error(self):
        parent = Path(self.temp.name) / "a-file"
        parent.write_text("existing")
        with self.assertRaises(OSError):
            save_state(parent / "state.json", {"food": 1})
        self.assertEqual(parent.read_text(), "existing")


if __name__ == "__main__":
    unittest.main()
