from calibre.utils.config import JSONConfig
from qt.core import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
    Qt,
)

from .log import get_log_text


PREFS = JSONConfig('plugins/crosspoint_reader')

# Connection settings
PREFS.defaults['host'] = '192.168.4.1'
PREFS.defaults['port'] = 81
PREFS.defaults['path'] = '/'
PREFS.defaults['chunk_size'] = 2048
PREFS.defaults['debug'] = False
PREFS.defaults['fetch_metadata'] = False

# Conversion settings
PREFS.defaults['enable_conversion'] = True
PREFS.defaults['jpeg_quality'] = 85
PREFS.defaults['light_novel_mode'] = False
PREFS.defaults['screen_width'] = 480
PREFS.defaults['screen_height'] = 800
PREFS.defaults['split_overlap'] = 15  # percentage


class CrossPointConfigWidget(QWidget):
    def __init__(self):
        super().__init__()
        layout = QVBoxLayout(self)
        
        # Connection Settings Group
        conn_group = QGroupBox("Connection Settings")
        conn_layout = QFormLayout()
        
        self.host = QLineEdit(self)
        self.port = QSpinBox(self)
        self.port.setRange(1, 65535)
        self.path = QLineEdit(self)
        self.chunk_size = QSpinBox(self)
        self.chunk_size.setRange(512, 65536)
        self.debug = QCheckBox('Enable debug logging', self)
        self.fetch_metadata = QCheckBox('Fetch metadata (slower device list)', self)

        self.host.setText(PREFS['host'])
        self.port.setValue(PREFS['port'])
        self.path.setText(PREFS['path'])
        self.chunk_size.setValue(PREFS['chunk_size'])
        self.debug.setChecked(PREFS['debug'])
        self.fetch_metadata.setChecked(PREFS['fetch_metadata'])

        conn_layout.addRow('Host', self.host)
        conn_layout.addRow('Port', self.port)
        conn_layout.addRow('Upload path', self.path)
        conn_layout.addRow('Chunk size', self.chunk_size)
        conn_layout.addRow('', self.debug)
        conn_layout.addRow('', self.fetch_metadata)
        conn_group.setLayout(conn_layout)
        layout.addWidget(conn_group)
        
        # Conversion Settings Group
        conv_group = QGroupBox("Image Conversion Settings")
        conv_layout = QFormLayout()
        
        self.enable_conversion = QCheckBox('Enable EPUB image conversion', self)
        self.enable_conversion.setChecked(PREFS['enable_conversion'])
        self.enable_conversion.setToolTip(
            "Convert images to baseline JPEG format for e-reader compatibility.\n"
            "Converts PNG/GIF/WebP/BMP to JPEG, fixes SVG covers, and scales images."
        )
        conv_layout.addRow('', self.enable_conversion)
        
        # JPEG Quality slider with value display
        quality_widget = QWidget()
        quality_layout = QHBoxLayout(quality_widget)
        quality_layout.setContentsMargins(0, 0, 0, 0)
        
        self.jpeg_quality = QSlider(Qt.Orientation.Horizontal)
        self.jpeg_quality.setRange(1, 95)
        self.jpeg_quality.setValue(PREFS['jpeg_quality'])
        self.jpeg_quality.setTickPosition(QSlider.TickPosition.TicksBelow)
        self.jpeg_quality.setTickInterval(10)
        
        self.quality_label = QLabel(f"{PREFS['jpeg_quality']}%")
        self.quality_label.setMinimumWidth(40)
        self.jpeg_quality.valueChanged.connect(
            lambda v: self.quality_label.setText(f"{v}%")
        )
        
        quality_layout.addWidget(self.jpeg_quality)
        quality_layout.addWidget(self.quality_label)
        conv_layout.addRow('JPEG Quality', quality_widget)
        
        # Quality presets
        presets_widget = QWidget()
        presets_layout = QHBoxLayout(presets_widget)
        presets_layout.setContentsMargins(0, 0, 0, 0)
        
        self.preset_buttons = []  # Track for enable/disable
        for name, value in [('Low (60%)', 60), ('Medium (75%)', 75), 
                           ('High (85%)', 85), ('Max (95%)', 95)]:
            btn = QPushButton(name)
            btn.clicked.connect(lambda checked, v=value: self._set_quality(v))
            presets_layout.addWidget(btn)
            self.preset_buttons.append(btn)
        
        conv_layout.addRow('Presets', presets_widget)
        
        # Light Novel Mode
        self.light_novel_mode = QCheckBox('Light Novel Mode (rotate & split wide images)', self)
        self.light_novel_mode.setChecked(PREFS['light_novel_mode'])
        self.light_novel_mode.setToolTip(
            "Rotate horizontal images 90° and split into multiple pages\n"
            "for vertical reading on e-readers. Best for manga/comics."
        )
        conv_layout.addRow('', self.light_novel_mode)
        
        # Screen dimensions
        screen_widget = QWidget()
        screen_layout = QHBoxLayout(screen_widget)
        screen_layout.setContentsMargins(0, 0, 0, 0)
        
        self.screen_width = QSpinBox()
        self.screen_width.setRange(100, 2000)
        self.screen_width.setValue(PREFS['screen_width'])
        self.screen_width.setSuffix(' px')
        
        screen_layout.addWidget(self.screen_width)
        screen_layout.addWidget(QLabel('×'))
        
        self.screen_height = QSpinBox()
        self.screen_height.setRange(100, 2000)
        self.screen_height.setValue(PREFS['screen_height'])
        self.screen_height.setSuffix(' px')
        
        screen_layout.addWidget(self.screen_height)
        screen_layout.addStretch()
        conv_layout.addRow('Screen Size', screen_widget)
        
        # Split overlap
        overlap_widget = QWidget()
        overlap_layout = QHBoxLayout(overlap_widget)
        overlap_layout.setContentsMargins(0, 0, 0, 0)
        
        self.split_overlap = QSpinBox()
        self.split_overlap.setRange(0, 50)
        self.split_overlap.setValue(PREFS['split_overlap'])
        self.split_overlap.setSuffix('%')
        self.split_overlap.setToolTip("Overlap between split pages (for Light Novel Mode)")
        
        overlap_layout.addWidget(self.split_overlap)
        overlap_layout.addStretch()
        conv_layout.addRow('Split Overlap', overlap_widget)
        
        conv_group.setLayout(conv_layout)
        layout.addWidget(conv_group)
        
        # Enable/disable conversion options based on checkbox
        self.enable_conversion.toggled.connect(self._update_conversion_enabled)
        self._update_conversion_enabled(self.enable_conversion.isChecked())
        
        # Log section
        log_group = QGroupBox("Debug Log")
        log_layout = QVBoxLayout()
        
        self.log_view = QPlainTextEdit(self)
        self.log_view.setReadOnly(True)
        self.log_view.setMaximumHeight(150)
        self.log_view.setPlaceholderText('Discovery and conversion log will appear here.')
        self._refresh_logs()

        refresh_btn = QPushButton('Refresh Log', self)
        refresh_btn.clicked.connect(self._refresh_logs)
        
        log_layout.addWidget(self.log_view)
        log_layout.addWidget(refresh_btn)
        log_group.setLayout(log_layout)
        layout.addWidget(log_group)
    
    def _set_quality(self, value):
        """Set JPEG quality from preset button."""
        self.jpeg_quality.setValue(value)
    
    def _update_conversion_enabled(self, enabled):
        """Enable/disable conversion options based on master checkbox."""
        self.jpeg_quality.setEnabled(enabled)
        self.quality_label.setEnabled(enabled)
        for btn in self.preset_buttons:
            btn.setEnabled(enabled)
        self.light_novel_mode.setEnabled(enabled)
        self.screen_width.setEnabled(enabled)
        self.screen_height.setEnabled(enabled)
        self.split_overlap.setEnabled(enabled)

    def save(self):
        # Connection settings
        PREFS['host'] = self.host.text().strip() or PREFS.defaults['host']
        PREFS['port'] = int(self.port.value())
        PREFS['path'] = self.path.text().strip() or PREFS.defaults['path']
        PREFS['chunk_size'] = int(self.chunk_size.value())
        PREFS['debug'] = bool(self.debug.isChecked())
        PREFS['fetch_metadata'] = bool(self.fetch_metadata.isChecked())
        
        # Conversion settings
        PREFS['enable_conversion'] = bool(self.enable_conversion.isChecked())
        PREFS['jpeg_quality'] = int(self.jpeg_quality.value())
        PREFS['light_novel_mode'] = bool(self.light_novel_mode.isChecked())
        PREFS['screen_width'] = int(self.screen_width.value())
        PREFS['screen_height'] = int(self.screen_height.value())
        PREFS['split_overlap'] = int(self.split_overlap.value())

    def _refresh_logs(self):
        self.log_view.setPlainText(get_log_text())

    def validate(self):
        return True


class CrossPointConfigDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('CrossPoint Reader')
        self.setMinimumWidth(500)
        self.widget = CrossPointConfigWidget()
        layout = QVBoxLayout(self)
        layout.addWidget(self.widget)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok |
                                   QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
