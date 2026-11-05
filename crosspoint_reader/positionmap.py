"""Build a source-to-optimized text position map for the EPUB being written."""

import json
import posixpath
from urllib.parse import unquote, urlsplit
from xml.etree import ElementTree as ET


MAP_PATH = 'META-INF/crosspoint-position-map.json'
UNAVAILABLE = b'{"version":1,"status":"unavailable"}'
OCF = '{urn:oasis:names:tc:opendocument:xmlns:container}'
OPF = '{http://www.idpf.org/2007/opf}'
XHTML = '{http://www.w3.org/1999/xhtml}'
XML_ID = '{http://www.w3.org/XML/1998/namespace}id'


def _spine(read, opf_name, opf=None):
    """Read the ordered spine of the default rendition, including linear=no."""
    container = ET.fromstring(read('META-INF/container.xml'))
    rootfile = container.find(f'./{OCF}rootfiles/{OCF}rootfile')
    if rootfile is None or rootfile.get('full-path') != opf_name:
        raise ValueError('splitter did not select the default rendition')
    package = ET.fromstring(read(opf_name) if opf is None else opf)
    items = package.findall(f'./{OPF}manifest/{OPF}item')
    manifest = {item.get('id'): item.get('href') for item in items}
    if None in manifest or len(manifest) != len(items):
        raise ValueError('missing or duplicate manifest IDs')
    names = []
    for itemref in package.findall(f'./{OPF}spine/{OPF}itemref'):
        href = urlsplit(manifest[itemref.get('idref')])
        if href.scheme or href.netloc or href.query or href.fragment or not href.path:
            raise ValueError('unsupported spine href')
        names.append(posixpath.normpath(posixpath.join(
            posixpath.dirname(opf_name), unquote(href.path, errors='strict'))))
    return names


def _body(raw):
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True, insert_pis=True))
    root = ET.fromstring(raw, parser=parser)
    if root.tag not in ('html', XHTML + 'html'):
        raise ValueError('not an XHTML document')
    bodies = root.findall(root.tag[:-4] + 'body')
    if len(bodies) != 1:
        raise ValueError('expected one XHTML body')
    return bodies[0]


def _text_nodes(element, path=''):
    """Yield body-relative text-node paths and decoded text in document order."""
    text_index = 0
    if element.text:
        text_index = 1
        yield path + '/text()[1]', element.text

    siblings = {}
    for child in element:
        if isinstance(child.tag, str):
            name = child.tag.rsplit('}', 1)[-1]
            siblings[name] = index = siblings.get(name, 0) + 1
            yield from _text_nodes(child, '%s/%s[%d]' % (path, name, index))
        if child.tail:
            text_index += 1
            yield '%s/text()[%d]' % (path, text_index), child.tail


def _structure(body):
    # Non-ID attributes may change during image and link optimization without
    # affecting the element or text positions in the body.
    for element in body.iter():
        for name in list(element.attrib):
            if name not in ('id', XML_ID):
                del element.attrib[name]
    body.tail = None
    return ET.tostring(body)


def build_position_map(source, entries, opf_name, original_opf, fragments, reverted):
    """Encode only ranges verified against the final archive entries.

    ``fragments`` records the ordered files emitted for each split spine file.
    Comparing the input, pre-split and final OPF spines guards against
    untracked changes in order.
    """
    try:
        source_spine = _spine(source.read, opf_name)
        original_spine = _spine(entries.__getitem__, opf_name, original_opf)
        optimized_spine = _spine(entries.__getitem__, opf_name)
    except (ET.ParseError, LookupError, ValueError):
        return UNAVAILABLE

    groups = [fragments.get(name, [name]) for name in source_spine]
    if (not source_spine or source_spine != original_spine
            or optimized_spine != [name for group in groups for name in group]):
        return UNAVAILABLE

    ranges, source_text = [], []
    identity = True
    for index, (name, group) in enumerate(zip(source_spine, groups)):
        try:
            if name in reverted:
                raise ValueError('text pass reverted')
            before = _body(source.read(name))
            after = [_body(entries[part]) for part in group]
            nodes = list(_text_nodes(before))
            texts = [''.join(text for _, text in _text_nodes(body)) for body in after]
            if ''.join(texts) != ''.join(text for _, text in nodes):
                raise ValueError('body text changed')
            if identity:
                identity = len(group) == 1 and _structure(before) == _structure(after[0])
        except (ET.ParseError, LookupError, RecursionError, ValueError):
            ranges.extend([None] * len(group))
            source_text.append(None)
            identity = False
            continue

        source_text.append([[path, len(text)] for path, text in nodes])
        start = 0
        for part in texts:
            ranges.append([index, start, len(part)])
            start += len(part)

    if all(entry is None for entry in ranges):
        return UNAVAILABLE
    if identity:
        status = 'identity'
    else:
        status = 'partial' if None in ranges else 'mapped'
    return json.dumps({'version': 1, 'status': status,
                       'sourceText': source_text, 'spine': ranges},
                      separators=(',', ':')).encode('utf-8')
