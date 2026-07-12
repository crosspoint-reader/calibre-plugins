"""Text-side EPUB transformations for low-RAM CrossPoint firmware.

The device lays out one <p> at a time, holding every word of the paragraph in
parallel in-RAM vectors, and caches whole spine sections built from single
files. Two consequences for ~380KB-RAM hardware:

    * a single multi-KB paragraph OOMs the layout engine even in a tiny file
      (observed: ~6KB single-<p> crashed an X4; ~1.2KB is comfortable), and
    * spine files beyond ~10KB make section indexing fragile.

This module post-processes the optimizer's output zip:

    * split every <p> larger than PARA_LIMIT into ~PARA_TARGET-byte siblings,
      cutting at sentence boundaries (inline tags kept atomic),
    * split spine XHTML files whose <body> exceeds SPLIT_LIMIT into
      ~CHUNK_TARGET-byte files, expanding OPF manifest + spine accordingly,
    * remap href/src="...#fragment" references onto the chunk that now holds
      the anchor,
    * remove embedded fonts (files, manifest items, @font-face rules),
    * drop page-list navs (print page numbers, dead weight on-device).

Everything is best-effort: each transformation verifies that the visible text
is unchanged and falls back to the untouched input on any error, so a transfer
is never blocked or corrupted by this pass.
"""

import posixpath
import re
import zipfile

SPLIT_LIMIT = 9500     # split <body> content bigger than this
CHUNK_TARGET = 7000    # aim for files of this many bytes of body content
PARA_LIMIT = 1600      # split <p> whose inner content exceeds this
PARA_TARGET = 1200     # aim for paragraphs of this many bytes

VOID = ('meta', 'link', 'img', 'br', 'hr', 'image', 'input', 'col', 'source')
FONT_RE = re.compile(r'\.(otf|ttf|woff2?)$', re.IGNORECASE)
XHTML_RE = re.compile(r'\.(xhtml|html|htm)$', re.IGNORECASE)


# ---------------------------------------------------------------------------
# Markup tokenizing / chunking
# ---------------------------------------------------------------------------

def parse_nodes(s):
    """Split markup into a list of top-level balanced nodes (tags or text)."""
    nodes, i, n = [], 0, len(s)
    while i < n:
        if s[i] == '<':
            depth, j = 0, i
            while True:
                k = s.find('>', j)
                if k == -1:
                    raise ValueError('unterminated tag')
                tag = s[j:k + 1]
                name = re.match(r'</?(\w+)', tag)
                name = name.group(1).lower() if name else ''
                if tag.startswith('<!--'):
                    k = s.find('-->', j)
                    if k == -1:
                        raise ValueError('unterminated comment')
                    k += 2
                elif tag.startswith('</'):
                    depth -= 1
                elif not (tag.endswith('/>') or name in VOID
                          or tag.startswith(('<!', '<?'))):
                    # processing instructions (<?dp ...?>) and declarations
                    # never nest; everything else opens an element
                    depth += 1
                if depth <= 0:
                    break
                j = s.find('<', k)
                if j == -1:
                    raise ValueError('unbalanced markup')
            nodes.append(s[i:k + 1])
            i = k + 1
        else:
            j = s.find('<', i)
            j = n if j == -1 else j
            nodes.append(s[i:j])
            i = j
    return nodes


def split_text_sentences(text, target):
    """Split a text run into pieces of ~target bytes at sentence/space boundaries.

    Lossless: the boundary whitespace stays with the preceding piece, so
    ``''.join(pieces) == text`` always holds (pieces may later be re-joined
    inside one paragraph, where a dropped space would corrupt the text)."""
    pieces = []
    while len(text) > target:
        cut = text.rfind('. ', 0, target)
        if cut != -1:
            cut += 2
        else:
            cut = text.rfind(' ', 0, target) + 1
            if cut == 0:
                break
        pieces.append(text[:cut])
        text = text[cut:]
    pieces.append(text)
    return pieces


def split_big_paragraphs(html):
    """Rewrite every <p> bigger than PARA_LIMIT into several sibling <p> elements.
    Only inline content is split; a <p> containing block-level tags is left alone.
    Returns (new_html, changed)."""
    out, changed = [], False
    for node in parse_nodes(html):
        m = re.match(r'(<p\b[^>]*>)(.*)(</p>)$', node, re.S) if node.startswith('<p') else None
        if not m or len(m.group(2)) <= PARA_LIMIT or re.search(r'<(?:p|div|table|ul|ol|img)\b', m.group(2)):
            if node.startswith('<') and not node.startswith(('<p', '<!', '</')) \
               and len(node) > PARA_LIMIT:
                mm = re.match(r'(<(\w+)[^>]*>)(.*)(</\2>)$', node, re.S)
                if mm and mm.group(2) not in VOID:
                    inner, was = split_big_paragraphs(mm.group(3))
                    if was:
                        out.append(mm.group(1) + inner + mm.group(4))
                        changed = True
                        continue
            out.append(node)
            continue
        open_tag, inner, close_tag = m.groups()
        groups, cur, cur_len = [], [], 0

        def pflush():
            nonlocal cur, cur_len
            if ''.join(cur).strip():
                groups.append(''.join(cur))
            cur, cur_len = [], 0

        for tok in parse_nodes(inner):
            pieces = (split_text_sentences(tok, PARA_TARGET)
                      if not tok.startswith('<') and len(tok) > PARA_LIMIT else [tok])
            for piece in pieces:
                if cur_len + len(piece) > PARA_TARGET and cur_len > 0 and piece.strip():
                    pflush()
                cur.append(piece)
                cur_len += len(piece)
        pflush()
        if len(groups) < 2:
            out.append(node)
            continue
        cont_tag = re.sub(r'\s+id="[^"]*"', '', open_tag)
        out.append(''.join((open_tag if k == 0 else cont_tag) + g + close_tag
                           for k, g in enumerate(groups)))
        changed = True
    return ''.join(out), changed


def chunk_nodes(nodes, target):
    """Greedy-pack nodes into chunks <= ~target, recursing into big containers."""
    chunks, cur, cur_len = [], [], 0

    def flush():
        nonlocal cur, cur_len
        if ''.join(cur).strip():
            chunks.append(''.join(cur))
        cur, cur_len = [], 0

    for node in nodes:
        if len(node) > SPLIT_LIMIT and node.startswith('<'):
            m = re.match(r'<(\w+)[^>]*>', node)
            open_tag, close_tag = m.group(0), '</%s>' % m.group(1)
            inner = node[len(open_tag):-len(close_tag)]
            flush()
            for sub in chunk_nodes(parse_nodes(inner), target):
                chunks.append(open_tag + sub + close_tag)
            continue
        if len(node) > SPLIT_LIMIT:
            flush()
            for piece in split_text_sentences(node, target):
                chunks.append(piece)
            continue
        if cur_len + len(node) > target and cur_len > 0 and node.strip():
            flush()
        cur.append(node)
        cur_len += len(node)
    flush()
    return chunks


def split_xhtml_doc(doc, basename):
    """Return list of (relname, content); [] if no split needed/possible."""
    m = re.search(r'(<body[^>]*>)(.*)(</body>)', doc, re.S)
    if not m:
        return []
    body = m.group(2)
    if len(body) <= SPLIT_LIMIT:
        return []
    head = doc[:m.start(2)]
    tail = doc[m.end(2):]
    chunks = chunk_nodes(parse_nodes(body), CHUNK_TARGET)
    if len(chunks) < 2:
        return []
    base, dot, ext = basename.rpartition('.')
    out = []
    for k, chunk in enumerate(chunks):
        name = basename if k == 0 else '%s_ek%d.%s' % (base, k, ext)
        out.append((name, head + chunk + tail))
    return out


def visible_text(s):
    m = re.search(r'<body.*?>(.*)</body>', s, re.S)
    body = m.group(1) if m else s
    body = re.sub(r'<!--.*?-->', '', body, flags=re.S)
    return re.sub(r'\s+', ' ', re.sub(r'<[^>]*>', ' ', body)).strip()


# ---------------------------------------------------------------------------
# Zip-level pass
# ---------------------------------------------------------------------------

def _find_attr(tag, attr):
    m = re.search(r'\b%s="([^"]*)"' % attr, tag)
    return m.group(1) if m else None


def split_epub_text(path, log, profile=None, opts=None):
    """Rewrite the EPUB at ``path`` in place. Returns a small summary dict.

    ``log(tag, message)`` receives per-step events. ``profile``/``opts`` (from
    the optimizer) enable re-encoding of extracted data-URI images. Never
    raises: on any unexpected error the file is left as it was.
    """
    result = {'paras': 0, 'file_splits': 0, 'fonts': 0, 'dataimgs': 0}
    try:
        _split_epub_text(path, log, result, profile, opts)
    except Exception as exc:
        log('SPLIT-ERR', 'text splitting skipped: %s' % exc)
    return result


_DATA_URI_RE = re.compile(
    r'(src|xlink:href)="data:image/(png|jpe?g|gif|webp|bmp);base64,([^"]+)"',
    re.IGNORECASE)


def _extract_data_uris(entries, order, zname, doc, opf, opf_dir, counter, log, profile, opts):
    """Replace base64 data-URI images with real zip entries + manifest items.

    A multi-KB base64 attribute inside a spine file defeats both the image
    optimizer (which only sees zip entries) and the device's streaming HTML
    parser. Returns (new_doc, new_opf).
    """
    import base64

    zdir = posixpath.dirname(zname)

    def repl(m):
        attr, ext, b64 = m.group(1), m.group(2).lower(), m.group(3)
        try:
            raw = base64.b64decode(re.sub(r'\s+', '', b64))
        except Exception:
            return m.group(0)
        name = 'dataimg_%d' % counter[0]
        counter[0] += 1
        data, out_ext, mt = raw, ('jpg' if ext == 'jpeg' else ext), None
        if profile is not None and opts is not None:
            try:
                from .optimizer import process_image
                data, _meta = process_image(raw, profile, opts, name)
                out_ext, mt = 'jpg', 'image/jpeg'
            except Exception as exc:
                log('SPLIT-ERR', 'data-URI image kept raw (%s)' % exc)
        if mt is None:
            mt = 'image/jpeg' if out_ext in ('jpg', 'jpeg') else 'image/%s' % out_ext
        fname = '%s.%s' % (name, out_ext)
        new_zname = posixpath.join(zdir, fname) if zdir else fname
        entries[new_zname] = data
        order.append(new_zname)
        href = posixpath.relpath(new_zname, opf_dir) if opf_dir else new_zname
        item = '<item id="%s" href="%s" media-type="%s"/>' % (name, href, mt)
        nonlocal_opf[0] = re.sub(r'(</(?:\w+:)?manifest>)', item + r'\n  \1',
                                 nonlocal_opf[0], count=1)
        log('SPLIT', 'extracted %.1f KB data-URI image -> %s' % (len(raw) / 1024.0, fname))
        return '%s="%s"' % (m.group(1), fname)

    nonlocal_opf = [opf]
    new_doc = _DATA_URI_RE.sub(repl, doc)
    return new_doc, nonlocal_opf[0]


def _split_epub_text(path, log, result, profile=None, opts=None):
    zin = zipfile.ZipFile(path, 'r')
    entries = {}   # name -> bytes, insertion-ordered
    order = []
    for n in zin.namelist():
        if zin.getinfo(n).is_dir():
            continue
        entries[n] = zin.read(n)
        order.append(n)
    zin.close()

    opf_name = next((n for n in order if n.lower().endswith('.opf')), None)
    if not opf_name:
        log('SPLIT-ERR', 'no OPF found, text splitting skipped')
        return
    opf = entries[opf_name].decode('utf-8', 'replace')
    opf_dir = posixpath.dirname(opf_name)

    # Manifest: id -> (href, full item tag); attribute-order independent.
    items = {}
    for tag in re.findall(r'<(?:\w+:)?item\b[^>]*/?>', opf):
        iid, href = _find_attr(tag, 'id'), _find_attr(tag, 'href')
        if iid and href:
            items[iid] = (href, tag)
    spine_ids = [i for i in re.findall(r'<(?:\w+:)?itemref\b[^>]*\bidref="([^"]+)"', opf)]

    anchor_map = {}     # (basename_of_orig_href, frag) -> new basename
    chunk_origin = {}   # zip name of a chunk -> basename of the file it was split from

    for sid in spine_ids:
        if sid not in items:
            continue
        href, item_tag = items[sid]
        zname = posixpath.normpath(posixpath.join(opf_dir, href)) if opf_dir else posixpath.normpath(href)
        if zname not in entries or not XHTML_RE.search(zname):
            continue
        doc = entries[zname].decode('utf-8', 'replace')

        # Pass 0: pull base64 data-URI images out into real files.
        if 'data:image/' in doc:
            counter = [result['dataimgs']]
            doc, opf = _extract_data_uris(entries, order, zname, doc, opf, opf_dir,
                                          counter, log, profile, opts)
            result['dataimgs'] = counter[0]
            entries[zname] = doc.encode('utf-8')

        orig_text = visible_text(doc)

        # Pass 1: paragraph splitting, in place.
        m = re.search(r'(<body[^>]*>)(.*)(</body>)', doc, re.S)
        if m:
            try:
                new_body, changed = split_big_paragraphs(m.group(2))
            except Exception as exc:
                log('SPLIT-ERR', '%s: paragraph pass skipped (%s)' % (posixpath.basename(zname), exc))
                new_body, changed = m.group(2), False
            if changed:
                new_doc = doc[:m.start(2)] + new_body + doc[m.end(2):]
                if visible_text(new_doc) == orig_text:
                    doc = new_doc
                    entries[zname] = doc.encode('utf-8')
                    result['paras'] += 1
                else:
                    log('SPLIT-ERR', '%s: paragraph pass produced text diff, reverted'
                        % posixpath.basename(zname))

        # Pass 2: file splitting.
        try:
            pieces = split_xhtml_doc(doc, posixpath.basename(zname))
        except Exception as exc:
            log('SPLIT-ERR', '%s: file split skipped (%s)' % (posixpath.basename(zname), exc))
            pieces = []
        if not pieces:
            continue
        joined = ' '.join(visible_text(c) for _, c in pieces)
        if re.sub(r'\s+', ' ', joined).strip() != orig_text:
            log('SPLIT-ERR', '%s: file split produced text diff, reverted'
                % posixpath.basename(zname))
            continue

        zdir = posixpath.dirname(zname)
        hdir = posixpath.dirname(href)
        insert_at = order.index(zname)
        for k, (name, content) in enumerate(pieces):
            new_zname = posixpath.join(zdir, name) if zdir else name
            entries[new_zname] = content.encode('utf-8')
            chunk_origin[new_zname] = posixpath.basename(href)
            if k > 0:
                order.insert(insert_at + k, new_zname)
            for frag in re.findall(r'id="([^"]+)"', content):
                anchor_map[(posixpath.basename(href), frag)] = name

        new_items = []
        new_refs = []
        for k, (name, _) in enumerate(pieces):
            piece_href = posixpath.join(hdir, name) if hdir else name
            piece_id = sid if k == 0 else '%s_ek%d' % (sid, k)
            if k == 0:
                new_items.append(item_tag)  # original tag keeps id/href/properties
            else:
                new_items.append('<item id="%s" href="%s" media-type="application/xhtml+xml"/>'
                                 % (piece_id, piece_href))
            new_refs.append('<itemref idref="%s"/>' % piece_id)
        opf = opf.replace(item_tag, '\n    '.join(new_items), 1)
        opf = re.sub(r'<(?:\w+:)?itemref\b[^>]*\bidref="%s"[^>]*/?>' % re.escape(sid),
                     lambda _m: '\n    '.join(new_refs), opf, count=1)
        result['file_splits'] += 1
        log('SPLIT', '%s -> %d files (max %d B)' % (
            posixpath.basename(zname), len(pieces), max(len(c) for _, c in pieces)))

    # Remap fragment links onto the chunk holding the anchor; drop page-list navs.
    for n in list(order):
        if not (XHTML_RE.search(n) or n.lower().endswith('.ncx')):
            continue
        s = entries[n].decode('utf-8', 'replace')
        before = s

        if anchor_map:
            def remap(mm):
                pre, fname, frag = mm.group(1), mm.group(2), mm.group(3)
                new = anchor_map.get((posixpath.basename(fname), frag))
                if new and new != posixpath.basename(fname):
                    d = posixpath.dirname(fname)
                    fname = posixpath.join(d, new) if d else new
                return '%s%s#%s"' % (pre, fname, frag)

            s = re.sub(r'((?:href|src)=")([^"#]+)#([^"]+)"', remap, s)

            # Fragment-only refs ("#note1") are relative to the file they sit
            # in; when that file was split, the anchor may now live in a
            # sibling chunk, so resolve against the chunk's original name.
            self_origin = chunk_origin.get(n, posixpath.basename(n))

            def remap_local(mm):
                pre, frag = mm.group(1), mm.group(2)
                new = anchor_map.get((self_origin, frag))
                if new and new != posixpath.basename(n):
                    return '%s%s#%s"' % (pre, new, frag)
                return mm.group(0)

            s = re.sub(r'(href=")#([^"]+)"', remap_local, s)

        s = re.sub(r'<nav[^>]*epub:type="page-list".*?</nav>', '', s, flags=re.S)
        if s != before:
            entries[n] = s.encode('utf-8')

    # Remove embedded fonts and @font-face rules.
    for n in list(order):
        if FONT_RE.search(n):
            order.remove(n)
            del entries[n]
            result['fonts'] += 1
        elif n.lower().endswith('.css'):
            s = entries[n].decode('utf-8', 'replace')
            s2 = re.sub(r'@font-face\s*\{[^}]*\}\s*', '', s)
            if s2 != s:
                entries[n] = s2.encode('utf-8')
    if result['fonts']:
        opf = re.sub(r'\s*<(?:\w+:)?item\b[^>]*\.(?:otf|ttf|woff2?)[^>]*/?>', '', opf,
                     flags=re.IGNORECASE)
        log('SPLIT', 'removed %d embedded font(s)' % result['fonts'])

    entries[opf_name] = opf.encode('utf-8')

    tmp = path + '.split-tmp'
    with zipfile.ZipFile(tmp, 'w') as zout:
        if 'mimetype' in entries:
            zout.writestr('mimetype', entries['mimetype'], compress_type=zipfile.ZIP_STORED)
        for n in order:
            if n == 'mimetype':
                continue
            zout.writestr(n, entries[n], compress_type=zipfile.ZIP_DEFLATED)
        for n in entries:
            if n not in order and n != 'mimetype':
                zout.writestr(n, entries[n], compress_type=zipfile.ZIP_DEFLATED)

    import os
    os.replace(tmp, path)
    if result['paras']:
        log('SPLIT', 'split oversized paragraphs in %d file(s)' % result['paras'])
