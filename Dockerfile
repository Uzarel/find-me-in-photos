# syntax=docker/dockerfile:1.7
FROM python:3.12-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    OPENCV_IO_MAX_IMAGE_PIXELS=40000000

WORKDIR /srv

COPY requirements.txt .
RUN pip install -r requirements.txt

# Face models from Hugging Face (OpenCV Zoo), pinned by revision and checksum.
# YuNet detector: MIT licence. SFace recogniser: Apache 2.0 licence.
ADD --checksum=sha256:8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4 \
    https://huggingface.co/opencv/face_detection_yunet/resolve/3cc26e7f1014a5ee5d74a42acee58bafc9d0a310/face_detection_yunet_2023mar.onnx \
    /models/face_detection_yunet_2023mar.onnx
ADD --checksum=sha256:0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79 \
    https://huggingface.co/opencv/face_recognition_sface/resolve/3d7082438a6e4551e840c9b2bb60b71e8da4b524/face_recognition_sface_2021dec.onnx \
    /models/face_recognition_sface_2021dec.onnx

RUN useradd --create-home --uid 1000 app \
    && mkdir -p /data \
    && chown app:app /data \
    && chmod 755 /models \
    && chmod 644 /models/*

COPY app ./app


FROM base AS test

COPY requirements-dev.txt .
RUN pip install -r requirements-dev.txt
COPY tests ./tests
ENV COVERAGE_FILE=/tmp/.coverage
USER app
CMD ["python", "-m", "pytest", "tests", "-p", "no:cacheprovider", \
     "--cov=app", "--cov-report=term-missing"]


FROM base AS runtime

USER app
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=3)"]
CMD ["uvicorn", "app.main:create_default_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]
