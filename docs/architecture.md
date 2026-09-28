# Architecture

How Find Me In Photos works, and why it is built this way. For commands and rules,
see `AGENTS.md`. For usage, see `README.md`.

## The idea

1. **Detect:** YuNet finds the faces in every gallery photo.
2. **Embed:** SFace turns each face into a vector of 128 numbers.
3. **Match:** the selfie's vector is compared with every face by cosine
   similarity. Each photo is scored by its best-matching face.

Vectors are normalised to length 1, so cosine similarity is a dot product and a
search is one matrix multiplication. A few hundred photos need no vector
database.

## Startup

```
uvicorn -> create_default_app()
             load_settings()            fails fast on invalid settings
             FaceEngine(...)            loads both models
             create_app(...)
               IndexService.start()     background thread
                 list_photos()          skips symlinks and other file types
                 compute_fingerprint()  names, sizes, times, detection settings
                 load_index()           cache hit: ready at once
                 build_index()          cache miss: scan every photo
                 save_index()           written to a temporary file, then renamed
```

The page is served while indexing runs and polls `/api/status` for progress.

## A search request

```
POST /api/search (multipart, field "selfie")
  SecurityHeadersMiddleware   adds headers to whatever response comes back
  TrustedHostMiddleware       unknown Host header -> 400
  BodyLimitMiddleware         too large -> 413, before anything is buffered
  AccessMiddleware            event mode: no session -> 401; too many -> 429
  search_photos()
    require_index()           still indexing -> 503
    read_upload()             size check
    decode_image()            rejects unreadable and oversized images
    find_selfie_faces()       tries 640, 320, then 1280 pixels
    largest_face()            the person taking the selfie
    search()                  best face per photo, highest score first
```

The server returns every match above `MIN_SCORE` (0.2). The page filters by the
strictness slider, so moving the slider needs no new request.

## Middleware order

Starlette runs the middleware added last first. `create_app` adds them
innermost first, which gives this order for a request:

```
SecurityHeaders -> TrustedHost -> BodyLimit -> Access -> routes
```

Two consequences matter:

- Security headers are on every response, including rejections.
- Access is checked before the body is parsed, so a stranger cannot make the
  app buffer an upload.

## Event mode

Setting `ACCESS_CODE` turns it on. There is one shared code, by design: the app
is for private events, and accounts would be out of proportion.

| Part | Choice | Reason |
|---|---|---|
| Session | Signed token `<expiry>.<hmac>` in a cookie | No storage needed; survives restarts |
| Signing key | PBKDF2 of the access code, computed once | A captured token cannot be used to guess the code quickly |
| Cookie | `HttpOnly`, `SameSite=Strict`, `Secure` over HTTPS | Out of reach of scripts and of other sites |
| Access check | Deny by default, with a short list of open paths | A route added later starts out protected |
| Join link | Code after the `#` in the address | Browsers never send that part, so it stays out of logs |
| Wrong codes | Only failures count: 20 per 5 minutes per address | Guests on one Wi-Fi share an address; successful logins must not lock them out |
| Rate limits | Kept in memory, per address, capped at 10,000 addresses | One process, one event; nothing to install |

Changing the access code changes the signing key, which ends every session.

### Guest addresses

In event mode the app is reached only through the proxy or the tunnel, never
directly. Rate limits need the guest's real address, which arrives in the
`X-Forwarded-For` header:

- The proxy (Caddy) overwrites that header with the address it saw.
- The tunnel appends the real address to whatever the guest sent.
- uvicorn trusts the header only from Docker's private address ranges and
  reads it from the right, stopping at the first address it does not trust.

Together these mean a guest cannot choose the address they are counted under.

## Decisions and their reasons

| Decision | Reason |
|---|---|
| YuNet and SFace through OpenCV | Small, fast on a CPU, permissive licences, one dependency |
| Models fetched at build time, pinned by revision and checksum | Reproducible builds; a changed file fails the build |
| One `FaceEngine` guarded by a lock | OpenCV model objects are not safe to share between threads |
| Index cached as `.npz`, loaded with `allow_pickle=False` | A tampered cache cannot run code |
| Index arrays are read-only | One index is shared by every request thread |
| Selfies tried at several sizes | The detector misses faces that fill the whole frame |
| Images over 40 megapixels refused | A small file can expand to a huge image in memory |
| ZIP built in a spooled temporary file and streamed | Memory stays bounded whatever the selection |
| Photos served by exact name from the index | No path from the request ever reaches the file system |
| Status snapshots are immutable and swapped under a lock | Readers never see a half-updated state |
| Port bound to `127.0.0.1` in local mode | Local mode has no login |
| `127.0.0.1` is always an allowed host | The container's health check calls it, whatever `ALLOWED_HOSTS` says |
| Container is read-only, without capabilities, non-root | Limits what a bug in image decoding could do |
| Page in plain JavaScript | No build step, and a strict content security policy stays possible |
| Uploads shrunk in the browser | Phone photos are large and event Wi-Fi is slow |

## Where to change what

| To change | Look at |
|---|---|
| How faces are detected or compared | `app/faces.py`, then raise `INDEX_VERSION` in `app/service.py` |
| Which files count as photos | `PHOTO_EXTENSIONS` and `list_photos` in `app/index.py` |
| A setting | `app/config.py`, the compose file, `.env.example`, `README.md` |
| Which routes are open in event mode | `OPEN_PATHS` in `app/security.py` |
| Rate limits | `create_app` in `app/main.py`, `app/session.py` for logins |
| Session length | `SESSION_SECONDS` in `app/access.py` |
| Response headers | `SECURITY_HEADERS` in `app/security.py` |
| The HTTPS front door | `Caddyfile`, `docker-compose.event.yml` |
