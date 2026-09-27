# Face Finder

Take a selfie, get every gallery photo you appear in. Runs entirely on your
computer: the selfie is analysed in memory and never stored or sent anywhere.

## Run

```
docker compose up -d --build
```

Then open http://localhost:8000. The first start indexes the gallery (about
10 seconds for 225 photos); later starts reuse the cached index.

```
docker compose logs -f face-finder   # follow the logs
docker compose down                  # stop
docker compose down -v               # stop and delete the cached index
```

## Settings

Set these as environment variables, or in a `.env` file next to
`docker-compose.yml`.

| Variable | Default | Meaning |
|---|---|---|
| `PHOTOS_DIR` | `./photos` | Folder with the gallery photos (`.jpg`, `.jpeg`, `.png`) |
| `PORT` | `8000` | Local port |
| `MATCH_THRESHOLD` | `0.363` | Starting position of the strictness slider |

The app only answers to the host names `localhost` and `127.0.0.1`. To open it
under another name, add `ALLOWED_HOSTS` to the `environment` section of
`docker-compose.yml` as a comma-separated list.

Adding or removing photos is picked up on the next restart:
`docker compose restart face-finder`.

## How it works

1. **Detect**: YuNet finds the faces in every photo.
2. **Embed**: SFace turns each face into a 128-number fingerprint.
3. **Match**: the selfie's fingerprint is compared with every face by cosine
   similarity; each photo is scored by its best-matching face.

Scores run from 0 to 1. The slider starts at 0.363, the threshold recommended
by the SFace authors. Matches close to the threshold deserve a second look:
small, blurred or side-on faces score lower, and relatives can score
surprisingly high.

Both models come from the OpenCV Zoo on Hugging Face and are pinned by revision
and checksum in the `Dockerfile`: `opencv/face_detection_yunet` (MIT) and
`opencv/face_recognition_sface` (Apache 2.0).

## Tests

```
docker compose --profile test build test
docker compose --profile test run --rm test
```

The suite includes an end-to-end check that runs the real models against the
mounted photos; it is skipped when the photos folder is empty.
