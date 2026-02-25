# CrossPoint Reader - Calibre Plugin

A Calibre device driver plugin for CrossPoint e-readers with built-in EPUB image conversion for optimal e-reader compatibility.

## Version 0.2.3

## Features

### Wireless Book Transfer
- Automatic device discovery via UDP broadcast
- WebSocket-based file transfer
- Support for nested folder structures
- Configurable upload paths

### EPUB Image Conversion
Automatically converts EPUB images before uploading for maximum e-reader compatibility:

- **Image Format Conversion**: Converts PNG, GIF, WebP, and BMP to baseline JPEG
- **SVG Cover Fix**: Converts SVG-based covers to standard HTML img tags
- **Image Scaling**: Scales oversized images to fit your e-reader screen
- **Light Novel Mode**: Rotates horizontal images 90° and splits them into multiple pages for manga/comics reading on vertical e-reader screens

### Configuration Options

#### Connection Settings
- **Host**: Device IP address (default: 192.168.4.1)
- **Port**: WebSocket port (default: 81)
- **Upload Path**: Default upload directory (default: /)
- **Chunk Size**: Transfer chunk size in bytes (default: 2048)
- **Debug Logging**: Enable detailed logging
- **Fetch Metadata**: Read metadata from device (slower)

#### Image Conversion Settings
- **Enable Conversion**: Turn EPUB image conversion on/off
- **JPEG Quality**: 1-95% (default: 85%)
  - Presets: Low (60%), Medium (75%), High (85%), Max (95%)
- **Light Novel Mode**: Rotate and split wide images
- **Screen Size**: Target screen dimensions (default: 480×800 px)
- **Split Overlap**: Overlap percentage for split pages (default: 15%)

## Installation

1. Download the plugin ZIP file
2. In Calibre, go to Preferences → Plugins → Load plugin from file
3. Select the downloaded ZIP file
4. Restart Calibre

## Usage

1. Connect your CrossPoint Reader to the same WiFi network as your computer
2. The device should appear automatically in Calibre's device list
3. Configure settings via Preferences → Plugins → CrossPoint Reader → Customize plugin
4. Send books to device as usual - images will be automatically converted

## What the Converter Does

✓ Converts PNG/GIF/WebP/BMP to baseline JPEG
✓ Fixes SVG covers for e-reader compatibility  
✓ Scales large images to fit your screen dimensions
✓ Light Novel Mode: rotates & splits wide images for manga/comics
✓ Maintains EPUB structure and metadata
✓ Preserves original file if conversion fails

## Requirements

- Calibre 5.0 or later
- CrossPoint Reader device with WebSocket server enabled
- Same WiFi network for device discovery

## Troubleshooting

### Device not detected
1. Ensure device and computer are on the same network
2. Check the Host setting in plugin configuration
3. Enable debug logging to see discovery attempts
4. Try manually entering the device IP address

### Images not converting
1. Verify "Enable EPUB image conversion" is checked
2. Check the debug log for conversion errors
3. Ensure sufficient disk space for temporary files

### Poor image quality
- Increase JPEG Quality setting (try 85% or 95%)

### Split images not aligned
- Adjust Split Overlap percentage (try 15-20%)

## License

This plugin is provided as-is for use with CrossPoint Reader devices.

## Changelog

### v0.2.3
- Fixed: Basename collision when EPUBs have duplicate filenames in different folders (e.g., `Images/cover.png` and `assets/cover.png`) - now uses full path as key
- Fixed: Split Overlap control now disabled when Light Novel Mode is off (reflects actual dependency)
- Fixed: Failed image conversions now preserve original extension instead of writing invalid `.jpg` files
- Fixed: Temp file cleanup errors are now logged instead of silently ignored

### v0.2.2
- Changed: Conversion disabled by default (opt-in for safe upgrades from v0.1.x)
- Fixed: OPF manifest href now uses correct relative paths for images in subdirectories
- Fixed: `</metadata>` replacement limited to first occurrence
- Fixed: Temp file cleanup on conversion failure
- Fixed: Progress callback closure now correctly captures loop index
- Fixed: Added length validation for files/names to prevent silent truncation
- Removed: Unused TemporaryDirectory import

### v0.2.1
- Fixed: mimetype now written first in EPUB archive (EPUB OCF spec compliance)
- Fixed: Preset quality buttons now disable when conversion is toggled off
- Fixed: Closure variable binding in replacement functions (B023)

### v0.2.0
- Added EPUB image conversion
- Added Light Novel Mode (rotate & split)
- Added configurable JPEG quality
- Added screen size settings
- Improved configuration UI with grouped settings

### v0.1.1
- Initial release
- Wireless book transfer
- Device auto-discovery
