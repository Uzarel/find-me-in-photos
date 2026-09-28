"""Web app: take a selfie, get every gallery photo you appear in."""
from __future__ import annotations

import logging
import tempfile
import zipfile
from collections.abc import Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import IO

from fastapi import APIRouter, FastAPI, File, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.config import Settings, load_settings
from app.faces import (
    Face,
    FaceEngine,
    FaceEngineError,
    FaceExtractor,
    ImageError,
    decode_image,
    largest_face,
)
from app.access import RateLimiter
from app.index import FaceIndex, search
from app.responses import ApiError, envelope, error_response
from app.security import AccessMiddleware, BodyLimitMiddleware, SecurityHeadersMiddleware
from app.service import STATE_ERROR, IndexService
from app.session import create_login_limiter
from app.session import router as session_router

STATIC_DIR = Path(__file__).resolve().parent / "static"
# YuNet works best on moderately sized faces, so selfies are tried small first.
SELFIE_MAX_SIDES = (640, 320, 1280)
MAX_DOWNLOAD_FILES = 2000
ZIP_FILENAME = "my-photos.zip"
# Archives larger than this are built on disk instead of in memory.
ZIP_SPOOL_BYTES = 32 * 1024 * 1024
ZIP_CHUNK_BYTES = 1024 * 1024
MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024 * 1024
# Room for the multipart boundaries and headers around an upload.
REQUEST_OVERHEAD_BYTES = 64 * 1024
SCORE_DECIMALS = 4
RATE_WINDOW_SECONDS = 60
DOWNLOADS_PER_WINDOW = 10

logger = logging.getLogger(__name__)
router = APIRouter()


class DownloadRequest(BaseModel):
    filenames: list[str] = Field(min_length=1, max_length=MAX_DOWNLOAD_FILES)


def is_servable(index: FaceIndex, photos_dir: Path, filename: str) -> bool:
    """Only photos that were indexed and decoded, and never through a symlink."""
    if filename not in index.filenames or filename in index.failed:
        return False
    return not (photos_dir / filename).is_symlink()


def require_index(request: Request) -> FaceIndex:
    status = request.app.state.service.status()
    if status.state == STATE_ERROR:
        raise ApiError(503, f"The gallery could not be indexed: {status.error}")
    if status.index is None:
        raise ApiError(503, "The gallery is still being indexed, try again shortly.")
    return status.index


def find_selfie_faces(extractor: FaceExtractor, image) -> tuple[Face, ...]:
    for max_side in SELFIE_MAX_SIDES:
        faces = extractor.extract(image, max_side)
        if faces:
            return faces
    return ()


def read_upload(upload: UploadFile, max_bytes: int) -> bytes:
    data = upload.file.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ApiError(413, f"The image is larger than {max_bytes // (1024 * 1024)} MB.")
    return data


def build_archive(photos_dir: Path, names: tuple[str, ...]) -> tuple[IO[bytes], int]:
    """Write the photos into a ZIP that spills to disk when large."""
    total = sum((photos_dir / name).stat().st_size for name in names)
    if total > MAX_DOWNLOAD_BYTES:
        raise ApiError(413, "The selection is too large to download at once.")
    spool = tempfile.SpooledTemporaryFile(max_size=ZIP_SPOOL_BYTES)
    try:
        with zipfile.ZipFile(spool, "w", zipfile.ZIP_STORED) as archive:
            for name in names:
                archive.write(photos_dir / name, arcname=name)
        size = spool.tell()
        spool.seek(0)
    except BaseException:
        spool.close()
        raise
    return spool, size


def iter_chunks(handle: IO[bytes]) -> Iterator[bytes]:
    try:
        while chunk := handle.read(ZIP_CHUNK_BYTES):
            yield chunk
    finally:
        handle.close()


@router.get("/")
def home() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@router.get("/api/status")
def get_status(request: Request) -> dict:
    settings: Settings = request.app.state.settings
    status = request.app.state.service.status()
    index = status.index
    return envelope({
        "state": status.state,
        "processed": status.processed,
        "total": status.total,
        "photos": len(index.filenames) if index else 0,
        "faces": int(index.embeddings.shape[0]) if index else 0,
        "unreadable": len(index.failed) if index else 0,
        "default_threshold": settings.default_threshold,
        "min_score": settings.min_score,
        "error": status.error or None,
    })


@router.post("/api/search")
def search_photos(request: Request, selfie: UploadFile = File(...)) -> dict:
    settings: Settings = request.app.state.settings
    index = require_index(request)
    data = read_upload(selfie, settings.max_upload_bytes)
    try:
        faces = find_selfie_faces(request.app.state.extractor, decode_image(data))
    except ImageError as error:
        raise ApiError(400, str(error)) from error
    except FaceEngineError as error:
        logger.exception("Face extraction failed on the selfie")
        raise ApiError(500, "Could not analyse the image.") from error
    if not faces:
        raise ApiError(422, "No face found. Use a well-lit photo with your face in view.")
    matches = search(index, largest_face(faces).embedding, settings.min_score)
    return envelope({
        "faces_in_selfie": len(faces),
        "matches": [
            {
                "filename": match.filename,
                "score": round(match.score, SCORE_DECIMALS),
                "url": f"/photos/{match.filename}",
                "box": list(match.box),
            }
            for match in matches
        ],
    })


@router.get("/photos/{filename}")
def get_photo(request: Request, filename: str) -> FileResponse:
    index = require_index(request)
    photos_dir: Path = request.app.state.settings.photos_dir
    if not is_servable(index, photos_dir, filename):
        raise ApiError(404, "Photo not found.")
    return FileResponse(photos_dir / filename)


@router.post("/api/download")
def download_photos(request: Request, payload: DownloadRequest) -> StreamingResponse:
    index = require_index(request)
    photos_dir: Path = request.app.state.settings.photos_dir
    requested = tuple(dict.fromkeys(payload.filenames))
    unknown = [name for name in requested if not is_servable(index, photos_dir, name)]
    if unknown:
        raise ApiError(400, f"{len(unknown)} requested photos are not in the gallery.")
    try:
        archive, size = build_archive(photos_dir, requested)
    except OSError as error:
        logger.exception("Could not build the archive")
        raise ApiError(500, "Could not read the photos from disk.") from error
    headers = {
        "Content-Disposition": f'attachment; filename="{ZIP_FILENAME}"',
        "Content-Length": str(size),
    }
    return StreamingResponse(iter_chunks(archive), media_type="application/zip",
                             headers=headers)


@router.get("/api/health")
def get_health(request: Request) -> dict:
    """For the container healthcheck: fails when the gallery could not be indexed."""
    status = request.app.state.service.status()
    if status.state == STATE_ERROR:
        raise ApiError(503, f"The gallery could not be indexed: {status.error}")
    return envelope({"state": status.state})


async def handle_api_error(_: Request, error: ApiError) -> JSONResponse:
    return error_response(error.status_code, error.message)


async def handle_http_error(_: Request, error: StarletteHTTPException) -> JSONResponse:
    return error_response(error.status_code, str(error.detail))


async def handle_validation_error(_: Request, error: RequestValidationError) -> JSONResponse:
    fields = sorted({str(item["loc"][-1]) for item in error.errors()})
    return error_response(422, f"Invalid request: check {', '.join(fields)}.")


async def handle_unexpected_error(request: Request, error: Exception) -> JSONResponse:
    logger.error("Unexpected error on %s %s", request.method, request.url.path,
                 exc_info=error)
    return error_response(500, "An unexpected error occurred.")


def create_app(settings: Settings, extractor: FaceExtractor,
               autostart: bool = True) -> FastAPI:
    service = IndexService(settings, extractor)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        if autostart:
            service.start()
        yield

    app = FastAPI(title="Face Finder", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.extractor = extractor
    app.state.service = service
    app.state.login_limiter = create_login_limiter()
    limiters = {
        "/api/search": RateLimiter(settings.search_rate_limit, RATE_WINDOW_SECONDS),
        "/api/download": RateLimiter(DOWNLOADS_PER_WINDOW, RATE_WINDOW_SECONDS),
    }
    app.add_exception_handler(ApiError, handle_api_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
    # Added innermost first: the security headers end up on every response.
    app.add_middleware(AccessMiddleware, access_code=settings.access_code,
                       limiters=limiters)
    app.add_middleware(BodyLimitMiddleware,
                       max_bytes=settings.max_upload_bytes + REQUEST_OVERHEAD_BYTES)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.allowed_hosts))
    app.add_middleware(SecurityHeadersMiddleware)
    app.include_router(router)
    app.include_router(session_router)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
    return app


def create_default_app() -> FastAPI:
    """Entry point for uvicorn: real settings and real models."""
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    settings = load_settings()
    engine = FaceEngine(settings.detector_path, settings.recognizer_path,
                        settings.min_face_size)
    return create_app(settings, engine)
