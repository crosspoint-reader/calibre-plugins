"""Live optimization progress dialog (shown during transfer).

The device driver's ``upload_books`` runs on Calibre's device-manager thread, not
the GUI thread, and Qt widgets may only be created/shown from the GUI thread.

Cross-thread delivery only works reliably when the receiving QObject was *created
on the GUI thread* (moving a worker-thread object to the GUI thread does not make
queued delivery work). So the bridge is created once, eagerly, at plugin import
time — which Calibre performs on the main thread, with the QApplication already
running — and the worker thread merely emits queued signals to it:

    begin(title)        -> open the dialog and show "working"
    step(tag, message)  -> append a line as each image/fix is processed
    finish(payload)     -> append the totals and enable the Close button

The dialog is modeless, so it appears as soon as optimization starts and updates
live while books are processed and uploaded.
"""

from qt.core import QObject, QApplication, QThread, Qt, pyqtSignal

from .log import add_log
from .optimizer import _human


def _summary_lines(payload):
    books = payload.get('books', [])
    total_orig = sum(b['orig_size'] for b in books)
    total_new = sum(b['new_size'] for b in books)
    total_imgs = sum(b['images'] for b in books)
    total_fixes = sum(b['fixes'] for b in books)
    total_err = sum(b['errors'] for b in books)
    saved = total_orig - total_new
    pct = (saved / float(total_orig) * 100.0) if total_orig else 0.0
    lines = [
        '',
        '──────────────────────────────',
        'Done: %d book(s), %d image(s), %d fix(es)%s' % (
            len(books), total_imgs, total_fixes,
            ('   %d error(s)' % total_err) if total_err else ''),
        'Total size: %s → %s  (%+.0f%%)' % (
            _human(total_orig), _human(total_new), -pct),
    ]
    return lines


class _ProgressDialog(object):
    """Wraps the Qt widgets; all methods run on the GUI thread."""

    def __init__(self, title):
        from qt.core import (
            QDialog, QVBoxLayout, QPlainTextEdit, QLabel, QProgressBar,
            QDialogButtonBox,
        )
        try:
            from calibre.gui2.ui import get_gui
            parent = get_gui()
        except Exception:
            parent = None

        self.dlg = QDialog(parent)
        self.dlg.setWindowTitle('CrossPoint optimizer')
        self.dlg.setModal(False)
        self.dlg.resize(660, 480)
        layout = QVBoxLayout(self.dlg)

        self.header = QLabel(title)
        self.header.setWordWrap(True)
        layout.addWidget(self.header)

        self.bar = QProgressBar(self.dlg)
        self.bar.setRange(0, 0)  # indeterminate until finished
        layout.addWidget(self.bar)

        self.view = QPlainTextEdit(self.dlg)
        self.view.setReadOnly(True)
        self.view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        try:
            from qt.core import QFontDatabase
            self.view.setFont(
                QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        except Exception:
            pass
        layout.addWidget(self.view)

        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.buttons.rejected.connect(self.dlg.reject)
        self.buttons.accepted.connect(self.dlg.accept)
        self.buttons.button(QDialogButtonBox.StandardButton.Close).setEnabled(False)
        layout.addWidget(self.buttons)

    def show(self):
        self.dlg.show()
        self.dlg.raise_()
        self.dlg.activateWindow()

    def append(self, line):
        self.view.appendPlainText(line)
        sb = self.view.verticalScrollBar()
        sb.setValue(sb.maximum())

    def finish(self, payload):
        from qt.core import QDialogButtonBox
        for line in _summary_lines(payload):
            self.view.appendPlainText(line)
        self.bar.setRange(0, 1)
        self.bar.setValue(1)
        self.header.setText('Optimization complete.')
        self.buttons.button(QDialogButtonBox.StandardButton.Close).setEnabled(True)
        self.dlg.raise_()


class _Bridge(QObject):
    """Lives on the GUI thread; drives the dialog when signalled from any thread."""

    begin_signal = pyqtSignal(object)
    step_signal = pyqtSignal(object)
    finish_signal = pyqtSignal(object)

    def __init__(self):
        super().__init__()
        q = Qt.ConnectionType.QueuedConnection
        self.begin_signal.connect(self._on_begin, type=q)
        self.step_signal.connect(self._on_step, type=q)
        self.finish_signal.connect(self._on_finish, type=q)
        self._dialog = None

    def _on_begin(self, title):
        try:
            self._dialog = _ProgressDialog(title)
            self._dialog.show()
        except Exception as exc:
            self._dialog = None
            add_log(f'[CrossPoint] failed to open optimizer dialog: {exc}')

    def _on_step(self, line):
        if self._dialog is not None:
            try:
                self._dialog.append(line)
            except Exception:
                pass

    def _on_finish(self, payload):
        if self._dialog is not None:
            try:
                self._dialog.finish(payload)
            except Exception as exc:
                add_log(f'[CrossPoint] optimizer dialog finish failed: {exc}')


_bridge = None


def ensure_bridge():
    """Create the GUI-thread bridge. Must be called on the GUI (main) thread.

    Called at plugin import (startup, main thread) and again from config_widget()
    as a belt-and-suspenders. No-op off the main thread or without a QApplication.
    """
    global _bridge
    if _bridge is not None:
        return _bridge
    app = QApplication.instance()
    if app is None:
        return None
    if QThread.currentThread() != app.thread():
        return None  # cannot safely create a QObject for the GUI thread here
    _bridge = _Bridge()
    return _bridge


# --- API called from the device-manager (worker) thread ---------------------

def begin(title):
    b = _bridge or ensure_bridge()
    if b is not None:
        b.begin_signal.emit(title)


def step(tag, message):
    b = _bridge
    if b is not None:
        b.step_signal.emit('[%s] %s' % (tag, message))


def finish(payload):
    b = _bridge
    if b is not None:
        b.finish_signal.emit(payload)
