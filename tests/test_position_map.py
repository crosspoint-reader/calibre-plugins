import hashlib
import json
import os
import posixpath
import re
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.dom import Node, minidom
from zipfile import ZIP_STORED, ZipFile


# Import the actual optimizer without initializing the Calibre device driver.
plugin = types.ModuleType('test_crosspoint_reader')
plugin.__path__ = [str(Path(__file__).resolve().parents[1] / 'crosspoint_reader')]
sys.modules[plugin.__name__] = plugin
from test_crosspoint_reader import textsplit
from test_crosspoint_reader.optimizer import DEVICE_PROFILES, Options, optimize_epub


MAP_PATH = 'META-INF/crosspoint-position-map.json'
OPF_NS = 'http://www.idpf.org/2007/opf'
TINY_PNG = ('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABAQAAAAA3bvkkAAAACklEQVR4nGNgAAAAAgAB'
            'SK+kcQAAAABJRU5ErkJggg==')
ALICE_SHA256 = 'e332bfce56f79897138d73e7b698dc59bd073d932acb87897606aa7b5df96dac'


def write_fixture(path, *, long=True, first_prefix='', stale=False, indented=False, prefixed_opf=False):
    heading = '<h1>First chapter</h1>'
    indent = '\n  ' if indented else ''
    paragraph = '<p id="long">%s<span><em>%s</em></span> Tail.</p>' % (
        indent,
        'É &amp; 🐇 arrives. ' * 175 if long else 'One sentence.')
    filler = ''.join('<p>Block %d. %s</p>' % (i, 'The garden was quiet. ' * 47)
                     for i in range(10)) if long else ''

    def xhtml(body):
        return ('<html xmlns="http://www.w3.org/1999/xhtml" '
                'xmlns:epub="http://www.idpf.org/2007/ops"><head><title>Fixture</title></head>'
                '<body>' + indent + body + indent + '</body></html>')

    with ZipFile(path, 'w') as epub:
        epub.writestr('mimetype', 'application/epub+zip', compress_type=ZIP_STORED)
        epub.writestr('META-INF/container.xml', '''
<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container" version="1.0">
  <rootfiles><rootfile full-path="OPS/book.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>'''.strip())
        opf = '''
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="uid">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="uid">urn:example:position-map</dc:identifier>
    <dc:title>Position fixture</dc:title><dc:language>en</dc:language>
    <meta property="dcterms:modified">2026-01-01T00:00:00Z</meta>
  </metadata>
  <manifest>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
    <item id="first" href="first.xhtml" media-type="application/xhtml+xml"/>
    <item id="next" href="next.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine><itemref idref="first"/><itemref idref="next" linear="no"/></spine>
</package>'''.strip()
        if prefixed_opf:
            opf = opf.replace('xmlns=', 'xmlns:opf=', 1)
            opf = opf.replace('<itemref idref="first"/>',
                              '<itemref id="start" idref="first" linear="no" '
                              'properties="rendition:layout-reflowable"></itemref>')
            opf = opf.replace('idref="next" linear="no"', 'idref="next"')
            opf = re.sub(r'<(/?)(package|metadata|meta|manifest|item|spine|itemref)(?=[\s/>])',
                         r'<\1opf:\2', opf)
        epub.writestr('OPS/book.opf', opf)
        epub.writestr('OPS/nav.xhtml', xhtml('<nav epub:type="toc"><ol>'
                      '<li><a href="first.xhtml">First</a></li>'
                      '<li><a href="next.xhtml">Next</a></li></ol></nav>'))
        epub.writestr('OPS/first.xhtml', xhtml('<main>' + heading + first_prefix + paragraph + filler + '</main>'))
        epub.writestr('OPS/next.xhtml', xhtml('<h1>Next chapter</h1><p>The next chapter opens here.</p>'))
        if stale:
            epub.writestr(MAP_PATH, '{"version":1,"status":"mapped","spine":[[9,99,1]]}')


def spine_documents(epub):
    container = minidom.parseString(epub.read('META-INF/container.xml'))
    opf_name = container.getElementsByTagName('rootfile')[0].getAttribute('full-path')
    opf = minidom.parseString(epub.read(opf_name))
    names = {node.getAttribute('id'): node.getAttribute('href')
             for node in opf.getElementsByTagNameNS(OPF_NS, 'item')}
    return [posixpath.join(posixpath.dirname(opf_name), names[node.getAttribute('idref')])
            for node in opf.getElementsByTagNameNS(OPF_NS, 'itemref')]


def dom_text_nodes(epub):
    """Read addresses independently from the archived XHTML DOM."""
    def walk(element, path):
        counts = {}
        for node in element.childNodes:
            if node.nodeType not in (Node.ELEMENT_NODE, Node.TEXT_NODE):
                continue
            step = node.localName if node.nodeType == Node.ELEMENT_NODE else 'text()'
            counts[step] = counts.get(step, 0) + 1
            address = '%s/%s[%d]' % (path, step, counts[step])
            if node.nodeType == Node.TEXT_NODE:
                yield address, node.data
            else:
                yield from walk(node, address)

    result = []
    for name in spine_documents(epub):
        doc = minidom.parseString(epub.read(name))
        doc.normalize()
        result.append(dict(walk(doc.getElementsByTagName('body')[0], '')))
    return result


def offset_for(nodes, path, char):
    start = 0
    for candidate, length in nodes:
        if candidate == path and 0 <= char < length:
            return start + char
        start += length
    raise ValueError('unknown text anchor')


def anchor_at(nodes, offset):
    for path, length in nodes:
        if 0 <= offset < length:
            return path, offset
        offset -= length
    raise ValueError('unknown text offset')


class PositionMapTest(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.source = Path(scratch.name) / 'source.epub'
        self.output = Path(scratch.name) / 'output.epub'

    def optimize(self, *, split=True):
        return optimize_epub(self.source, self.output, DEVICE_PROFILES['X4'], Options(split_text=split))

    def read_map(self):
        with ZipFile(self.output) as output:
            self.assertEqual(output.namelist().count(MAP_PATH), 1)
            return json.loads(output.read(MAP_PATH))

    def read_bodies(self):
        with ZipFile(self.source) as original, ZipFile(self.output) as optimized:
            return dom_text_nodes(original), dom_text_nodes(optimized)

    def assert_ranges(self, position_map, source, optimized):
        self.assertEqual(position_map['version'], 1)
        self.assertEqual(len(position_map['spine']), len(optimized))
        self.assertEqual(len(position_map['sourceText']), len(source))
        for index, nodes in enumerate(position_map['sourceText']):
            if nodes is not None:
                self.assertEqual(nodes, [[path, len(text)] for path, text in source[index].items()])
        next_offset = [0] * len(source)
        for nodes, record in zip(optimized, position_map['spine']):
            if record is None:
                continue
            index, start, length = record
            self.assertEqual(start, next_offset[index])
            final_text = ''.join(nodes.values())
            self.assertEqual(len(final_text), length)
            self.assertEqual(final_text, ''.join(source[index].values())[start:start + length])
            next_offset[index] += length
        for index, nodes in enumerate(position_map['sourceText']):
            if nodes is not None:
                self.assertEqual(next_offset[index], sum(map(len, source[index].values())))

    def assert_round_trip(self, position_map, source, optimized, anchor):
        index, path, char = anchor
        source_nodes = position_map['sourceText'][index]
        offset = offset_for(source_nodes, path, char)
        (target, local), = [(i, offset - entry[1]) for i, entry in enumerate(position_map['spine'])
                           if entry and entry[0] == index and entry[1] <= offset < entry[1] + entry[2]]
        target_nodes = [(p, len(text)) for p, text in optimized[target].items()]
        new_path, new_char = anchor_at(target_nodes, local)
        self.assertEqual(source[index][path][char], optimized[target][new_path][new_char])
        reverse_index, start, _ = position_map['spine'][target]
        reverse_offset = start + offset_for(target_nodes, new_path, new_char)
        self.assertEqual((reverse_index, *anchor_at(source_nodes, reverse_offset)), anchor)
        return target, new_path, new_char

    def test_chapter_and_nested_paragraph_splits(self):
        write_fixture(self.source)
        self.optimize()
        position_map = self.read_map()
        source, optimized = self.read_bodies()
        self.assertEqual(position_map['status'], 'mapped')
        self.assert_ranges(position_map, source, optimized)
        self.assertGreater(len(optimized), 2)

        _, second_start, _ = position_map['spine'][1]
        second_fragment = (0, *anchor_at(position_map['sourceText'][0], second_start + 13))
        self.assertEqual(self.assert_round_trip(position_map, source, optimized, second_fragment)[0], 1)
        fragment_boundary = (0, *anchor_at(position_map['sourceText'][0], second_start))
        self.assertEqual(self.assert_round_trip(position_map, source, optimized, fragment_boundary)[0], 1)

        before = '/main[1]/p[1]/span[1]/em[1]/text()[1]'
        after = '/main[1]/p[2]/span[1]/em[1]/text()[1]'
        cut = len(optimized[0][before])
        self.assertEqual(self.assert_round_trip(position_map, source, optimized, (0, before, cut - 1)),
                         (0, before, cut - 1))
        self.assertEqual(self.assert_round_trip(position_map, source, optimized, (0, before, cut)),
                         (0, after, 0))
        self.assert_round_trip(position_map, source, optimized, (0, before, 4))  # non-BMP code point
        self.assertEqual(self.assert_round_trip(position_map, source, optimized, (1, '/p[1]/text()[1]', 4)),
                         (len(optimized) - 1, '/p[1]/text()[1]', 4))

    def test_disabled_leaves_structure_and_adds_no_map(self):
        write_fixture(self.source)
        self.optimize(split=False)
        with ZipFile(self.output) as output:
            self.assertNotIn(MAP_PATH, output.namelist())
        source, optimized = self.read_bodies()
        self.assertEqual(source, optimized)

    def test_indented_chapter_preserves_body_and_inline_whitespace(self):
        write_fixture(self.source, indented=True)
        self.optimize()
        position_map = self.read_map()
        source, optimized = self.read_bodies()
        self.assertEqual(position_map['status'], 'mapped')
        self.assertGreater(len(optimized), 2)
        self.assert_ranges(position_map, source, optimized)
        for path in ('/text()[1]', '/main[1]/p[1]/text()[1]', '/text()[2]'):
            self.assertEqual(source[0][path], '\n  ')
            self.assert_round_trip(position_map, source, optimized, (0, path, 1))

    def test_split_preserves_opf_namespace_and_spine_attributes(self):
        write_fixture(self.source, prefixed_opf=True,
                      first_prefix='<img src="data:image/png;base64,%s" alt=""/>' % TINY_PNG)
        self.optimize()
        position_map = self.read_map()
        self.assertEqual(position_map['status'], 'mapped')
        self.assert_ranges(position_map, *self.read_bodies())

        with ZipFile(self.output) as epub:
            opf = minidom.parseString(epub.read('OPS/book.opf'))
            manifest_ids = {node.getAttribute('id') for node in opf.getElementsByTagNameNS(OPF_NS, 'item')}
        self.assertIn('dataimg_0', manifest_ids)
        refs = opf.getElementsByTagNameNS(OPF_NS, 'itemref')
        self.assertGreater(len(refs), 2)
        self.assertEqual(refs[0].getAttribute('id'), 'start')
        self.assertEqual(refs[-1].getAttribute('idref'), 'next')
        self.assertEqual(refs[-1].getAttribute('linear'), '')
        for ref in refs[:-1]:
            self.assertEqual(ref.getAttribute('linear'), 'no')
            self.assertEqual(ref.getAttribute('properties'), 'rendition:layout-reflowable')
        self.assertTrue(all(not ref.hasAttribute('id') for ref in refs[1:-1]))

    def test_identity_with_splitting_enabled(self):
        write_fixture(self.source, long=False)
        self.optimize()
        position_map = self.read_map()
        self.assertEqual(position_map['status'], 'identity')
        self.assert_ranges(position_map, *self.read_bodies())

    def test_archive_rollback_discards_stale_and_unpublished_ranges(self):
        write_fixture(self.source, stale=True)
        with patch.object(textsplit.os, 'replace', side_effect=OSError('publish failed')) as replace:
            result = self.optimize()
        replace.assert_called_once()
        self.assertFalse(Path(replace.call_args.args[0]).exists())
        self.assertIn(('SPLIT-ERR', 'text splitting skipped: publish failed'), result['steps'])
        self.assertEqual(self.read_map(), {'version': 1, 'status': 'unavailable'})
        source, optimized = self.read_bodies()
        self.assertEqual(source, optimized)

    def test_reverted_paragraphs_are_unmapped(self):
        write_fixture(self.source)
        original = textsplit.split_big_paragraphs

        def change_text(html):
            if '<main>' in html:
                return html.replace('First chapter', 'Different title'), True
            return original(html)

        with patch.object(textsplit, 'split_big_paragraphs', side_effect=change_text):
            self.optimize()
        position_map = self.read_map()
        self.assertEqual(position_map['status'], 'partial')
        self.assertIsNone(position_map['sourceText'][0])
        self.assertTrue(all(entry is None for entry in position_map['spine'][:-1]))
        self.assert_ranges(position_map, *self.read_bodies())

    def test_earlier_svg_rewrite_is_accounted_for(self):
        for caption, status in (('', 'mapped'), ('<title>Lost caption</title>', 'partial')):
            with self.subTest(status=status):
                svg = ('<svg xmlns="http://www.w3.org/2000/svg">%s'
                       '<image href="data:image/png;base64,%s"/></svg>') % (caption, TINY_PNG)
                write_fixture(self.source, long=False, first_prefix=svg)
                self.optimize()
                position_map = self.read_map()
                self.assertEqual(position_map['status'], status)
                if caption:
                    self.assertIsNone(position_map['sourceText'][0])
                    self.assertIsNone(position_map['spine'][0])
                self.assert_ranges(position_map, *self.read_bodies())

    @unittest.skipUnless(os.environ.get('CROSSPOINT_TEST_GUTENBERG_EPUB'), 'optional Gutenberg EPUB')
    def test_gutenberg_alice(self):
        self.source = Path(os.environ['CROSSPOINT_TEST_GUTENBERG_EPUB'])
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), ALICE_SHA256)
        result = self.optimize()
        self.assertGreater(result['images'], 0)
        self.assertEqual(result['errors'], 0)
        position_map = self.read_map()
        source, optimized = self.read_bodies()
        self.assertEqual((len(source), len(optimized)), (14, 33))
        self.assertEqual(position_map['status'], 'mapped')
        self.assert_ranges(position_map, source, optimized)

        path = '/div[1]/p[1]/text()[1]'
        self.assertTrue(source[3][path][1:].startswith('“Curiouser and curiouser!”'))
        self.assertTrue(optimized[3][path][1:].startswith('Alice opened the door'))
        self.assertEqual(self.assert_round_trip(position_map, source, optimized, (3, path, 1)),
                         (4, path, 1))
        self.assertEqual(source[3][path], optimized[4][path])
