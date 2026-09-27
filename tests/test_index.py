import numpy as np
import pytest

from app.index import (
    build_index,
    compute_fingerprint,
    list_photos,
    load_index,
    save_index,
    search,
)
from tests.helpers import FakeExtractor, make_face, unit, write_image

MAX_SIDE = 1600


@pytest.fixture
def photos(tmp_path):
    write_image(tmp_path / "b.png", 20)
    write_image(tmp_path / "a.png", 10)
    write_image(tmp_path / "c.png", 30)
    return list_photos(tmp_path)


@pytest.fixture
def extractor():
    return FakeExtractor({
        10: (make_face([1, 0, 0, 0], box=(1, 2, 30, 40)),),
        20: (make_face([0, 1, 0, 0]), make_face([0.9, 0.1, 0, 0], box=(5, 6, 7, 8))),
    })


def test_list_photos_sorts_and_filters(tmp_path):
    write_image(tmp_path / "b.png", 1)
    write_image(tmp_path / "A.JPG", 1)
    (tmp_path / "notes.txt").write_text("x")
    (tmp_path / "b.png.part").write_text("x")
    (tmp_path / "folder.jpg").mkdir()
    assert [p.name for p in list_photos(tmp_path)] == ["A.JPG", "b.png"]


def test_list_photos_skips_symlinks(tmp_path):
    gallery = tmp_path / "gallery"
    gallery.mkdir()
    write_image(gallery / "a.png", 1)
    outside = write_image(tmp_path / "outside.png", 1)
    (gallery / "link.png").symlink_to(outside)
    assert [p.name for p in list_photos(gallery)] == ["a.png"]


def test_list_photos_missing_directory_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        list_photos(tmp_path / "nope")


def test_fingerprint_changes_with_files_and_salt(tmp_path, photos):
    original = compute_fingerprint(photos, "v1")
    assert compute_fingerprint(photos, "v1") == original
    assert compute_fingerprint(photos, "v2") != original
    write_image(tmp_path / "d.png", 40)
    assert compute_fingerprint(list_photos(tmp_path), "v1") != original


def test_build_index_collects_faces_and_reports_progress(photos, extractor):
    progress = []
    index = build_index(photos, extractor, "fp", MAX_SIDE,
                        on_progress=lambda done, total: progress.append((done, total)))
    assert index.filenames == ("a.png", "b.png", "c.png")
    assert index.embeddings.shape == (3, 4)
    assert index.face_photo_ids.tolist() == [0, 1, 1]
    assert index.boxes[0].tolist() == [1, 2, 30, 40]
    assert index.failed == ()
    assert progress == [(1, 3), (2, 3), (3, 3)]


def test_build_index_records_unreadable_photos(tmp_path, extractor):
    write_image(tmp_path / "a.png", 10)
    (tmp_path / "broken.jpg").write_bytes(b"not an image")
    index = build_index(list_photos(tmp_path), extractor, "fp", MAX_SIDE)
    assert index.filenames == ("a.png", "broken.jpg")
    assert index.failed == ("broken.jpg",)
    assert index.embeddings.shape == (1, 4)


def test_build_index_without_faces(tmp_path):
    write_image(tmp_path / "c.png", 30)
    index = build_index(list_photos(tmp_path), FakeExtractor({}), "fp", MAX_SIDE)
    assert index.embeddings.shape[0] == 0
    assert search(index, unit([1, 0, 0, 0]), 0.2) == ()


def test_save_and_load_roundtrip(tmp_path, photos, extractor):
    index = build_index(photos, extractor, "fp", MAX_SIDE)
    path = tmp_path / "cache" / "index.npz"
    save_index(index, path)
    loaded = load_index(path, "fp")
    assert loaded.filenames == index.filenames
    assert loaded.failed == index.failed
    assert np.array_equal(loaded.embeddings, index.embeddings)
    assert np.array_equal(loaded.face_photo_ids, index.face_photo_ids)
    assert np.array_equal(loaded.boxes, index.boxes)


def test_index_arrays_are_read_only(tmp_path, photos, extractor):
    built = build_index(photos, extractor, "fp", MAX_SIDE)
    path = tmp_path / "index.npz"
    save_index(built, path)
    for index in (built, load_index(path, "fp")):
        for array in (index.embeddings, index.boxes, index.face_photo_ids):
            assert not array.flags.writeable
            with pytest.raises(ValueError):
                array[...] = 0


def test_load_index_rejects_stale_missing_or_corrupt_cache(tmp_path, photos, extractor):
    path = tmp_path / "index.npz"
    assert load_index(path, "fp") is None
    save_index(build_index(photos, extractor, "fp", MAX_SIDE), path)
    assert load_index(path, "other") is None
    path.write_bytes(b"garbage")
    assert load_index(path, "fp") is None


def test_search_returns_best_face_per_photo_sorted(photos, extractor):
    index = build_index(photos, extractor, "fp", MAX_SIDE)
    matches = search(index, unit([1, 0, 0, 0]), 0.2)
    assert [m.filename for m in matches] == ["a.png", "b.png"]
    assert matches[0].score == pytest.approx(1.0)
    assert matches[0].box == (1, 2, 30, 40)
    assert matches[1].score == pytest.approx(0.9939, abs=1e-3)
    assert matches[1].box == (5, 6, 7, 8)


def test_search_applies_minimum_score(photos, extractor):
    index = build_index(photos, extractor, "fp", MAX_SIDE)
    matches = search(index, unit([1, 0, 0, 0]), 0.999)
    assert [m.filename for m in matches] == ["a.png"]


def test_search_rejects_wrong_embedding_size(photos, extractor):
    index = build_index(photos, extractor, "fp", MAX_SIDE)
    with pytest.raises(ValueError):
        search(index, unit([1, 0]), 0.2)
