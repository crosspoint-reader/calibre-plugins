import os
import shutil
import tempfile

from calibre.ebooks.conversion.plugins.epub_output import EPUBOutput

from .config import PREFS
from .optimizer import Options, resolve_profile, optimize_epub


class _CrossPointEpubOutput(EPUBOutput):
    """Defines a generic OutputFormatPlugin for producing optimized EPUBs
    
    By using Calibre's file conversion tool and this class's two child classes,
    you can create and save EPUBs optimized for the Xteink X4 or X3"""
    device_name = None

    def _convert_optimized(self, oeb_book, output, input_plugin, opts, log):
        """Create a normal EPUB, then optimize it for the target device."""
        temporary_fd, temporary_path = tempfile.mkstemp(suffix='.epub')
        os.close(temporary_fd)
        optimized_path = None
        try:
            super().convert(oeb_book, temporary_path, input_plugin, opts, log)
            _, profile = resolve_profile(self.device_name, None)
            optimizer_options = Options(
                quality=PREFS['optimize_quality'],
                grayscale=PREFS['optimize_grayscale'],
                auto_crop=PREFS['optimize_auto_crop'],
                split_text=PREFS['optimize_split'],
            )
            log.info(
                '[CrossPoint %s] optimizer settings: quality=%d grayscale=%s '
                'auto_crop=%s split_text=%s' % (
                    self.device_name,
                    optimizer_options.quality,
                    optimizer_options.grayscale,
                    optimizer_options.auto_crop,
                    optimizer_options.split_text,
                ))
            optimized_fd, optimized_path = tempfile.mkstemp(suffix='.epub')
            os.close(optimized_fd)
            summary = optimize_epub(
                temporary_path,
                optimized_path,
                profile,
                optimizer_options,
                log_fn=lambda tag, message: log.info(
                    '[CrossPoint %s] %s: %s' % (self.device_name, tag, message)),
            )
            log.info(
                '[CrossPoint %s] optimizer result: images=%d errors=%d '
                'fixes=%d' % (
                    self.device_name,
                    summary['images'],
                    summary['errors'],
                    summary['fixes'],
                ))
            self._write_output(optimized_path, output)
        finally:
            for path in (temporary_path, optimized_path):
                if path:
                    try:
                        os.remove(path)
                    except OSError:
                        pass

    @staticmethod
    def _write_output(source_path, output):
        if hasattr(output, 'write'):
            with open(source_path, 'rb') as source:
                shutil.copyfileobj(source, output)
        else:
            shutil.copyfile(source_path, output)


class CrossPointEpubDispatcher(_CrossPointEpubOutput):
    """Handle Calibre's normalized ``epub`` key for compound output names.
    
    This class may seem unnecessary, but without this, one is still given the option
    of converting to an x3.epub and x4.epub, but when you press convert, Calibre
    will see that it needs to make a `foo.x3.epub` file and treat it like any other
    epub file."""
    name = 'CrossPoint EPUB Output Dispatcher'
    author = 'CrossPoint Reader'
    file_type = 'epub'
    commit_name = 'crosspoint_epub_output_dispatcher'

    def convert(self, oeb_book, output, input_plugin, opts, log):
        output_path = os.fspath(output).lower() if isinstance(output, (str, bytes, os.PathLike)) else ''
        if output_path.endswith('.x3.epub'):
            self.device_name = 'X3'
        elif output_path.endswith('.x4.epub'):
            self.device_name = 'X4'
        else:
            return super().convert(oeb_book, output, input_plugin, opts, log)
        return self._convert_optimized(oeb_book, output, input_plugin, opts, log)


class CrossPointX4EpubOutput(_CrossPointEpubOutput):
    name = 'CrossPoint X4 EPUB Output'
    author = 'CrossPoint Reader'
    file_type = 'x4.epub'
    commit_name = 'crosspoint_x4_epub_output'
    device_name = 'X4'


class CrossPointX3EpubOutput(_CrossPointEpubOutput):
    name = 'CrossPoint X3 EPUB Output'
    author = 'CrossPoint Reader'
    file_type = 'x3.epub'
    commit_name = 'crosspoint_x3_epub_output'
    device_name = 'X3'