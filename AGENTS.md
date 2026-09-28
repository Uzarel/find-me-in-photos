# Agent guide

Find Me In Photos is a small self-hosted web app for events: a guest takes a
selfie and gets every photo of the gallery they appear in. It runs in Docker,
in two modes: event (shared with guests over HTTPS, behind an access code) and
local (one user, no login).

Read `README.md` for usage and settings, and `docs/architecture.md` for how the
parts fit together and why they are built this way.

## Commands

Tests run only in Docker. The host is not expected to have `pytest` or OpenCV.

```
docker compose --profile test build test      # rebuild after EVERY code change
docker compose --profile test run --rm test   # run the suite with coverage
docker compose up -d --build                  # run the app at http://localhost:8000
docker compose logs -f find-me-in-photos
```

The code is copied into the image, not mounted. If you skip the rebuild, you
are testing the previous version.

To try event mode without exposing anything, bind it to this machine:

```
ACCESS_CODE=test-code-12345 EVENT_HOST=localhost EVENT_TLS="tls internal" \
EVENT_BIND=127.0.0.1 HTTP_PORT=8081 HTTPS_PORT=8443 \
docker compose -f docker-compose.yml -f docker-compose.event.yml --profile proxy up -d --build
```

Then use `curl -k https://localhost:8443/...`. Stop it with the same command
and `down`, then start local mode again if it was running before.

If the owner's own stack is running, do not replace it. Add `-p verify` to the
command to start a separate copy, and remove it with `down -v` afterwards.

## Layout

| Path | Responsibility |
|---|---|
| `app/config.py` | Settings from environment variables, validated once at startup |
| `app/faces.py` | Image decoding; `FaceEngine` wraps the YuNet and SFace models |
| `app/index.py` | Build, cache (`.npz`) and search the face index |
| `app/service.py` | Background indexing thread and its status snapshots |
| `app/access.py` | Access code check, session tokens, `RateLimiter` |
| `app/session.py` | `/api/session`, `/api/login`, `/api/logout` |
| `app/security.py` | ASGI middlewares: access, body size limit, security headers |
| `app/responses.py` | Response envelope and `ApiError` |
| `app/main.py` | Routes, exception handlers, app factory, middleware order |
| `app/static/` | Page, in plain HTML, CSS and JavaScript (no build step) |
| `tests/helpers.py` | `FakeExtractor`, synthetic images, `make_settings` |
| `tests/test_page.py` | Static checks on the page files (no browser) |
| `docker-compose.event.yml`, `Caddyfile` | Event mode: HTTPS proxy or tunnel |

## Rules that must not be broken

These protect people's photos. Treat a change that weakens one as a bug.

1. **Never commit photos or `.env`.** Both are in `.gitignore`. Check
   `git status` before staging, and never use `git add -f` on them.
2. **Never expose the app beyond this machine without the owner asking.** That
   means: do not start the `tunnel` profile, and do not bind event mode to
   `0.0.0.0`, as part of testing. Both publish a private gallery.
3. **Never set `FORWARDED_ALLOW_IPS` to `*`.** Guests could then fake their
   address and dodge every rate limit. The value in `docker-compose.event.yml`
   must match `TRUSTED_PROXIES` in `tests/test_event_mode.py`.
4. **Routes are locked by default in event mode.** A new route needs a session
   unless you add it to `OPEN_PATHS` in `app/security.py`. Only do that for
   routes that reveal nothing about the gallery.
5. **Serve photos only through `is_servable`** in `app/main.py`. It rejects
   names outside the index, files that failed to decode, and symlinks.
6. **Never store or log a selfie**, its bytes or its embedding. Selfies are
   analysed in memory and discarded.
7. **Never log or return the access code.** Error messages shown to guests
   must not contain internal details or file paths.

## Conventions

- **Tests first.** Write the failing test, then the code. Keep coverage above
  80% (it is 96%). Fix the code, not the test, unless the test is wrong.
- **Immutable data.** Frozen dataclasses and tuples. Build a new value instead
  of changing one in place. Arrays in `FaceIndex` are write-protected.
- **Small units.** Functions under 50 lines, files under 800, no nesting
  deeper than 4 levels.
- **Type annotations** on every function signature.
- **Named constants**, not bare numbers or strings, at the top of the module.
- **Errors are explicit.** Raise `ApiError(status, message)` in routes. Never
  swallow an exception silently; log it with context.
- **One response shape:** `{"success": bool, "data": ..., "error": str | null}`
  from `envelope()` and `error_response()`.
- **`logging`, never `print`.**
- **No new dependencies** unless the standard library and the current ones
  cannot do the job. Pin exact versions.
- **Frontend:** the page has a strict content security policy. No inline
  scripts or styles, no `innerHTML`; build elements and set `textContent`.
- **Every new setting** goes in `app/config.py` with validation, in the compose
  file that passes it through, in `.env.example` and in `README.md`.
- **Commits:** `<type>: <description>`, with types `feat`, `fix`, `refactor`,
  `docs`, `test`, `chore`, `perf`, `ci`.

## Traps

Each of these cost time once. They are not visible from reading the code.

- **`app/faces.py` sets an environment variable before `import cv2`.** OpenCV
  reads `OPENCV_IO_MAX_IMAGE_PIXELS` when first imported. Do not let a
  formatter or linter move the import above it.
- **Changing detection settings needs a cache bump.** If you change how faces
  are detected or embedded, raise `INDEX_VERSION` in `app/service.py`, or the
  app keeps using the old cached index.
- **Exceptions raised inside an ASGI `receive` must be `HTTPException`.**
  FastAPI turns any other exception raised while reading the body into a
  generic 400.
- **The test client cannot set the caller's address.** The installed Starlette
  has no `client=` argument. Use `served_to()` in `tests/test_event_mode.py`.
- **`FakeExtractor` is keyed by gray level.** Test images are solid colours;
  the fake returns the faces registered for the value of the first pixel. Use
  PNG in tests, since JPEG changes pixel values.
- **Docker `ADD --chmod=644` also applies to the folder it creates**, which
  makes it unreadable for the non-root user. The `Dockerfile` sets permissions
  with an explicit `chmod` instead.
- **A CSS rule that sets `display` shows elements marked `hidden`.** The page
  hides parts with the `hidden` attribute, and any `display` rule beats the
  browser's style for it. `style.css` has a `[hidden]` rule with `!important`
  for this; keep it.
- **The health check calls `127.0.0.1`, and event mode replaces the host
  list.** `app/config.py` always adds that address to the allowed hosts. If the
  address in the `Dockerfile` changes, change `HEALTHCHECK_HOST` too.
- **The app container is read-only.** Code can write only to `/data` (index
  cache) and `/tmp` (512 MB, in memory).
- **On Docker Desktop all guests share one address** behind the proxy, because
  of how it routes traffic. Rate limit tests against a live proxy will see a
  single client.
- **On a Windows host, Git Bash paths such as `/tmp` do not exist for Windows
  Python.** Use paths inside the project or a scratch folder in scripts that
  mix `curl` and `python`.

## Before calling a change done

1. The test image was rebuilt and the whole suite passes.
2. Coverage did not drop below 80%.
3. Settings, `.env.example` and `README.md` agree with each other.
4. For changes to access control, rate limits, uploads or file serving: the
   behaviour was also checked against a running container, not only in tests.
5. `git status` shows no photos and no `.env`.
6. The report says what was verified and what was not.

## Known gaps

- The public domain setup (DuckDNS, port forwarding) is documented but untested.
- The page has no browser tests. `tests/test_page.py` only checks the files,
  and the layout was checked by hand in headless Chrome.
- The session cookie (`ff_session`) and the key salt in `app/access.py` keep
  the project's first name, Face Finder. Renaming them ends every session.
- Logout clears the cookie on that device only. Sessions are stateless, so a
  copied cookie stays valid until it expires (12 hours).
