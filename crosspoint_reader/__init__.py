"""
CrossPoint Reader - Calibre Device Driver Plugin

A wireless device driver for CrossPoint e-readers with built-in
EPUB image conversion for optimal e-reader compatibility.

Features:
- Wireless book transfer via WebSocket
- Automatic EPUB image conversion to baseline JPEG
- PNG/GIF/WebP/BMP to JPEG conversion
- Fix ALL SVG-wrapped images (not just covers)
- Image scaling to fit e-reader screen
- Light Novel Mode: rotate and split wide images for manga/comics
- Configurable JPEG quality and screen dimensions
"""

from .driver import CrossPointDevice


class CrossPointReaderDevice(CrossPointDevice):
    """CrossPoint Reader device driver for Calibre."""
    pass
