import os
import time
import urllib.error
import urllib.parse
import urllib.request

from calibre.devices.errors import ControlError
from calibre.devices.interface import DevicePlugin
from calibre.devices.usbms.deviceconfig import DeviceConfig
from calibre.devices.usbms.books import Book, BookList
from calibre.ebooks.metadata.book.base import Metadata

from . import ws_client
from .config import CrossPointConfigWidget, PREFS
from .log import add_log


class CrossPointDevice(DeviceConfig, DevicePlugin):
    name = 'CrossPoint Reader'
    gui_name = 'CrossPoint Reader'
    description = 'CrossPoint Reader wireless device'
    supported_platforms = ['windows', 'osx', 'linux']
    author = 'CrossPoint Reader'
    version = (0, 2, 4)

    # Invalid USB vendor info to avoid USB scans matching.
    VENDOR_ID = [0xFFFF]
    PRODUCT_ID = [0xFFFF]
    BCD = [0xFFFF]

    FORMATS = ['epub']
    ALL_FORMATS = ['epub']
    SUPPORTS_SUB_DIRS = True
    MUST_READ_METADATA = False
    MANAGES_DEVICE_PRESENCE = True
    DEVICE_PLUGBOARD_NAME = 'CROSSPOINT_READER'
    MUST_READ_METADATA = False
    SUPPORTS_DEVICE_DB = False
    # Disable Calibre's device cache so we always refresh from device.
    device_is_usb_mass_storage = False

    def __init__(self, path):
        super().__init__(path)
        self.is_connected = False
        self.device_host = None
        self.device_port = None
        self.device_model = None  # 'X3' | 'X4' from /api/status
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

    # Device discovery / presence
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
            self._detect_device_model()
            return self
        if debug:
            self._log('[CrossPoint] discovery failed')
        return None

    def _detect_device_model(self):
        """Query /api/status for the device model (X3/X4), like the web UI."""
        try:
            status = self._http_get_json('/api/status', timeout=4)
            model = (status or {}).get('device')
            if model in ('X3', 'X4'):
                self.device_model = model
                self._log(f'[CrossPoint] detected device model: {model}')
            else:
                self._log('[CrossPoint] /api/status returned no device model')
        except Exception as exc:
            self._log(f'[CrossPoint] device model detection failed: {exc}')
        return self.device_model

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

    def _device_id(self):
        """Stable id for the connected device, used to key the metadata cache."""
        host = self.device_host or PREFS['host']
        return 'crosspoint-' + host.replace('.', '-')

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
        req = urllib.request.Request(url, data=body, method='POST',
                                     headers={'Content-Type': 'application/x-www-form-urlencoded'})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode('utf-8', 'ignore')
        except Exception as exc:
            raise ControlError(desc=f'HTTP request failed: {exc}')

    def config_widget(self):
        # Runs on the GUI thread; ensure the summary bridge exists (idempotent).
        try:
            from . import summary as summary_ui
            summary_ui.ensure_bridge()
        except Exception:
            pass
        return CrossPointConfigWidget()

    def save_settings(self, config_widget):
        config_widget.save()

    def _list_files_recursive(self, path='/'):
        """Return a flat list of (lpath, size) for all EPUB files on device."""
        results = []
        try:
            entries = self._http_get_json('/api/files', params={'path': path})
        except Exception as exc:
            self._log(f'[CrossPoint] listing {path} failed: {exc}')
            return results
        for entry in entries:
            name = entry.get('name', '')
            if not name:
                continue
            if path == '/':
                entry_path = '/' + name
            else:
                entry_path = path + '/' + name
            if entry.get('isDirectory'):
                results.extend(self._list_files_recursive(entry_path))
            elif entry.get('isEpub'):
                results.append((entry_path, entry.get('size', 0)))
        return results

    def books(self, oncard=None, end_session=True):
        if oncard is not None:
            return BookList(None, None, None)
        from . import metadata_cache as mc
        file_list = self._list_files_recursive('/')
        bl = BookList(None, None, None)
        fetch_metadata = PREFS['fetch_metadata']
        device_id = self._device_id()
        seen = set()
        for lpath, size in file_list:
            key = self._normalize_device_path(lpath)
            seen.add(key)
            meta = None
            # Prefer the cache: a book sent from this machine is recognized
            # instantly (with its library uuid) so Calibre marks it on-device
            # without re-downloading. Size guards against a changed file.
            entry = mc.get_entry(device_id, key)
            if entry and entry.get('size') == size:
                meta = Metadata(
                    entry.get('title') or os.path.splitext(os.path.basename(lpath))[0],
                    list(entry.get('authors') or []))
                if entry.get('uuid'):
                    meta.uuid = entry['uuid']
            # Fall back to reading the EPUB (slow: downloads the file) only when
            # the user opted in and the cache had nothing usable.
            if meta is None and fetch_metadata:
                try:
                    from calibre.customize.ui import quick_metadata
                    from calibre.ebooks.metadata.meta import get_metadata
                    with self._download_temp(lpath) as tf:
                        with quick_metadata:
                            m = get_metadata(tf, stream_type='epub', force_read_metadata=True)
                        if m is not None:
                            meta = m
                            mc.put_many(device_id,
                                        [(key, mc.entry_from_metadata(size, m))])
                except Exception as exc:
                    self._log(f'[CrossPoint] metadata read failed for {lpath}: {exc}')
            if meta is None:
                meta = Metadata(os.path.splitext(os.path.basename(lpath))[0], [])
            book = Book('', lpath, size=size, other=meta)
            if getattr(meta, 'uuid', None):
                book.uuid = meta.uuid
            bl.add_book(book, replace_metadata=True)
        # Forget files that are no longer on the device.
        mc.prune(device_id, seen)
        return bl

    def sync_booklists(self, booklists, end_session=True):
        # No on-device metadata sync supported.
        return None

    def card_prefix(self, end_session=True):
        return None, None

    def total_space(self, end_session=True):
        return 10 * 1024 * 1024 * 1024, 0, 0

    def free_space(self, end_session=True):
        return 10 * 1024 * 1024 * 1024, 0, 0

    def _format_upload_path(self, mi, original_name):
        """Format an upload path using the send-to-device template.

        Returns (subdirs, filename) where subdirs is a list of directory
        components from the template (may be empty for flat templates).
        """
        try:
            from calibre.library.save_to_disk import config as sconfig, get_components
            from calibre.utils.filenames import ascii_filename

            template = self.save_template()
            if not template:
                template = sconfig().parse().send_template

            components = get_components(
                template, mi, -1, '%b %Y', 250,
                ascii_filename, to_lowercase=False,
                replace_whitespace=False, safe_format=True,
                last_has_extension=False,
            )

            components = [c.strip() for c in components if c and c.strip()]
            if not components:
                return [], original_name

            ext = os.path.splitext(original_name)[1]
            filename = components[-1] + ext
            subdirs = components[:-1]
            return subdirs, filename
        except Exception as exc:
            self._log(f'[CrossPoint] template format failed: {exc}')
            return [], original_name

    def _mkdir_on_device(self, name, path):
        """Create a directory on device via POST /mkdir.

        Silently ignores 400 errors (folder already exists).
        Uses urllib directly to avoid _http_post_form which wraps all
        errors as ControlError.
        """
        url = self._http_base() + '/mkdir'
        body = urllib.parse.urlencode({'name': name, 'path': path}).encode('utf-8')
        req = urllib.request.Request(url, data=body, method='POST',
                                     headers={'Content-Type': 'application/x-www-form-urlencoded'})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 400:
                self._log(f'[CrossPoint] mkdir ignored (already exists): {name} in {path}')
            else:
                raise ControlError(desc=f'mkdir failed for {name} in {path}: {exc}')
        except Exception as exc:
            raise ControlError(desc=f'mkdir failed for {name} in {path}: {exc}')

    def _ensure_dir(self, parent_path, subdirs):
        """Ensure subdirectories exist under parent_path on device.

        Creates each level individually (one mkdir per component) rather than
        relying on the device to build a deep path in a single recursive call.
        A single-level mkdir is the most reliable operation; nested templates
        like ``Fanfiction/Fandom/Series/title.epub`` failed when the whole path
        was sent at once. ``_mkdir_on_device`` ignores "already exists" (400),
        so re-creating existing ancestors is a no-op. Returns the full path.
        """
        current = parent_path
        for sub in subdirs:
            self._mkdir_on_device(sub, current)
            if current == '/':
                current = '/' + sub
            else:
                current = current + '/' + sub
        return current

    def upload_books(self, files, names, on_card=None, end_session=True, metadata=None):
        host = self.device_host or PREFS['host']
        port = self.device_port or PREFS['port']
        upload_path = PREFS['path']
        chunk_size = PREFS['chunk_size']
        if chunk_size > 2048:
            self._log(f'[CrossPoint] chunk_size capped to 2048 (was {chunk_size})')
            chunk_size = 2048
        debug = PREFS['debug']

        # Normalize base upload path
        base_path = upload_path
        if not base_path.startswith('/'):
            base_path = '/' + base_path
        if base_path != '/' and base_path.endswith('/'):
            base_path = base_path[:-1]

        optimize_enabled = bool(PREFS['optimize'])
        opt_profile = None
        summary_ui = None
        if optimize_enabled:
            from .optimizer import resolve_profile
            _, opt_profile = resolve_profile(PREFS['device_target'], self.device_model)
            # Open the live optimizer dialog up-front so the user sees steps stream.
            try:
                from . import summary as summary_ui
                summary_ui.begin('Optimizing %d book(s) for %s…' % (
                    len(files), opt_profile['label']))
            except Exception as exc:
                summary_ui = None
                self._log(f'[CrossPoint] could not open optimizer dialog: {exc}')

        paths = []
        summaries = []
        total = len(files)
        for i, (infile, name) in enumerate(zip(files, names)):
            if hasattr(infile, 'read'):
                filepath = getattr(infile, 'name', None)
                if not filepath:
                    raise ControlError(desc='In-memory uploads are not supported')
            else:
                filepath = infile
            filename = os.path.basename(name)
            subdirs = []
            if metadata and i < len(metadata) and not PREFS['send_to_root']:
                subdirs, filename = self._format_upload_path(metadata[i], filename)

            if subdirs:
                target_dir = self._ensure_dir(base_path, subdirs)
            else:
                target_dir = base_path

            if target_dir == '/':
                lpath = '/' + filename
            else:
                lpath = target_dir + '/' + filename

            # Optionally optimize the EPUB to a temp file before uploading.
            send_path = filepath
            opt_temp = None
            if optimize_enabled and filepath.lower().endswith('.epub'):
                step_cb = summary_ui.step if summary_ui is not None else None
                opt_temp, summary = self._optimize_book(filepath, opt_profile,
                                                        step_cb=step_cb)
                if opt_temp is not None:
                    send_path = opt_temp
                if summary is not None:
                    summaries.append(summary)
                if summary_ui is not None:
                    summary_ui.step('SEND', 'Uploading %s …' % filename)

            def _progress(sent, size):
                if size > 0:
                    self.report_progress((i + sent / float(size)) / float(total),
                                         'Transferring books to device...')

            try:
                ws_client.upload_file(
                    host,
                    port,
                    target_dir,
                    filename,
                    send_path,
                    chunk_size=chunk_size,
                    debug=debug,
                    progress_cb=_progress,
                    logger=self._log,
                )
                paths.append((lpath, os.path.getsize(send_path)))
            finally:
                if opt_temp is not None:
                    try:
                        os.remove(opt_temp)
                    except OSError:
                        pass

        self.report_progress(1.0, 'Transferring books to device...')

        if summary_ui is not None:
            try:
                summary_ui.finish({
                    'profile': opt_profile['label'] if opt_profile else '?',
                    'books': summaries,
                })
            except Exception as exc:
                self._log(f'[CrossPoint] could not finalize optimizer dialog: {exc}')

        return paths

    def _optimize_book(self, filepath, profile, step_cb=None):
        """Optimize an EPUB to a temp file. Returns (temp_path_or_None, summary_or_None).

        ``step_cb(tag, message)`` (optional) streams each step to the live dialog.
        On any failure the original file is used (temp_path is None) so a transfer
        is never blocked by optimization.
        """
        from calibre.ptempfile import PersistentTemporaryFile
        from .optimizer import optimize_epub, Options

        opts = Options(
            quality=PREFS['optimize_quality'],
            grayscale=PREFS['optimize_grayscale'],
            auto_crop=PREFS['optimize_auto_crop'],
        )

        def _step(tag, message):
            self._log(f'[CrossPoint][opt] {tag}: {message}')
            if step_cb is not None:
                try:
                    step_cb(tag, message)
                except Exception:
                    pass

        out_path = None
        try:
            tf = PersistentTemporaryFile(suffix='.epub')
            out_path = tf.name
            tf.close()
            summary = optimize_epub(filepath, out_path, profile, opts, log_fn=_step)
            return out_path, summary
        except Exception as exc:
            self._log(f'[CrossPoint] optimization failed for {os.path.basename(filepath)}: '
                      f'{exc} (sending original)')
            if out_path:
                try:
                    os.remove(out_path)
                except OSError:
                    pass
            return None, None

    def add_books_to_metadata(self, locations, metadata, booklists):
        from . import metadata_cache as mc
        self._log(f'[CrossPoint] add_books_to_metadata: {len(locations)} locations, '
                  f'{len(booklists)} booklists')
        device_id = self._device_id()
        cache_entries = []
        metadata = iter(metadata)
        for location in locations:
            info = next(metadata)
            lpath = location[0]
            length = location[1]
            book = Book('', lpath, size=length, other=info)
            if booklists:
                booklists[0].add_book(book, replace_metadata=True)
                self._log(f'[CrossPoint] added to booklist: {lpath}')
            else:
                self._log(f'[CrossPoint] WARNING: booklists empty, could not add {lpath}')
            # Remember this book's identity so it is recognized (and marked
            # on-device) on the next connect without re-reading the EPUB.
            cache_entries.append((self._normalize_device_path(lpath),
                                  mc.entry_from_metadata(length, info)))
        mc.put_many(device_id, cache_entries)


    @staticmethod
    def _normalize_device_path(p):
        """Normalize a path to the device's forward-slash, leading-slash form.

        Calibre stores device book paths using the local os.sep, so on Windows a
        freshly-sent book's path arrives here with backslashes (e.g.
        ``/\\Author\\Title.epub``). The device only knows forward-slash paths, so
        convert separators, collapse any resulting ``//``, and ensure a leading
        slash before talking to it.
        """
        if not p:
            return ''
        p = p.replace('\\', '/')
        while '//' in p:
            p = p.replace('//', '/')
        if not p.startswith('/'):
            p = '/' + p
        return p

    def delete_books(self, paths, end_session=True):
        import json as _json
        norm_paths = [self._normalize_device_path(p) for p in paths]
        self._log(f'[CrossPoint] deleting {len(norm_paths)} books: {norm_paths}')
        url = self._http_base() + '/delete'
        # Server expects form field 'paths' containing a JSON array string
        body = urllib.parse.urlencode({'paths': _json.dumps(norm_paths)}).encode('utf-8')
        req = urllib.request.Request(url, data=body, method='POST',
                                    headers={'Content-Type': 'application/x-www-form-urlencoded'})
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                self._log(f'[CrossPoint] delete OK: {resp.status}')
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode('utf-8', 'ignore') if exc.fp else ''
            self._log(f'[CrossPoint] delete error {exc.code}: {err_body}')
            raise ControlError(desc=f'Delete failed: {exc.code} {err_body}')
        except Exception as exc:
            raise ControlError(desc=f'Delete failed: {exc}')

    def remove_books_from_metadata(self, paths, booklists):
        from . import metadata_cache as mc
        norm = self._normalize_device_path
        deleted = set(norm(p) for p in paths)
        self._log(f'[CrossPoint] deleted paths: {sorted(deleted)}')

        removed = 0
        for bl in booklists:
            for book in tuple(bl):
                bpath = norm(getattr(book, 'path', ''))
                blpath = norm(getattr(book, 'lpath', ''))
                if bpath in deleted or blpath in deleted:
                    bl.remove_book(book)
                    removed += 1
        mc.remove_many(self._device_id(), deleted)
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
