"""Build, cache and search the index of faces found in the gallery photos."""
from __future__ import annotations

import hashlib
import logging
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from app.faces import Face, FaceEngineError, FaceExtractor, ImageError, decode_image

PHOTO_EXTENSIONS = frozenset({".jpg", ".jpeg", ".png"})
BOX_COLUMNS = 4
TEMP_SUFFIX = ".tmp.npz"

logger = logging.getLogger(__name__)

ProgressCallback = Callable[[int, int], None]


@dataclass(frozen=True, eq=False)
class FaceIndex:
    fingerprint: str
    filenames: tuple[str, ...]  # every photo that was scanned
    failed: tuple[str, ...]  # photos that could not be read
    face_photo_ids: np.ndarray  # (faces,) position of each face's photo in filenames
    embeddings: np.ndarray  # (faces, dimensions), L2-normalised
    boxes: np.ndarray  # (faces, 4) x, y, width, height


@dataclass(frozen=True)
class Match:
    filename: str
    score: float
    box: tuple[int, int, int, int]


def _read_only(array: np.ndarray, dtype: type) -> np.ndarray:
    """Return a write-protected copy: the index is shared between threads."""
    frozen = np.array(array, dtype=dtype)
    frozen.setflags(write=False)
    return frozen


def list_photos(photos_dir: Path) -> tuple[Path, ...]:
    if not photos_dir.is_dir():
        raise FileNotFoundError(f"Photos folder not found: {photos_dir}")
    return tuple(sorted(
        (path for path in photos_dir.iterdir()
         if path.suffix.lower() in PHOTO_EXTENSIONS
         and not path.is_symlink() and path.is_file()),
        key=lambda path: path.name,
    ))


def compute_fingerprint(photos: tuple[Path, ...], salt: str) -> str:
    """Hash of the photo set, used to detect a stale cache."""
    digest = hashlib.sha256(salt.encode("utf-8"))
    for photo in photos:
        stat = photo.stat()
        digest.update(f"\n{photo.name}|{stat.st_size}|{stat.st_mtime_ns}".encode("utf-8"))
    return digest.hexdigest()


def _extract_faces(photo: Path, extractor: FaceExtractor,
                   max_side: int) -> tuple[Face, ...] | None:
    """Return the faces in a photo, or None when the photo cannot be processed."""
    try:
        return extractor.extract(decode_image(photo.read_bytes()), max_side)
    except (ImageError, FaceEngineError, OSError) as error:
        logger.warning("Skipping %s: %s", photo.name, error)
        return None


def _scan(photos: tuple[Path, ...], extractor: FaceExtractor, max_side: int,
          on_progress: ProgressCallback | None):
    for position, photo in enumerate(photos, start=1):
        yield _extract_faces(photo, extractor, max_side)
        if on_progress is not None:
            on_progress(position, len(photos))


def build_index(photos: tuple[Path, ...], extractor: FaceExtractor, fingerprint: str,
                max_side: int, on_progress: ProgressCallback | None = None) -> FaceIndex:
    scanned = tuple(_scan(photos, extractor, max_side, on_progress))
    found = tuple(
        (photo_id, face)
        for photo_id, faces in enumerate(scanned)
        for face in faces or ()
    )
    embeddings = (
        np.stack([face.embedding for _, face in found])
        if found else np.zeros((0, 0))
    )
    boxes = np.array([face.box for _, face in found]).reshape(-1, BOX_COLUMNS)
    return FaceIndex(
        fingerprint=fingerprint,
        filenames=tuple(photo.name for photo in photos),
        failed=tuple(p.name for p, faces in zip(photos, scanned) if faces is None),
        face_photo_ids=_read_only([photo_id for photo_id, _ in found], np.int32),
        embeddings=_read_only(embeddings, np.float32),
        boxes=_read_only(boxes, np.int32),
    )


def save_index(index: FaceIndex, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.stem + TEMP_SUFFIX)
    try:
        np.savez(
            temporary,
            fingerprint=np.array(index.fingerprint),
            filenames=np.array(index.filenames, dtype=str),
            failed=np.array(index.failed, dtype=str),
            face_photo_ids=index.face_photo_ids,
            embeddings=index.embeddings,
            boxes=index.boxes,
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_index(path: Path, fingerprint: str) -> FaceIndex | None:
    """Load a cached index, or None when it is missing, stale or unreadable."""
    if not path.is_file():
        return None
    try:
        with np.load(path, allow_pickle=False) as data:
            if str(data["fingerprint"]) != fingerprint:
                logger.info("Cached index is stale, rebuilding.")
                return None
            return FaceIndex(
                fingerprint=fingerprint,
                filenames=tuple(str(name) for name in data["filenames"]),
                failed=tuple(str(name) for name in data["failed"]),
                face_photo_ids=_read_only(data["face_photo_ids"], np.int32),
                embeddings=_read_only(data["embeddings"], np.float32),
                boxes=_read_only(data["boxes"], np.int32),
            )
    except (OSError, ValueError, KeyError, zipfile.BadZipFile) as error:
        logger.warning("Ignoring unreadable index cache %s: %s", path, error)
        return None


def search(index: FaceIndex, embedding: np.ndarray, min_score: float) -> tuple[Match, ...]:
    """Return the best-matching face of each photo, highest score first."""
    if index.embeddings.shape[0] == 0:
        return ()
    if embedding.shape != (index.embeddings.shape[1],):
        raise ValueError(
            f"Expected an embedding of size {index.embeddings.shape[1]}, "
            f"got shape {embedding.shape}"
        )
    scores = index.embeddings @ embedding.astype(np.float32)
    ranked = [int(i) for i in np.argsort(-scores, kind="stable") if scores[i] >= min_score]
    best_per_photo = {}
    for face_id in ranked:
        best_per_photo.setdefault(int(index.face_photo_ids[face_id]), face_id)
    return tuple(
        Match(
            filename=index.filenames[photo_id],
            score=float(scores[face_id]),
            box=tuple(int(value) for value in index.boxes[face_id]),
        )
        for photo_id, face_id in best_per_photo.items()
    )
