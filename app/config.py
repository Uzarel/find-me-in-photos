"""Runtime settings, read once from environment variables."""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

DETECTOR_FILENAME = "face_detection_yunet_2023mar.onnx"
RECOGNIZER_FILENAME = "face_recognition_sface_2021dec.onnx"
BYTES_PER_MB = 1024 * 1024

DEFAULTS = {
    "PHOTOS_DIR": "/photos",
    "DATA_DIR": "/data",
    "MODELS_DIR": "/models",
    # 0.363 is the cosine threshold recommended for SFace by its authors.
    "MATCH_THRESHOLD": "0.363",
    "MIN_SCORE": "0.2",
    "MAX_UPLOAD_MB": "10",
    "MIN_FACE_SIZE": "20",
    "GALLERY_MAX_SIDE": "1600",
    # Host names the app answers to; anything else is refused (DNS rebinding).
    "ALLOWED_HOSTS": "localhost,127.0.0.1",
}
WILDCARD_HOST = "*"


class ConfigError(Exception):
    """Raised when an environment variable holds an invalid value."""


@dataclass(frozen=True)
class Settings:
    photos_dir: Path
    data_dir: Path
    detector_path: Path
    recognizer_path: Path
    default_threshold: float
    min_score: float
    max_upload_bytes: int
    min_face_size: int
    gallery_max_side: int
    allowed_hosts: tuple[str, ...]


def _read(env: Mapping[str, str], name: str) -> str:
    return env.get(name, DEFAULTS[name]).strip()


def _read_number(env: Mapping[str, str], name: str, kind: type):
    raw = _read(env, name)
    try:
        return kind(raw)
    except ValueError as error:
        raise ConfigError(f"{name} must be a valid {kind.__name__}, got {raw!r}") from error


def _read_positive_int(env: Mapping[str, str], name: str) -> int:
    value = _read_number(env, name, int)
    if value <= 0:
        raise ConfigError(f"{name} must be greater than zero, got {value}")
    return value


def _read_scores(env: Mapping[str, str]) -> tuple[float, float]:
    min_score = _read_number(env, "MIN_SCORE", float)
    threshold = _read_number(env, "MATCH_THRESHOLD", float)
    if not 0 < min_score <= threshold < 1:
        raise ConfigError(
            "Scores must satisfy 0 < MIN_SCORE <= MATCH_THRESHOLD < 1, "
            f"got MIN_SCORE={min_score} and MATCH_THRESHOLD={threshold}"
        )
    return min_score, threshold


def _read_hosts(env: Mapping[str, str]) -> tuple[str, ...]:
    hosts = tuple(host.strip() for host in _read(env, "ALLOWED_HOSTS").split(",")
                  if host.strip())
    if not hosts:
        raise ConfigError("ALLOWED_HOSTS must name at least one host")
    if WILDCARD_HOST in hosts:
        raise ConfigError("ALLOWED_HOSTS must not contain the wildcard '*'")
    return hosts


def load_settings(env: Mapping[str, str] = os.environ) -> Settings:
    min_score, threshold = _read_scores(env)
    models_dir = Path(_read(env, "MODELS_DIR"))
    return Settings(
        photos_dir=Path(_read(env, "PHOTOS_DIR")),
        data_dir=Path(_read(env, "DATA_DIR")),
        detector_path=models_dir / DETECTOR_FILENAME,
        recognizer_path=models_dir / RECOGNIZER_FILENAME,
        default_threshold=threshold,
        min_score=min_score,
        max_upload_bytes=_read_positive_int(env, "MAX_UPLOAD_MB") * BYTES_PER_MB,
        min_face_size=_read_positive_int(env, "MIN_FACE_SIZE"),
        gallery_max_side=_read_positive_int(env, "GALLERY_MAX_SIDE"),
        allowed_hosts=_read_hosts(env),
    )
