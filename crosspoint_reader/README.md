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

## Optimizer

The plugin can optimize EPUBs before transfer, mirroring the optimizer built into
the CrossPoint web server. When enabled (Preferences > Plugins > device config >
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

After each transfer a summary dialog lists what changed per book (before→after
size, images processed, fixes, per-image steps). If optimization fails for a
book, the original is sent unchanged so a transfer is never blocked.

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
6. When the transfer finishes, a **summary dialog** opens showing, per book, the
   before→after size, how many images were processed/cropped, and the fixes
   applied. The same steps are also written to the plugin **Log** (visible in the
   config dialog when **Enable debug logging** is on).

To turn the feature off, uncheck **Optimize EPUBs before transfer** — books are
then sent exactly as Calibre exports them.

Install:
1. Download the latest release from the [releases page](https://github.com/crosspoint-reader/calibre-plugins/releases) (or zip the contents of this directory).
2. In Calibre: Preferences > Plugins > Load plugin from file.
3. The device should appear in Calibre once it is discoverable on the network.

No configuration needed. The plugin auto-discovers the device via UDP and
falls back to 192.168.4.1:81.
