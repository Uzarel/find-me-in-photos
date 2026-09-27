"""Face detection (YuNet) and face embeddings (SFace) on top of OpenCV."""
from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

MAX_IMAGE_PIXELS = 40_000_000
# OpenCV reads this when it is first imported. It makes imdecode refuse an
# oversized image before allocating memory for it (decompression bombs).
os.environ.setdefault("OPENCV_IO_MAX_IMAGE_PIXELS", str(MAX_IMAGE_PIXELS))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

DETECTION_CONFIDENCE = 0.7
NMS_THRESHOLD = 0.3
DETECTION_TOP_K = 5000
INITIAL_INPUT_SIZE = (320, 320)
WIDTH_COLUMN = 2
HEIGHT_COLUMN = 3
BOX_COLUMNS = 4
CONFIDENCE_COLUMN = 14


class ImageError(Exception):
    """Raised when bytes cannot be decoded into an image."""


class FaceEngineError(Exception):
    """Raised when the face models cannot be loaded or run."""


@dataclass(frozen=True, eq=False)
class Face:
    box: tuple[int, int, int, int]  # x, y, width, height in source pixels
    confidence: float
    embedding: np.ndarray  # L2-normalised

    @property
    def area(self) -> int:
        return self.box[2] * self.box[3]


class FaceExtractor(Protocol):
    def extract(self, image: np.ndarray, max_side: int) -> tuple[Face, ...]:
        """Detect the faces in an image and compute their embeddings."""


def decode_image(data: bytes) -> np.ndarray:
    if not data:
        raise ImageError("The image is empty.")
    try:
        image = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    except cv2.error as error:
        raise ImageError("The file is not a readable image.") from error
    if image is None:
        raise ImageError("The file is not a readable image.")
    if image.shape[0] * image.shape[1] > MAX_IMAGE_PIXELS:
        raise ImageError("The image dimensions are too large.")
    return image


def resize_to_fit(image: np.ndarray, max_side: int) -> tuple[np.ndarray, float]:
    """Shrink an image so its longest side fits max_side; never enlarges."""
    if max_side <= 0:
        raise ValueError(f"max_side must be positive, got {max_side}")
    height, width = image.shape[:2]
    longest = max(height, width)
    if longest <= max_side:
        return image, 1.0
    scale = max_side / longest
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    return cv2.resize(image, size, interpolation=cv2.INTER_AREA), scale


def normalize(vector: np.ndarray) -> np.ndarray:
    flat = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(flat))
    if norm == 0:
        raise ValueError("Cannot normalise a zero vector.")
    return flat / norm


def largest_face(faces: tuple[Face, ...]) -> Face:
    if not faces:
        raise ValueError("No faces to choose from.")
    return max(faces, key=lambda face: face.area)


class FaceEngine:
    """Thread-safe wrapper around the YuNet detector and SFace recogniser."""

    def __init__(self, detector_path: Path, recognizer_path: Path, min_face_size: int):
        for path in (detector_path, recognizer_path):
            if not path.is_file():
                raise FaceEngineError(f"Model file not found: {path}")
        try:
            self._detector = cv2.FaceDetectorYN.create(
                str(detector_path), "", INITIAL_INPUT_SIZE,
                DETECTION_CONFIDENCE, NMS_THRESHOLD, DETECTION_TOP_K,
            )
            self._recognizer = cv2.FaceRecognizerSF.create(str(recognizer_path), "")
        except cv2.error as error:
            raise FaceEngineError(f"Could not load the face models: {error}") from error
        self._min_face_size = min_face_size
        self._lock = threading.Lock()

    def extract(self, image: np.ndarray, max_side: int) -> tuple[Face, ...]:
        scaled, scale = resize_to_fit(image, max_side)
        height, width = scaled.shape[:2]
        try:
            with self._lock:
                self._detector.setInputSize((width, height))
                _, rows = self._detector.detect(scaled)
                if rows is None:
                    return ()
                return tuple(
                    self._describe(scaled, row, scale)
                    for row in rows if self._is_large_enough(row)
                )
        except cv2.error as error:
            raise FaceEngineError(f"Face extraction failed: {error}") from error

    def _is_large_enough(self, row: np.ndarray) -> bool:
        return min(row[WIDTH_COLUMN], row[HEIGHT_COLUMN]) >= self._min_face_size

    def _describe(self, scaled: np.ndarray, row: np.ndarray, scale: float) -> Face:
        aligned = self._recognizer.alignCrop(scaled, row)
        embedding = normalize(self._recognizer.feature(aligned))
        embedding.setflags(write=False)
        x, y, width, height = (int(round(value / scale)) for value in row[:BOX_COLUMNS])
        return Face(
            box=(x, y, width, height),
            confidence=float(row[CONFIDENCE_COLUMN]),
            embedding=embedding,
        )
