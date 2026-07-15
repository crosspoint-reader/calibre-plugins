# Calibre Plugins

Calibre plugins for [CrossPoint Reader](https://github.com/crosspoint-reader).

## Plugins

### CrossPoint Reader

A wireless device plugin that uploads EPUB files to CrossPoint Reader over WebSocket. The plugin auto-discovers devices on the local network via UDP broadcast.

It can also **optimize EPUBs before transfer** — mirroring the optimizer built into the CrossPoint web server — by resizing images to the device screen (X4 480×800 / X3 528×792, auto-detected), converting them to grayscale JPEG, and rewriting the container. The optimizer is based on the initial work by [@zgredex](https://github.com/zgredex), ported here from the firmware. Enable it in the plugin's settings; see the [usage instructions](crosspoint_reader/README.md#using-the-optimizer).

See [crosspoint_reader/README.md](crosspoint_reader/README.md) for protocol details, the optimizer, and configuration.

## Installation

Download the latest release ZIP from the [releases page](https://github.com/crosspoint-reader/calibre-plugins/releases). Do not open or extract the ZIP; install the ZIP file itself in Calibre via **Preferences > Advanced > Plugins > Load plugin from file**.

## Development

### Setup

```sh
# Build and install the plugin into Calibre
make install

# List all installed plugins
make list

# Remove the plugin
make remove
```

See [`calibre-customize` docs](https://manual.calibre-ebook.com/generated/en/calibre-customize.html) for more options.

### Releasing

The version is read from the `version` tuple in `crosspoint_reader/driver.py`.

```sh
# Bump the version (updates driver.py in place)
make bump-patch   # 1.0.0 -> 1.0.1
make bump-minor   # 1.0.0 -> 1.1.0
make bump-major   # 1.0.0 -> 2.0.0

# Package the plugin into a zip (crosspoint_reader-vX.Y.Z.zip)
make zip

# Create a GitHub release with the zip attached (runs `make zip` first)
make release
```

`make release` uses the [GitHub CLI](https://cli.github.com/) (`gh`) to create a tagged release with auto-generated notes.

Typical workflow:

1. `make bump-patch` (or `bump-minor` / `bump-major`)
2. Commit the version change
3. `make release`

### Project structure

```
crosspoint_reader/
  __init__.py   # Plugin entry point
  driver.py     # Device driver (discovery, upload, delete)
  ws_client.py  # WebSocket client and UDP discovery
  config.py     # Settings UI and preferences
  log.py        # Logging utilities
```

## License

[MIT](LICENSE)
