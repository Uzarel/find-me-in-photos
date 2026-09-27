"""Background indexing of the gallery and access to its current status."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass

from app.config import Settings
from app.faces import FaceExtractor
from app.index import (
    FaceIndex,
    build_index,
    compute_fingerprint,
    list_photos,
    load_index,
    save_index,
)

STATE_INDEXING = "indexing"
STATE_READY = "ready"
STATE_ERROR = "error"
INDEX_FILENAME = "face_index.npz"
INDEX_VERSION = 1

logger = logging.getLogger(__name__)


class IndexingError(Exception):
    """Raised when the gallery cannot be indexed."""


@dataclass(frozen=True)
class IndexStatus:
    state: str
    processed: int = 0
    total: int = 0
    index: FaceIndex | None = None
    error: str = ""


class IndexService:
    """Owns the face index; status snapshots are immutable and swapped atomically."""

    def __init__(self, settings: Settings, extractor: FaceExtractor):
        self._settings = settings
        self._extractor = extractor
        self._lock = threading.Lock()
        self._status = IndexStatus(state=STATE_INDEXING)
        self._thread: threading.Thread | None = None

    def status(self) -> IndexStatus:
        with self._lock:
            return self._status

    def start(self) -> None:
        self._thread = threading.Thread(target=self.run, name="indexer", daemon=True)
        self._thread.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def run(self) -> None:
        """Load or build the index; failures end up in the status, not raised."""
        try:
            index = self._load_or_build()
        except Exception as error:  # top of a background thread: report, don't die
            logger.exception("Indexing failed")
            self._publish(IndexStatus(state=STATE_ERROR, error=str(error)))
            return
        total = len(index.filenames)
        self._publish(IndexStatus(state=STATE_READY, processed=total, total=total,
                                  index=index))
        logger.info("Index ready: %d photos, %d faces, %d unreadable",
                    total, index.embeddings.shape[0], len(index.failed))

    def _publish(self, status: IndexStatus) -> None:
        with self._lock:
            self._status = status

    def _report_progress(self, processed: int, total: int) -> None:
        self._publish(IndexStatus(state=STATE_INDEXING, processed=processed, total=total))

    def _fingerprint_salt(self) -> str:
        settings = self._settings
        return (f"v{INDEX_VERSION}|{settings.min_face_size}|{settings.gallery_max_side}|"
                f"{settings.detector_path.name}|{settings.recognizer_path.name}")

    def _load_or_build(self) -> FaceIndex:
        settings = self._settings
        try:
            photos = list_photos(settings.photos_dir)
        except OSError as error:
            raise IndexingError(f"Cannot read the photos folder: {error}") from error
        if not photos:
            raise IndexingError(f"No photos found in {settings.photos_dir}")

        fingerprint = compute_fingerprint(photos, self._fingerprint_salt())
        cache_path = settings.data_dir / INDEX_FILENAME
        cached = load_index(cache_path, fingerprint)
        if cached is not None:
            logger.info("Loaded cached index from %s", cache_path)
            return cached

        logger.info("Indexing %d photos", len(photos))
        self._report_progress(0, len(photos))
        index = build_index(photos, self._extractor, fingerprint,
                            settings.gallery_max_side, self._report_progress)
        try:
            save_index(index, cache_path)
        except OSError as error:
            logger.warning("Could not save the index cache: %s", error)
        return index
