import tempfile
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

from scoring import analyze_photo, difference_hash, find_jpgs, hash_distance, mark_duplicates


class ScoringTests(unittest.TestCase):
    def test_only_jpgs_are_discovered(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            Image.new("RGB", (40, 30), "red").save(root / "a.JPG")
            Image.new("RGB", (40, 30), "blue").save(root / "b.png")
            (root / "clip.mp4").write_bytes(b"not a video")
            self.assertEqual([p.name for p in find_jpgs(root)], ["a.JPG"])

    def test_score_has_valid_range(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "photo.jpg"
            data = np.tile(np.arange(0, 255, dtype=np.uint8), (180, 1))
            Image.fromarray(np.dstack((data, np.flip(data, 1), data))).save(path)
            result = analyze_photo(path)
            self.assertIn(result.stars, range(1, 6))
            self.assertTrue(0 <= result.score <= 100)

    def test_hash_and_duplicate_detection(self):
        image = Image.new("RGB", (64, 64), "gray")
        self.assertEqual(hash_distance(difference_hash(image), difference_hash(image)), 0)
        with tempfile.TemporaryDirectory() as temp:
            paths = []
            for name in ("a.jpg", "b.jpg"):
                path = Path(temp) / name
                image.save(path)
                paths.append(path)
            scores = [analyze_photo(p) for p in paths]
            mark_duplicates(scores)
            self.assertEqual(sum(item.duplicate for item in scores), 1)


if __name__ == "__main__":
    unittest.main()
