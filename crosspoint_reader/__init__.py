from .driver import CrossPointDevice
import importlib

_output = importlib.import_module('.output', __package__)
if getattr(_output, '__file__', None):
    _output = importlib.reload(_output)

CrossPointEpubDispatcher = _output.CrossPointEpubDispatcher
CrossPointX3EpubOutput = _output.CrossPointX3EpubOutput
CrossPointX4EpubOutput = _output.CrossPointX4EpubOutput


class CrossPointReaderDevice(CrossPointDevice):
    def initialize(self):
        super().initialize()

        # Calibre loads one primary plugin class per archive. Register the
        # output plugins here so one installation can provide all capabilities.
        from calibre.customize import ui
        if ui.is_disabled(self):
            return

        output_plugins = [
            # We need to intercept .x4.epub and .x3.epub files for special handling,
            # otherwise Calibre will start treating them as regular .epub files during
            # conversion
            CrossPointEpubDispatcher(self.plugin_path),
            # Handles converting to a .x3.epub - that is, just an epub optimized for x3
            CrossPointX3EpubOutput(self.plugin_path),
            # Handles converting to a .x4.epub
            CrossPointX4EpubOutput(self.plugin_path),
        ]
        for output_plugin in output_plugins:
            output_plugin.installation_type = self.installation_type
            output_plugin.initialize()
        ui._initialized_plugins[0:0] = output_plugins


# Create the optimization-summary bridge on the main thread at plugin load, so
# that the post-transfer dialog can be shown safely from the device thread.
try:
    from . import summary as _summary
    _summary.ensure_bridge()
except Exception:
    pass
