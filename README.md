# Face Finder

Take a selfie, get every gallery photo you appear in. Runs entirely on your
computer: the selfie is analysed in memory and never stored or sent anywhere.

Two ways to use it:

- **Local mode**: just for you, on your own computer.
- **Event mode**: shared with guests over HTTPS, behind an access code.

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

## Event mode

Share the app with guests so each of them can take a selfie and find their
photos. Event mode adds HTTPS, which phones need before they allow camera
access, and an access code.

Copy `.env.example` to `.env` and set at least `ACCESS_CODE` (8 characters or
more) and `EVENT_NAME`. Then pick one of the ways to publish it.

| Option | Guests can be | Port forwarding | Domain name |
|---|---|---|---|
| A: tunnel | Anywhere | Not needed | Not needed |
| B1: public domain | Anywhere | Needed (80 and 443) | Needed; DuckDNS gives a free one |
| B2: local network | On the same Wi-Fi | Not needed | Not needed |

### Option A: public link through a tunnel

No router or domain setup. Works from any network.

```
docker compose -f docker-compose.yml -f docker-compose.event.yml --profile tunnel up -d --build
docker compose -f docker-compose.yml -f docker-compose.event.yml --profile tunnel logs tunnel
```

The logs show an address like `https://some-random-words.trycloudflare.com`.
It changes every time the tunnel restarts.

### Option B: self-hosted behind the built-in proxy

Both variants start with the same command; they differ in the `.env` settings.

```
docker compose -f docker-compose.yml -f docker-compose.event.yml --profile proxy up -d --build
```

| Variant | `EVENT_HOST` | `EVENT_TLS` |
|---|---|---|
| B1: public domain | your domain, such as `myevent.duckdns.org` | empty |
| B2: local network | this machine's address, such as `192.168.1.50` | `tls internal` |

#### B1: public domain

Guests can connect from anywhere, and the HTTPS certificate is issued
automatically with no browser warning. It takes three steps.

1. **Get a domain name that points to your network.** Any domain works. A free
   choice is [DuckDNS](https://www.duckdns.org): sign in, create a name such as
   `myevent`, and set its IP to your public address (the page fills it in when
   you open it from the same network). Use `myevent.duckdns.org` as `EVENT_HOST`.
2. **Forward ports 80 and 443 on your router** to the computer running the app.
   Look for "port forwarding" or "virtual server" in the router settings, and
   forward both ports (TCP) to the computer's local address, such as
   `192.168.1.50`. Port 80 is needed to issue the certificate, port 443 serves
   the app.
3. **Allow both ports through the computer's firewall** if it asks.

Things that commonly go wrong:

- **Port forwarding does nothing.** Some internet providers put several
  customers behind one shared public address (carrier-grade NAT), common on
  mobile and some fibre connections. If the address shown by DuckDNS differs
  from the WAN address in your router, you are behind one: use Option A.
- **The certificate is not issued.** The name must already point to you and
  both ports must be reachable before the first start. Check the proxy logs.
- **Your public address changes.** Home addresses can change, usually after a
  router restart. For a one-day event, set the address in DuckDNS shortly
  before. For longer, DuckDNS documents small updater scripts that keep it
  current.
- **Testing from inside your own network fails.** Some routers cannot reach
  their own public address from the inside. Test from a phone on mobile data.

Close the forwarded ports again when the event is over.

#### B2: local network

Nothing to set up on the router and nothing exposed to the internet. Guests
must be on the same Wi-Fi as the computer running the app, and they see a
certificate warning once, which they have to accept.

### Inviting guests

Send guests a join link, or print it as a QR code. It logs them in directly:

```
https://<address>/#code=<ACCESS_CODE>
```

The code sits after the `#`, so browsers never send it to the server and it
stays out of the logs. Guests who open the plain address are asked for the code.

### Event settings

| Variable | Default | Meaning |
|---|---|---|
| `ACCESS_CODE` | none, required | Code guests enter, 8 characters or more |
| `EVENT_NAME` | `Face Finder` | Title shown on the page |
| `EVENT_HOST` | `localhost` | Name or address guests open (proxy only) |
| `EVENT_TLS` | empty | Set to `tls internal` on a local network (proxy only) |
| `SEARCH_RATE_LIMIT` | `60` | Searches per minute from one address |
| `HTTP_PORT`, `HTTPS_PORT` | `80`, `443` | Published ports (proxy only) |
| `CLOUDFLARED_VERSION` | `2026.9.3` | Version of the tunnel client (tunnel only) |

### What to know before sharing

- **Anyone with the code can search with any face.** A guest can upload a
  photo of someone else and find that person's photos. Share the code only
  with people who may see the whole gallery.
- **Selfies are never stored.** They are analysed in memory and discarded.
- **Sessions last 12 hours.** Changing `ACCESS_CODE` and restarting ends them all.
- **Wrong codes are limited** to 20 per 5 minutes from one address.
- **On Docker Desktop (Windows, macOS) with the proxy, all guests appear to
  come from one address**, so they share the search and wrong-code limits.
  Raise `SEARCH_RATE_LIMIT` for a large event. The tunnel is not affected.

Stop event mode with the same command, replacing `up -d --build` with `down`.

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
