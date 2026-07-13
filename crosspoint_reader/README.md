# CrossPoint Reader Calibre Plugin

This plugin adds CrossPoint Reader as a wireless device in Calibre. It uploads
EPUB files over WebSocket to the CrossPoint web server.

Protocol:
- Connect to ws://<host>:<port>/
- Send: START:<filename>:<size>:<path>
- Wait for READY
- Send binary frames with file content
- Wait for DONE (or ERROR:<message>)

Default settings:
- Auto-discover device via UDP
- Host fallback: 192.168.4.1
- Port: 81
- Upload path: /
- Upload reliability: 3 retries, 2s retry delay, 1s delay between books,
  30s socket timeout

## Optimizer

The plugin can optimize EPUBs before transfer, mirroring the optimizer built into
the CrossPoint web server. The optimizer is based on the initial work by
[@zgredex](https://github.com/zgredex), ported here from the firmware. When
enabled (Preferences > Plugins > device config >
"Optimize EPUBs before transfer"), each EPUB is processed before upload:

- Every image is scaled to fit the device screen, converted to grayscale, and
  re-encoded as JPEG (quality configurable, default 85). Optional auto-crop trims
  uniform page margins.
- The container is rewritten: raster images are renamed to `.jpg`, stale `<img>`
  width/height are stripped, SVG covers/wrapped images are unwrapped, OPF
  media-types and cover meta are fixed, the NCX identifier is synced, a small
  defensive stylesheet is injected, and the archive is re-zipped mimetype-first.

The target screen size comes from the device profile — **X4 = 480×800**,
**X3 = 528×792** — which is auto-detected from the device's `/api/status`
endpoint on connect (matching the web UI), or can be set manually
(Auto / X4 / X3) in the plugin settings.

When a transfer starts, a live dialog opens and streams the optimization steps
as they happen (per book: each image's before→after size, fixes, and upload),
then finishes with the totals. When several books are sent at once they are
processed in turn in the same dialog, which ends with combined totals. If
optimization fails for a book, the original is sent unchanged so a transfer is
never blocked.

### Using the optimizer

1. Open **Preferences → Plugins**, expand **Device Interface plugins**, select
   **CrossPoint Reader**, and click **Customize plugin**.
2. Check **Optimize EPUBs before transfer**.
3. Set the options below it (they enable once the box is checked):
   - **Device target** — leave on **Auto-detect** to read X3/X4 from the device
     on connect, or force **X4** / **X3**. Auto-detect falls back to X4 if the
     device can't be queried.
   - **JPEG quality** — 1–100 (default 85). Lower = smaller files.
   - **Convert images to grayscale** — on by default (recommended for e-ink).
   - **Auto-crop uniform margins** — off by default; trims solid page borders.
4. Click **OK**, then **restart Calibre** if it was already running so the new
   settings take effect.
5. Send a book to the device as usual (right-click → *Send to device*, or the
   **Send to device** toolbar button). The EPUB is optimized just before upload.
6. As the transfer runs, a **live dialog** opens and streams each step — per
   book, the images processed/cropped and fixes applied — and ends with the
   combined totals (and a **Close** button). Selecting multiple books processes
   them one after another in the same dialog. The same steps are also written to
   the plugin **Log** (visible in the config dialog when **Enable debug logging**
   is on).

To turn the feature off, uncheck **Optimize EPUBs before transfer** — books are
then sent exactly as Calibre exports them.

## On-device status

When the device connects, Calibre marks the library books that are already on it
(the "on device" indicator, the same one you see right after sending a book).

No download is needed. On connect the plugin recognizes device files in two fast,
local ways:

1. **Sent-book cache** — when a book is sent, its identity (`uuid`, title,
   authors) is cached locally, so it's recognized instantly on every later
   connect. The cache is updated on send/delete and pruned when files disappear.
2. **Library name match** — a book can only be marked on-device if it's in your
   library, so for anything not in the cache (e.g. side-loaded, or sent from
   another computer) the plugin matches the device **file name against your
   library by title** and attaches that book's `uuid`. When several library books
   share a title, an author in the device path disambiguates; if it's still
   ambiguous, the file is left unmarked rather than mismatched.

Both happen with no network transfer. The optional **Fetch metadata** setting is
a last resort for library books whose on-device filename doesn't resemble the
title at all: it downloads each unmatched EPUB once on connect to read its exact
identity, then caches it. Most setups never need it — leave it off unless some
library books still aren't marked.

Install:
1. Download the latest release from the [releases page](https://github.com/crosspoint-reader/calibre-plugins/releases) (or zip the contents of this directory).
2. In Calibre: Preferences > Plugins > Load plugin from file.
3. The device should appear in Calibre once it is discoverable on the network.

No configuration needed. The plugin auto-discovers the device via UDP and
falls back to 192.168.4.1:81.
