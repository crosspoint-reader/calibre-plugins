import os
import time
import urllib.parse
import urllib.request

from calibre.devices.errors import ControlError
from calibre.devices.interface import DevicePlugin
from calibre.devices.usbms.deviceconfig import DeviceConfig
from calibre.devices.usbms.books import Book
from calibre.ebooks.metadata.book.base import Metadata

from . import ws_client
from .config import CrossPointConfigWidget, PREFS
from .log import add_log


class CrossPointDevice(DeviceConfig, DevicePlugin):
    name = 'CrossPoint Reader'
    gui_name = 'CrossPoint Reader'
    description = 'CrossPoint Reader wireless device with baseline JPEG conversion'
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'Megabit'
    version = (0, 2, 0)

    VENDOR_ID = [0xFFFF]
    PRODUCT_ID = [0xFFFF]
    BCD = [0xFFFF]

    FORMATS = ['epub']
    ALL_FORMATS = ['epub']
    SUPPORTS_SUB_DIRS = True
    MUST_READ_METADATA = False
    MANAGES_DEVICE_PRESENCE = True
    DEVICE_PLUGBOARD_NAME = 'CROSSPOINT_READER'
    SUPPORTS_DEVICE_DB = False
    device_is_usb_mass_storage = False

    def __init__(self, path):
        super().__init__(path)
        self.is_connected = False
        self.device_host = None
        self.device_port = None
        self.last_discovery = 0.0
        self.report_progress = lambda x, y: x
        self._debug_enabled = False

    def _log(self, message):
        add_log(message)
        if self._debug_enabled:
            try:
                self.report_progress(0.0, message)
            except Exception:
                pass

    def _discover(self):
        now = time.time()
        if now - self.last_discovery < 2.0:
            return None, None
        self.last_discovery = now
        host, port = ws_client.discover_device(
            timeout=1.0,
            debug=PREFS['debug'],
            logger=self._log,
            extra_hosts=[PREFS['host']],
        )
        if host and port:
            return host, port
        return None, None

    def detect_managed_devices(self, devices_on_system, force_refresh=False):
        if self.is_connected:
            return self
        debug = PREFS['debug']
        self._debug_enabled = debug
        if debug:
            self._log('[CrossPoint] detect_managed_devices')
        host, port = self._discover()
        if host:
            if debug:
                self._log(f'[CrossPoint] discovered {host} {port}')
            self.device_host = host
            self.device_port = port
            self.is_connected = True
            return self
        if debug:
            self._log('[CrossPoint] discovery failed')
        return None

    def open(self, connected_device, library_uuid):
        if not self.is_connected:
            raise ControlError(desc='Attempt to open a closed device')
        return True

    def get_device_information(self, end_session=True):
        host = self.device_host or PREFS['host']
        device_info = {
            'device_store_uuid': 'crosspoint-' + host.replace('.', '-'),
            'device_name': 'CrossPoint Reader',
            'device_version': '1',
        }
        return (self.gui_name, '1', '1', '', {'main': device_info})

    def reset(self, key='-1', log_packets=False, report_progress=None, detected_device=None):
        self.set_progress_reporter(report_progress)

    def set_progress_reporter(self, report_progress):
        if report_progress is None:
            self.report_progress = lambda x, y: x
        else:
            self.report_progress = report_progress

    def _http_base(self):
        host = self.device_host or PREFS['host']
        return f'http://{host}'

    def _http_get_json(self, path, params=None, timeout=5):
        url = self._http_base() + path
        if params:
            url += '?' + urllib.parse.urlencode(params)
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                data = resp.read().decode('utf-8', 'ignore')
        except Exception as exc:
            raise ControlError(desc=f'HTTP request failed: {exc}')
        try:
            import json
            return json.loads(data)
        except Exception as exc:
            raise ControlError(desc=f'Invalid JSON response: {exc}')

    def _http_post_form(self, path, data, timeout=5):
        url = self._http_base() + path
        body = urllib.parse.urlencode(data).encode('utf-8')
        req = urllib.request.Request(url, data=body, method='POST')
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode('utf-8', 'ignore')
        except Exception as exc:
            raise ControlError(desc=f'HTTP request failed: {exc}')

    def config_widget(self):
        return CrossPointConfigWidget()

    def save_settings(self, config_widget):
        config_widget.save()

    def books(self, oncard=None, end_session=True):
        if oncard is not None:
            return []
        entries = self._http_get_json('/api/files', params={'path': '/'})
        books = []
        fetch_metadata = PREFS['fetch_metadata']
        for entry in entries:
            if entry.get('isDirectory'):
                continue
            if not entry.get('isEpub'):
                continue
            name = entry.get('name', '')
            if not name:
                continue
            size = entry.get('size', 0)
            lpath = '/' + name if not name.startswith('/') else name
            title = os.path.splitext(os.path.basename(name))[0]
            meta = Metadata(title, [])
            if fetch_metadata:
                try:
                    from calibre.customize.ui import quick_metadata
                    from calibre.ebooks.metadata.meta import get_metadata
                    with self._download_temp(lpath) as tf:
                        with quick_metadata:
                            m = get_metadata(tf, stream_type='epub', force_read_metadata=True)
                        if m is not None:
                            meta = m
                except Exception as exc:
                    self._log(f'[CrossPoint] metadata read failed for {lpath}: {exc}')
            book = Book('', lpath, size=size, other=meta)
            books.append(book)
        return books

    def sync_booklists(self, booklists, end_session=True):
        return None

    def card_prefix(self, end_session=True):
        return None, None

    def total_space(self, end_session=True):
        return 10 * 1024 * 1024 * 1024, 0, 0

    def free_space(self, end_session=True):
        return 10 * 1024 * 1024 * 1024, 0, 0

    def _convert_epub_to_baseline(self, filepath):
        from calibre.ptempfile import PersistentTemporaryFile
        from .baseline_jpeg import convert_epub_images

        quality = PREFS.get('jpeg_quality', 85)

        temp_file = PersistentTemporaryFile(suffix='.epub')
        temp_path = temp_file.name
        temp_file.close()

        try:
            import shutil
            shutil.copy2(filepath, temp_path)

            converted = convert_epub_images(
                temp_path,
                output_path=temp_path,
                quality=quality,
                logger=self._log
            )

            self._log(f'[CrossPoint] Converted {converted} images to baseline JPEG')
            return temp_path

        except Exception as exc:
            self._log(f'[CrossPoint] Baseline conversion failed: {exc}')
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return filepath

    def upload_books(self, files, names, on_card=None, end_session=True, metadata=None):
        host = self.device_host or PREFS['host']
        port = self.device_port or PREFS['port']
        upload_path = PREFS['path']
        chunk_size = PREFS['chunk_size']
        if chunk_size > 2048:
            self._log(f'[CrossPoint] chunk_size capped to 2048 (was {chunk_size})')
            chunk_size = 2048
        debug = PREFS['debug']
        convert_baseline = PREFS.get('convert_baseline_jpeg', True)

        paths = []
        total = len(files)
        temp_files = []

        try:
            for i, (infile, name) in enumerate(zip(files, names)):
                if hasattr(infile, 'read'):
                    filepath = getattr(infile, 'name', None)
                    if not filepath:
                        raise ControlError(desc='In-memory uploads are not supported')
                else:
                    filepath = infile

                if convert_baseline and filepath.lower().endswith('.epub'):
                    self.report_progress(i / float(total), f'Converting images in {os.path.basename(name)}...')
                    converted_path = self._convert_epub_to_baseline(filepath)
                    if converted_path != filepath:
                        temp_files.append(converted_path)
                        filepath = converted_path

                filename = os.path.basename(name)
                lpath = upload_path
                if not lpath.startswith('/'):
                    lpath = '/' + lpath
                if lpath != '/' and lpath.endswith('/'):
                    lpath = lpath[:-1]
                if lpath == '/':
                    lpath = '/' + filename
                else:
                    lpath = lpath + '/' + filename

                def _progress(sent, size):
                    if size > 0:
                        self.report_progress((i + sent / float(size)) / float(total),
                                             'Transferring books to device...')

                ws_client.upload_file(
                    host,
                    port,
                    upload_path,
                    filename,
                    filepath,
                    chunk_size=chunk_size,
                    debug=debug,
                    progress_cb=_progress,
                    logger=self._log,
                )
                paths.append((lpath, os.path.getsize(filepath)))

        finally:
            for temp_path in temp_files:
                try:
                    if os.path.exists(temp_path):
                        os.remove(temp_path)
                except Exception:
                    pass

        self.report_progress(1.0, 'Transferring books to device...')
        return paths

    def add_books_to_metadata(self, locations, metadata, booklists):
        return

    def delete_books(self, paths, end_session=True):
        for path in paths:
            status, body = self._http_post_form('/delete', {'path': path, 'type': 'file'})
            if status != 200:
                raise ControlError(desc=f'Delete failed for {path}: {body}')
            self._log(f'[CrossPoint] deleted {path}')

    def remove_books_from_metadata(self, paths, booklists):
        def norm(p):
            if not p:
                return ''
            p = p.replace('\\', '/')
            if not p.startswith('/'):
                p = '/' + p
            return p

        def norm_name(p):
            if not p:
                return ''
            name = os.path.basename(p)
            try:
                import unicodedata
                name = unicodedata.normalize('NFKC', name)
            except Exception:
                pass
            name = name.replace('\u2019', "'").replace('\u2018', "'")
            return name.casefold()

        device_names = set()
        try:
            entries = self._http_get_json('/api/files', params={'path': '/'})
            on_device = set()
            for entry in entries:
                if entry.get('isDirectory'):
                    continue
                name = entry.get('name', '')
                if not name:
                    continue
                on_device.add(norm(name))
                on_device.add(norm('/' + name))
                device_names.add(norm_name(name))
            self._log(f'[CrossPoint] on-device list: {sorted(on_device)}')
        except Exception as exc:
            self._log(f'[CrossPoint] refresh list failed: {exc}')
            on_device = None

        removed = 0
        for bl in booklists:
            for book in tuple(bl):
                bpath = norm(getattr(book, 'path', ''))
                blpath = norm(getattr(book, 'lpath', ''))
                self._log(f'[CrossPoint] book paths: {bpath} | {blpath}')
                should_remove = False
                if on_device is not None:
                    if device_names:
                        if norm_name(bpath) not in device_names and norm_name(blpath) not in device_names:
                            should_remove = True
                    elif bpath and bpath not in on_device and blpath and blpath not in on_device:
                        should_remove = True
                else:
                    for path in paths:
                        target = norm(path)
                        target_name = os.path.basename(target)
                        if target == bpath or target == blpath:
                            should_remove = True
                        elif target_name and (os.path.basename(bpath) == target_name or os.path.basename(blpath) == target_name):
                            should_remove = True
                if should_remove:
                    bl.remove_book(book)
                    removed += 1
        if removed:
            self._log(f'[CrossPoint] removed {removed} items from device list')

    def get_file(self, path, outfile, end_session=True, this_book=None, total_books=None):
        url = self._http_base() + '/download'
        params = urllib.parse.urlencode({'path': path})
        try:
            with urllib.request.urlopen(url + '?' + params, timeout=10) as resp:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    outfile.write(chunk)
        except Exception as exc:
            raise ControlError(desc=f'Failed to download {path}: {exc}')

    def _download_temp(self, path):
        from calibre.ptempfile import PersistentTemporaryFile
        tf = PersistentTemporaryFile(suffix='.epub')
        self.get_file(path, tf)
        tf.flush()
        tf.seek(0)
        return tf

    def eject(self):
        self.is_connected = False

    def is_dynamically_controllable(self):
        return 'crosspoint'

    def start_plugin(self):
        return None

    def stop_plugin(self):
        self.is_connected = False
