from .driver import CrossPointDevice


class CrossPointReaderDevice(CrossPointDevice):
    pass


# Create the optimization-summary bridge on the main thread at plugin load, so
# that the post-transfer dialog can be shown safely from the device thread.
try:
    from . import summary as _summary
    _summary.ensure_bridge()
except Exception:
    pass
