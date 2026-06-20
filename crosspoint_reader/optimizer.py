"""EPUB optimizer for CrossPoint Reader.

This is a Python port of the client-side optimizer that ships in the CrossPoint
web server (``FilesPage.html``). It mirrors the "core" optimization path used by
the device's web UI when no manual per-image splitting is selected:

    * resolve a device profile (X4 = 480x800, X3 = 528x792, portrait short x long)
    * for every raster image inside the EPUB:
        - optional auto-crop of uniform margins
        - scale to fit the device screen (preserving aspect ratio)
        - flatten transparency onto white and convert to grayscale
        - re-encode as JPEG (quality 85 by default)
    * rewrite the container: rename raster images to ``.jpg``, strip stale
      width/height from ``<img>`` tags, unwrap SVG covers / SVG-wrapped images,
      fix OPF media-types + cover meta, sync the NCX identifier, inject a small
      defensive stylesheet, and re-zip mimetype-first.

The interactive H-split / V-split / rotate picker from the web UI is intentionally
out of scope (the plugin transfers automatically, with no per-image prompts).

All image work uses Pillow (PIL), which Calibre bundles. PIL's ``.convert('L')``
applies the exact ITU-R 601 luma weights (0.299/0.587/0.114) used by the web
optimizer, and EPUB markup is rewritten with lxml (also bundled), with regex
fallbacks throughout.
"""

import io
import os
import re
import time
import zipfile


# --- Device profiles (short edge x long edge, portrait) ---------------------
DEVICE_PROFILES = {
    'X4': {'width': 480, 'height': 800, 'label': 'X4'},
    'X3': {'width': 528, 'height': 792, 'label': 'X3'},
}
DEFAULT_DEVICE = 'X4'

DEFAULT_JPEG_QUALITY = 85

# Auto-crop tuning (mirrors the web optimizer constants).
CROP_WHITE_THRESHOLD = 245
CROP_BACKGROUND_TOLERANCE = 28
CROP_BACKGROUND_MAX_SPREAD = 24
CROP_EDGE_SAMPLE_SIZE = 12
CROP_PADDING_PX = 8
MIN_CROP_SAVINGS_RATIO = 0.08
MIN_COLOR_CROP_SAVINGS_RATIO = 0.20
MIN_CROP_DIMENSION = 240

RASTER_RE = re.compile(r'\.(png|gif|webp|bmp|jpe?g)$', re.IGNORECASE)
# Anchored: rename a whole path/filename or an isolated attribute value.
RENAME_RE = re.compile(r'\.(png|gif|webp|bmp|jpeg)$', re.IGNORECASE)
# Unanchored: rename references embedded inline (href/src/url(...)) in markup/CSS.
REF_RE = re.compile(r'\.(?:png|gif|webp|bmp|jpeg)(?=["\'\)\s#?>])', re.IGNORECASE)
XHTML_RE = re.compile(r'\.(xhtml|html|htm)$', re.IGNORECASE)

DEFENSIVE_STYLE = (
    '<style type="text/css">img,svg{max-width:100%;height:auto}'
    'body{overflow-wrap:break-word}table{max-width:100%;table-layout:fixed}'
    'pre,code{white-space:pre-wrap;word-wrap:break-word}*{box-sizing:border-box}</style>'
)

_COVER_NAME_RE = re.compile(
    r'(^|/)(cover|thumbnail|thumb|icon)[^/]*\.(jpe?g|png|gif|webp|bmp)$', re.IGNORECASE)


def resolve_profile(device_target, detected_device):
    """Resolve 'auto' | 'X4' | 'X3' (+ detected model) to a concrete profile dict."""
    if device_target and device_target.upper() in DEVICE_PROFILES:
        key = device_target.upper()
    else:  # 'auto' (or anything unexpected)
        key = (detected_device or '').upper()
        if key not in DEVICE_PROFILES:
            key = DEFAULT_DEVICE
    return key, DEVICE_PROFILES[key]


class Options(object):
    def __init__(self, quality=DEFAULT_JPEG_QUALITY, grayscale=True, auto_crop=False):
        self.quality = int(quality)
        self.grayscale = bool(grayscale)
        self.auto_crop = bool(auto_crop)


# ---------------------------------------------------------------------------
# Image processing (Pillow)
# ---------------------------------------------------------------------------
#
# Calibre bundles Pillow (PIL) but not numpy, so the per-pixel work uses PIL.
# Conveniently, PIL's ``.convert('L')`` uses the exact ITU-R 601 luma weights
# (0.299 R + 0.587 G + 0.114 B) that the CrossPoint web optimizer applies, so
# grayscale output matches the device's web UI.


def _flatten_white_rgb(im):
    """Composite any transparency onto a white background; return an RGB image."""
    from PIL import Image
    if im.mode in ('RGBA', 'LA') or (im.mode == 'P' and 'transparency' in im.info):
        im = im.convert('RGBA')
        bg = Image.new('RGB', im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[3])
        return bg
    if im.mode == 'RGB':
        return im
    return im.convert('RGB')


def _estimate_crop_background(rgb):
    """Sample 8 edge regions; return their mean colour, or None if non-uniform."""
    from PIL import ImageStat
    w, h = rgb.size
    sample = min(CROP_EDGE_SAMPLE_SIZE, w // 8, h // 8)
    if sample < 2:
        return None
    pts = [
        (0, 0), (w - sample, 0), (0, h - sample), (w - sample, h - sample),
        ((w - sample) // 2, 0), ((w - sample) // 2, h - sample),
        (0, (h - sample) // 2), (w - sample, (h - sample) // 2),
    ]
    means = []
    for x, y in pts:
        box = rgb.crop((x, y, x + sample, y + sample))
        means.append(ImageStat.Stat(box).mean[:3])
    avg = [sum(c) / len(means) for c in zip(*means)]
    spread = max(abs(m[i] - avg[i]) for m in means for i in range(3))
    if spread > CROP_BACKGROUND_MAX_SPREAD:
        return None
    return tuple(avg)


def _find_crop_box(rgb):
    """Return a PIL crop box (left, top, right, bottom) trimming uniform margins.

    Mirrors the web optimizer: detect a uniform edge background (else assume
    white), keep pixels where ANY channel differs beyond tolerance, pad, and
    require a minimum savings ratio. Returns None when cropping is not worth it.
    """
    from PIL import Image, ImageChops
    w, h = rgb.size
    bg = _estimate_crop_background(rgb)

    if bg is not None:
        solid = Image.new('RGB', (w, h), tuple(int(round(c)) for c in bg))
        diff = ImageChops.difference(rgb, solid)
        r, g, b = diff.split()
        per_pixel_max = ImageChops.lighter(ImageChops.lighter(r, g), b)
        mask = per_pixel_max.point(
            lambda v: 255 if v > CROP_BACKGROUND_TOLERANCE else 0)
    else:
        r, g, b = rgb.split()

        def below_white(ch):
            return ch.point(lambda v: 255 if v < CROP_WHITE_THRESHOLD else 0)

        mask = ImageChops.lighter(
            ImageChops.lighter(below_white(r), below_white(g)), below_white(b))

    bbox = mask.getbbox()
    if not bbox:
        return None
    left, top, right, bottom = bbox  # right/bottom are exclusive in PIL

    left = max(0, left - CROP_PADDING_PX)
    top = max(0, top - CROP_PADDING_PX)
    right = min(w, right + CROP_PADDING_PX)
    bottom = min(h, bottom + CROP_PADDING_PX)

    crop_w, crop_h = right - left, bottom - top
    saved_ratio = 1.0 - (crop_w * crop_h) / float(w * h)
    near_white = bg is not None and all(c >= CROP_WHITE_THRESHOLD for c in bg)
    min_ratio = (MIN_CROP_SAVINGS_RATIO if (bg is None or near_white)
                 else MIN_COLOR_CROP_SAVINGS_RATIO)
    if saved_ratio < min_ratio:
        return None
    return (left, top, right, bottom)


def _should_skip_auto_crop(image_path, w, h):
    if w < MIN_CROP_DIMENSION or h < MIN_CROP_DIMENSION:
        return True
    return bool(_COVER_NAME_RE.search(image_path or ''))


def process_image(data, profile, opts, image_path=''):
    """Run the core optimization on a single image.

    Returns (jpeg_bytes, meta) where meta has orig/final dims + sizes + flags.
    Raises on decode failure so the caller can fall back to the original bytes.
    """
    from PIL import Image

    im = Image.open(io.BytesIO(data))
    im.load()
    orig_w, orig_h = im.size
    orig_size = len(data)
    max_w, max_h = profile['width'], profile['height']

    src = im
    src_w, src_h = orig_w, orig_h
    cropped = False

    # --- optional auto-crop -------------------------------------------------
    if opts.auto_crop and not _should_skip_auto_crop(image_path, orig_w, orig_h):
        box = _find_crop_box(_flatten_white_rgb(im))
        if box is not None:
            src = im.crop(box)
            src_w, src_h = src.size
            cropped = True

    # --- scale to fit (preserve aspect ratio) -------------------------------
    fits = src_w <= max_w and src_h <= max_h
    if fits and not cropped:
        scaled = src
        final_w, final_h = src_w, src_h
    else:
        scale = min(max_w / float(src_w), max_h / float(src_h))
        final_w = max(1, int(round(src_w * scale)))
        final_h = max(1, int(round(src_h * scale)))
        if (final_w, final_h) == (src_w, src_h):
            scaled = src
        else:
            scaled = src.resize((final_w, final_h), Image.LANCZOS)

    # --- flatten onto white, grayscale, JPEG --------------------------------
    rgb = _flatten_white_rgb(scaled)
    out = rgb.convert('L') if opts.grayscale else rgb
    buf = io.BytesIO()
    out.save(buf, 'JPEG', quality=int(opts.quality), optimize=True)
    jpeg = buf.getvalue()

    meta = {
        'orig_w': orig_w, 'orig_h': orig_h, 'orig_size': orig_size,
        'final_w': final_w, 'final_h': final_h, 'final_size': len(jpeg),
        'cropped': cropped,
    }
    return jpeg, meta



# ---------------------------------------------------------------------------
# EPUB container rewriting
# ---------------------------------------------------------------------------

def _decode_text(raw):
    if raw[:3] == b'\xef\xbb\xbf':
        raw = raw[3:]
    try:
        return raw.decode('utf-8')
    except UnicodeDecodeError:
        head = raw[:512].decode('ascii', 'ignore')
        m = (re.search(r'encoding=["\']([^"\']+)["\']', head, re.IGNORECASE)
             or re.search(r'charset=["\']?([^"\'\s;]+)', head, re.IGNORECASE))
        enc = m.group(1) if m else 'windows-1252'
        try:
            return raw.decode(enc, 'replace')
        except (LookupError, UnicodeDecodeError):
            return raw.decode('iso-8859-1', 'replace')


def _renamed_path(path):
    """png/gif/webp/bmp/jpeg -> .jpg (jpg stays jpg)."""
    return RENAME_RE.sub('.jpg', path)


def _rename_basename_refs(text):
    """Rewrite raster-image extensions to .jpg inside hrefs/src/url() references."""
    return REF_RE.sub('.jpg', text)


def _strip_img_dims(text):
    """Remove width/height attributes from every <img> tag (regex fallback)."""
    def repl(m):
        tag = re.sub(r'\s+(?:width|height)\s*=\s*"[^"]*"', '', m.group(0),
                     flags=re.IGNORECASE)
        tag = re.sub(r"\s+(?:width|height)\s*=\s*'[^']*'", '', tag,
                     flags=re.IGNORECASE)
        return tag
    return re.sub(r'<img\b[^>]*>', repl, text, flags=re.IGNORECASE)


def _fix_xhtml(text, log):
    """Strip <img> width/height, unwrap SVG covers/images, rename refs, inject CSS."""
    fixes = 0
    # SVG cover / SVG-wrapped images: unwrap to a plain <img>.
    new_text, n = _unwrap_svg_images(text)
    if n:
        text = new_text
        fixes += n
        log('FIX', 'SVG images (%d)' % n)

    try:
        from lxml import etree
        parser = etree.XMLParser(recover=True, resolve_entities=False, huge_tree=True)
        root = etree.fromstring(text.encode('utf-8'), parser=parser)
        if root is not None:
            modified = False
            for img in root.iter('{http://www.w3.org/1999/xhtml}img'):
                if img.get('width') is not None:
                    del img.attrib['width']
                    modified = True
                if img.get('height') is not None:
                    del img.attrib['height']
                    modified = True
                src = img.get('src')
                if src and RENAME_RE.search(src):
                    img.set('src', RENAME_RE.sub('.jpg', src))
                    modified = True
            # also catch namespace-less <img> (malformed docs)
            for img in root.iter('img'):
                if img.get('width') is not None:
                    del img.attrib['width']
                    modified = True
                if img.get('height') is not None:
                    del img.attrib['height']
                    modified = True
                src = img.get('src')
                if src and RENAME_RE.search(src):
                    img.set('src', RENAME_RE.sub('.jpg', src))
                    modified = True
            if modified:
                text = etree.tostring(
                    root, encoding='unicode',
                    xml_declaration=False)
                if not text.lstrip().startswith('<?xml'):
                    text = '<?xml version="1.0" encoding="utf-8"?>\n' + text
    except Exception:
        # Regex fallback: rename refs and drop width/height on img tags.
        text = _rename_basename_refs(text)
        text = _strip_img_dims(text)

    # Inject the defensive stylesheet just before </head>.
    if '</head>' in text and DEFENSIVE_STYLE not in text:
        text = text.replace('</head>', DEFENSIVE_STYLE + '</head>', 1)

    return text, fixes


_SVG_IMG_RE = re.compile(
    r'<(?:svg:)?svg\b[^>]*>[\s\S]*?<(?:svg:)?image\b[^>]*?'
    r'(?:xlink:href|href)=["\']([^"\']+)["\'][\s\S]*?</(?:svg:)?svg>',
    re.IGNORECASE)


def _unwrap_svg_images(content):
    if '<svg' not in content or 'href' not in content:
        return content, 0
    count = [0]

    def repl(m):
        count[0] += 1
        return ('<img style="max-width:100%%;height:auto" src="%s" alt="" />'
                % RENAME_RE.sub('.jpg', m.group(1)))

    new = _SVG_IMG_RE.sub(repl, content)
    return new, count[0]


def _fix_opf(text, log):
    """Fix media-types for renamed images, drop svg properties, ensure cover meta."""
    try:
        from lxml import etree
        parser = etree.XMLParser(recover=True, resolve_entities=False, huge_tree=True)
        root = etree.fromstring(text.encode('utf-8'), parser=parser)
        if root is None:
            raise ValueError('opf parse failed')
        opf_ns = 'http://www.idpf.org/2007/opf'

        def items():
            for it in root.iter('{%s}item' % opf_ns):
                yield it
            for it in root.iter('item'):
                yield it

        seen = set()
        for it in items():
            if id(it) in seen:
                continue
            seen.add(id(it))
            href = it.get('href') or ''
            if RENAME_RE.search(href):
                it.set('href', RENAME_RE.sub('.jpg', href))
                it.set('media-type', 'image/jpeg')
            props = it.get('properties')
            if props and 'svg' in props.split():
                new_props = ' '.join(p for p in props.split() if p != 'svg').strip()
                if new_props:
                    it.set('properties', new_props)
                else:
                    del it.attrib['properties']

        _ensure_cover_meta_lxml(root, opf_ns, log)
        out = etree.tostring(root, encoding='unicode', xml_declaration=False)
        if not out.lstrip().startswith('<?xml'):
            out = '<?xml version="1.0" encoding="utf-8"?>\n' + out
        return out
    except Exception:
        # Regex fallback.
        text = re.sub(
            r'(<(?:\w+:)?item\b[^>]*href="[^"]+\.jpg"[^>]*)media-type="image/(?:png|gif|webp|bmp)"',
            r'\1media-type="image/jpeg"', text)
        text = _rename_basename_refs(text)
        text = re.sub(r'\s+svg(?=["\'\s>])', '', text)
        return text


def _ensure_cover_meta_lxml(root, opf_ns, log):
    def tag(name):
        return '{%s}%s' % (opf_ns, name)

    cover_id = None
    items = list(root.iter(tag('item'))) or list(root.iter('item'))
    for it in items:
        if 'cover-image' in (it.get('properties') or '') and \
                (it.get('media-type') or '').startswith('image/'):
            cover_id = it.get('id')
            break
    if not cover_id:
        for it in items:
            mt = (it.get('media-type') or '')
            if not mt.startswith('image/'):
                continue
            if 'cover' in (it.get('id') or '').lower() or \
                    'cover' in (it.get('href') or '').lower():
                cover_id = it.get('id')
                break
    if not cover_id:
        return

    metas = list(root.iter(tag('meta'))) or list(root.iter('meta'))
    for m in metas:
        if m.get('name') == 'cover':
            if m.get('content') != cover_id:
                m.set('content', cover_id)
                log('FIX', 'OPF cover meta')
            return
    metadata = None
    for md in root.iter(tag('metadata')):
        metadata = md
        break
    if metadata is None:
        for md in root.iter('metadata'):
            metadata = md
            break
    if metadata is None:
        return
    from lxml import etree
    new_meta = etree.SubElement(metadata, tag('meta'))
    new_meta.set('name', 'cover')
    new_meta.set('content', cover_id)
    log('FIX', 'OPF cover meta')


def _sync_ncx_identifier(text, identifier):
    if not identifier:
        return text
    return re.sub(
        r'(<meta\s+name=["\']dtb:uid["\']\s+content=)["\'][^"\']*["\']',
        lambda m: m.group(1) + '"%s"' % identifier, text, flags=re.IGNORECASE)


def _extract_identifier(opf_text):
    m = re.search(
        r'<(?:\w+:)?package[^>]*unique-identifier=["\']([^"\']+)["\']',
        opf_text, re.IGNORECASE)
    if m:
        uid = re.escape(m.group(1))
        m2 = re.search(
            r'<dc:identifier[^>]*id=["\']%s["\'][^>]*>([^<]+)</dc:identifier>' % uid,
            opf_text, re.IGNORECASE)
        if m2:
            return m2.group(1).strip()
    m3 = re.search(r'<dc:identifier[^>]*>([^<]+)<', opf_text, re.IGNORECASE)
    return m3.group(1).strip() if m3 else None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def optimize_epub(in_path, out_path, profile, opts, log_fn=None):
    """Optimize an EPUB at ``in_path``, writing the result to ``out_path``.

    ``log_fn(tag, message)`` receives per-step events (may be None).
    Returns a summary dict.
    """
    start = time.time()

    steps = []

    def log(tag, message):
        steps.append((tag, message))
        if log_fn is not None:
            try:
                log_fn(tag, message)
            except Exception:
                pass

    orig_size = os.path.getsize(in_path)
    summary = {
        'name': os.path.basename(in_path),
        'orig_size': orig_size,
        'new_size': orig_size,
        'images': 0,
        'cropped': 0,
        'errors': 0,
        'fixes': 0,
        'profile': profile['label'],
        'steps': steps,
        'elapsed': 0.0,
    }

    log('INFO', '%s (%s) — target %s %dx%d, quality %d%%, grayscale %s, auto-crop %s' % (
        summary['name'], _human(orig_size), profile['label'],
        profile['width'], profile['height'], opts.quality,
        'ON' if opts.grayscale else 'OFF', 'ON' if opts.auto_crop else 'OFF'))

    zin = zipfile.ZipFile(in_path, 'r')
    try:
        names = zin.namelist()
        renamed = {n: _renamed_path(n) for n in names if RENAME_RE.search(n)}
        opf_text = None
        identifier = None

        # First locate the OPF (for NCX identifier sync).
        for n in names:
            if n.lower().endswith('.opf'):
                opf_text = _decode_text(zin.read(n))
                identifier = _extract_identifier(opf_text)
                break

        with zipfile.ZipFile(out_path, 'w') as zout:
            # mimetype FIRST, stored uncompressed, per the OCF spec.
            if 'mimetype' in names:
                zout.writestr('mimetype', zin.read('mimetype'),
                              compress_type=zipfile.ZIP_STORED)

            for n in names:
                if n == 'mimetype':
                    continue
                info = zin.getinfo(n)
                if info.is_dir():
                    continue
                low = n.lower()
                data = zin.read(n)

                if RASTER_RE.search(low):
                    try:
                        jpeg, meta = process_image(data, profile, opts, n)
                        summary['images'] += 1
                        if meta['cropped']:
                            summary['cropped'] += 1
                        log('IMG', '%s (%dx%d %s) → %dx%d %s%s' % (
                            os.path.basename(n), meta['orig_w'], meta['orig_h'],
                            _human(meta['orig_size']), meta['final_w'], meta['final_h'],
                            _human(meta['final_size']),
                            ' [cropped]' if meta['cropped'] else ''))
                        zout.writestr(renamed.get(n, _renamed_path(n)), jpeg,
                                      compress_type=zipfile.ZIP_STORED)
                    except Exception as exc:
                        summary['errors'] += 1
                        log('IMG-ERR', '%s: %s (kept original)' % (
                            os.path.basename(n), exc))
                        zout.writestr(n, data, compress_type=zipfile.ZIP_STORED)
                elif XHTML_RE.search(low):
                    text = _decode_text(data)
                    text, fixes = _fix_xhtml(text, log)
                    summary['fixes'] += fixes
                    zout.writestr(n, text.encode('utf-8'),
                                  compress_type=zipfile.ZIP_DEFLATED)
                elif low.endswith('.opf'):
                    before = summary['fixes']
                    text = _decode_text(data)
                    text = _rename_basename_refs(text)
                    text = _fix_opf(text, log)
                    zout.writestr(n, text.encode('utf-8'),
                                  compress_type=zipfile.ZIP_DEFLATED)
                    summary['fixes'] = max(summary['fixes'], before)
                elif low.endswith('.ncx'):
                    text = _decode_text(data)
                    text = _rename_basename_refs(text)
                    new_text = _sync_ncx_identifier(text, identifier)
                    if new_text != text:
                        log('FIX', 'NCX identifier synced')
                        summary['fixes'] += 1
                    zout.writestr(n, new_text.encode('utf-8'),
                                  compress_type=zipfile.ZIP_DEFLATED)
                elif low.endswith('.css'):
                    text = _decode_text(data)
                    zout.writestr(n, _rename_basename_refs(text).encode('utf-8'),
                                  compress_type=zipfile.ZIP_DEFLATED)
                else:
                    zout.writestr(n, data, compress_type=zipfile.ZIP_DEFLATED)
    finally:
        zin.close()

    summary['new_size'] = os.path.getsize(out_path)
    summary['elapsed'] = time.time() - start
    saved = orig_size - summary['new_size']
    pct = (saved / float(orig_size) * 100.0) if orig_size else 0.0
    log('DONE', 'Optimized %d image(s), %d fix(es) — %s → %s (%+.0f%%) in %.1fs' % (
        summary['images'], summary['fixes'], _human(orig_size),
        _human(summary['new_size']), -pct, summary['elapsed']))
    return summary


def _human(n):
    n = float(n or 0)
    for unit in ('B', 'KB', 'MB', 'GB'):
        if n < 1024.0 or unit == 'GB':
            return '%.1f %s' % (n, unit) if unit != 'B' else '%d B' % int(n)
        n /= 1024.0
    return '%.1f GB' % n
