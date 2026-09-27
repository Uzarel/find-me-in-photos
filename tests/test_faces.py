import cv2
import numpy as np
import pytest

from app.faces import (
    MAX_IMAGE_PIXELS,
    FaceEngine,
    FaceEngineError,
    ImageError,
    decode_image,
    largest_face,
    normalize,
    resize_to_fit,
)
from tests.helpers import encode_image, make_face


def test_decode_image_reads_valid_image():
    image = decode_image(encode_image(77))
    assert image.shape == (32, 32, 3)
    assert int(image[0, 0, 0]) == 77


@pytest.mark.parametrize("data", [b"", b"not an image", b"\xff\xd8\xff broken"])
def test_decode_image_rejects_invalid_data(data):
    with pytest.raises(ImageError):
        decode_image(data)


def test_decode_image_rejects_oversized_dimensions():
    side = int(MAX_IMAGE_PIXELS ** 0.5) + 100
    ok, buffer = cv2.imencode(".png", np.zeros((side, side), dtype=np.uint8))
    assert ok
    assert len(buffer) < 1024 * 1024  # tiny file, huge image
    with pytest.raises(ImageError):
        decode_image(buffer.tobytes())


def test_resize_to_fit_keeps_small_images():
    image = np.zeros((100, 200, 3), dtype=np.uint8)
    resized, scale = resize_to_fit(image, 640)
    assert resized is image
    assert scale == 1.0


def test_resize_to_fit_shrinks_large_images():
    image = np.zeros((1000, 2000, 3), dtype=np.uint8)
    resized, scale = resize_to_fit(image, 500)
    assert resized.shape[:2] == (250, 500)
    assert scale == pytest.approx(0.25)


def test_resize_to_fit_rejects_invalid_side():
    with pytest.raises(ValueError):
        resize_to_fit(np.zeros((10, 10, 3), dtype=np.uint8), 0)


def test_normalize_returns_unit_vector_without_mutating_input():
    vector = np.array([[3.0, 4.0]], dtype=np.float32)
    result = normalize(vector)
    assert result.tolist() == pytest.approx([0.6, 0.8])
    assert vector.tolist() == [[3.0, 4.0]]


def test_normalize_rejects_zero_vector():
    with pytest.raises(ValueError):
        normalize(np.zeros(4))


def test_largest_face_picks_biggest_box():
    small = make_face([1, 0], box=(0, 0, 10, 10))
    big = make_face([0, 1], box=(0, 0, 40, 40))
    assert largest_face((small, big)) is big


def test_largest_face_requires_faces():
    with pytest.raises(ValueError):
        largest_face(())


def test_engine_reports_missing_models(tmp_path):
    with pytest.raises(FaceEngineError):
        FaceEngine(tmp_path / "missing.onnx", tmp_path / "missing2.onnx", 20)
