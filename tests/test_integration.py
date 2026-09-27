"""End-to-end check with the real models and the real gallery photos.

Skipped automatically when the models or the photos are not available.
"""
import os
from pathlib import Path

import pytest

from app.faces import FaceEngine, decode_image, largest_face
from app.index import build_index, list_photos, search

MODELS_DIR = Path(os.environ.get("MODELS_DIR", "/models"))
PHOTOS_DIR = Path(os.environ.get("PHOTOS_DIR", "/photos"))
DETECTOR = MODELS_DIR / "face_detection_yunet_2023mar.onnx"
RECOGNIZER = MODELS_DIR / "face_recognition_sface_2021dec.onnx"
SAMPLE_SIZE = 25
GALLERY_MAX_SIDE = 1600
SELFIE_MAX_SIDE = 640
MIN_REFERENCE_FACE = 90
CROP_MARGIN = 0.6
THRESHOLD = 0.363

pytestmark = pytest.mark.skipif(
    not (DETECTOR.is_file() and RECOGNIZER.is_file() and PHOTOS_DIR.is_dir()),
    reason="models or photos not available",
)


def crop_around(image, box):
    x, y, width, height = box
    margin_x, margin_y = int(width * CROP_MARGIN), int(height * CROP_MARGIN)
    top, left = max(0, y - margin_y), max(0, x - margin_x)
    return image[top:y + height + margin_y, left:x + width + margin_x].copy()


def find_reference(engine, photos):
    for photo in photos:
        image = decode_image(photo.read_bytes())
        faces = engine.extract(image, GALLERY_MAX_SIDE)
        large = [face for face in faces if face.box[2] >= MIN_REFERENCE_FACE]
        if large:
            return photo, image, largest_face(tuple(large))
    return None


def test_face_crop_finds_its_source_photo():
    photos = list_photos(PHOTOS_DIR)[:SAMPLE_SIZE]
    if not photos:
        pytest.skip("no photos in the gallery folder")
    engine = FaceEngine(DETECTOR, RECOGNIZER, min_face_size=20)
    reference = find_reference(engine, photos)
    if reference is None:
        pytest.skip("no sufficiently large face in the sample")
    photo, image, face = reference

    selfie_faces = engine.extract(crop_around(image, face.box), SELFIE_MAX_SIDE)
    assert selfie_faces, "the face crop should contain a detectable face"

    index = build_index(photos, engine, "integration", GALLERY_MAX_SIDE)
    matches = search(index, largest_face(selfie_faces).embedding, THRESHOLD)
    assert matches, "the source photo should match"
    assert matches[0].filename == photo.name
    assert matches[0].score > 0.8
