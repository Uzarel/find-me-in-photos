import shutil

from app.service import STATE_ERROR, STATE_INDEXING, STATE_READY, IndexService
from tests.helpers import FakeExtractor, make_face, make_settings, write_image


def make_extractor():
    return FakeExtractor({10: (make_face([1, 0, 0, 0]),)})


def test_starts_in_indexing_state(tmp_path):
    service = IndexService(make_settings(tmp_path), make_extractor())
    status = service.status()
    assert status.state == STATE_INDEXING
    assert status.index is None


def test_run_builds_index_and_writes_cache(tmp_path):
    settings = make_settings(tmp_path)
    write_image(settings.photos_dir / "a.png", 10)
    write_image(settings.photos_dir / "b.png", 30)
    service = IndexService(settings, make_extractor())
    service.run()
    status = service.status()
    assert status.state == STATE_READY
    assert (status.processed, status.total) == (2, 2)
    assert status.index.filenames == ("a.png", "b.png")
    assert list(settings.data_dir.glob("*.npz"))


def test_second_run_uses_cache(tmp_path):
    settings = make_settings(tmp_path)
    write_image(settings.photos_dir / "a.png", 10)
    IndexService(settings, make_extractor()).run()
    extractor = make_extractor()
    service = IndexService(settings, extractor)
    service.run()
    assert service.status().state == STATE_READY
    assert extractor.calls == 0


def test_changed_photos_trigger_rebuild(tmp_path):
    settings = make_settings(tmp_path)
    write_image(settings.photos_dir / "a.png", 10)
    IndexService(settings, make_extractor()).run()
    write_image(settings.photos_dir / "b.png", 10)
    extractor = make_extractor()
    service = IndexService(settings, extractor)
    service.run()
    assert extractor.calls == 2
    assert service.status().index.filenames == ("a.png", "b.png")


def test_missing_photos_directory_is_reported(tmp_path):
    settings = make_settings(tmp_path)
    shutil.rmtree(settings.photos_dir)
    service = IndexService(settings, make_extractor())
    service.run()
    status = service.status()
    assert status.state == STATE_ERROR
    assert "photos" in status.error.lower()


def test_empty_photos_directory_is_reported(tmp_path):
    service = IndexService(make_settings(tmp_path), make_extractor())
    service.run()
    assert service.status().state == STATE_ERROR


def test_start_runs_in_background(tmp_path):
    settings = make_settings(tmp_path)
    write_image(settings.photos_dir / "a.png", 10)
    service = IndexService(settings, make_extractor())
    service.start()
    service.join(timeout=10)
    assert service.status().state == STATE_READY
