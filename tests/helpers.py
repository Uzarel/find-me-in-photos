"""Shared test helpers: synthetic images and a fake face extractor."""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from app.config import Settings
from app.faces import Face

IMAGE_SIDE = 32


def unit(values) -> np.ndarray:
    vector = np.array(values, dtype=np.float32)
    return vector / np.linalg.norm(vector)


def make_face(values, box=(10, 10, 50, 50), confidence=0.9) -> Face:
    return Face(box=box, confidence=confidence, embedding=unit(values))


def solid_image(level: int) -> np.ndarray:
    return np.full((IMAGE_SIDE, IMAGE_SIDE, 3), level, dtype=np.uint8)


def write_image(path: Path, level: int) -> Path:
    assert cv2.imwrite(str(path), solid_image(level))
    return path


def encode_image(level: int) -> bytes:
    ok, buffer = cv2.imencode(".png", solid_image(level))
    assert ok
    return buffer.tobytes()


class FakeExtractor:
    """Returns canned faces keyed by the gray level of a solid test image."""

    def __init__(self, faces_by_level):
        self._faces_by_level = dict(faces_by_level)
        self.calls = 0

    def extract(self, image, max_side):
        self.calls += 1
        return self._faces_by_level.get(int(image[0, 0, 0]), ())


def make_settings(root: Path, **overrides) -> Settings:
    photos_dir = root / "photos"
    data_dir = root / "data"
    photos_dir.mkdir()
    data_dir.mkdir()
    return replace(_base_settings(root, photos_dir, data_dir), **overrides)


def _base_settings(root: Path, photos_dir: Path, data_dir: Path) -> Settings:
    return Settings(
        photos_dir=photos_dir,
        data_dir=data_dir,
        detector_path=root / "detector.onnx",
        recognizer_path=root / "recognizer.onnx",
        default_threshold=0.36,
        min_score=0.2,
        max_upload_bytes=1024 * 1024,
        min_face_size=20,
        gallery_max_side=1600,
        allowed_hosts=("localhost", "testserver"),
        access_code=None,
        event_name="Face Finder",
        search_rate_limit=1000,
    )
