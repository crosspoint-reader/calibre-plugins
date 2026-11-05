# EPUB position map, version 1

With **Split large chapters/paragraphs** enabled, the Calibre optimizer writes
UTF-8 JSON to `META-INF/crosspoint-position-map.json` in the optimized EPUB.
The source is the EPUB passed to the optimizer, before image and text rewrites.
The map is published in the same ZIP replacement as the text changes it
describes. A complete rollback of the text pass leaves an `unavailable` marker.

CrossInk v1.6.1 reads `sourceSpineMap` from `META-INF/x-locations.json`. It can
rewrite `DocFragment` and one element's sibling index, while keeping the rest
of the XPath and its character offset. Splitting a paragraph can change both
its descendant path and the character offset. The Calibre map has its own
entry and requires a firmware reader for this format.
[CrossInk mapper](https://github.com/uxjulia/CrossInk/blob/v1.6.1/lib/KOReaderSync/ProgressMapper.cpp#L232)
[CrossInk metadata reader](https://github.com/uxjulia/CrossInk/blob/v1.6.1/lib/Epub/Epub.cpp#L1765)

## Coordinates

- Spine indexes are zero-based and count every OPF `spine/itemref`, including
  `linear="no"`, in the default rendition. `DocFragment[N]` is one-based when
  the caller's fragment numbering follows that same spine.
- A text-node anchor is `(spine index, path relative to body, character offset)`.
  Element steps use case-sensitive XML local names and one-based indexes among
  siblings with that local name. The terminal `text()[N]` is the one-based
  index among nonempty immediate text nodes of that element. Include text
  before, between and after child nodes. Comments and processing instructions
  have no text of their own in the map.
- Text is decoded XML character data in document order, including whitespace
  and tails, with XML entity and line-ending processing. Coalesce adjacent
  character data and CDATA without an intervening node. Apply no additional
  whitespace folding or Unicode normalization. There are no separators added
  at element or fragment boundaries.
- Offsets and lengths count Unicode scalar values. An offset is zero-based
  and identifies a character. UTF-8 bytes, UTF-16 code units, grapheme indexes,
  layout positions and element-only anchors require conversion before using
  the map. Reject an anchor if its path or character offset cannot be resolved
  in these coordinates. An offset equal to a text node's length is outside
  that node.

## Fields and lookup

`version` is the integer `1`. In a map with ranges, `sourceText` is indexed by
source spine index. Each row is an ordered array of `[path, length]` pairs.
The sum of lengths before a pair is that node's start in the source body text
stream. This index lets firmware resolve source XML anchors using the optimized
archive alone. An empty row is a body with zero characters. A `null` row has no
verified correspondence.

`spine` is indexed by optimized spine index. Each row is `[sourceIndex,
sourceStart, length]` or `null`. A non-null row maps the whole optimized body
text stream to `[sourceStart, sourceStart + length)` in the corresponding
source stream. The ranges for a supported source item are in order, disjoint
and cover its text. A zero-length row owns no anchor. At a split boundary the
character at that offset belongs to the following nonempty fragment.

1. **Source to optimized.** Find the source path in `sourceText[sourceIndex]`.
   Add the preceding lengths to its character offset. Find the unique range
   for that source item containing this stream offset. Subtract `sourceStart`
   and resolve the resulting offset against the corresponding optimized XHTML.
2. **Optimized to source.** Resolve the optimized anchor to a stream offset in
   that XHTML. Use the range at the optimized spine index and add `sourceStart`.
   Find the text node containing the result in `sourceText[sourceIndex]` and
   subtract the lengths of preceding nodes.

For example, the source paragraph `One. Two.` splits into `One. ` and `Two.`
in separate spine files. The following source spine item contains `End.`.

```json
{
  "version": 1,
  "status": "mapped",
  "sourceText": [
    [["/p[1]/text()[1]", 9]],
    [["/p[1]/text()[1]", 4]]
  ],
  "spine": [[0, 0, 5], [0, 5, 4], [1, 0, 4]]
}
```

`(0, /p[1]/text()[1], 5)` maps to `(1, /p[1]/text()[1], 0)` at `T`. The
reverse lookup adds `5` and returns the original anchor. The first character
of `End.` moves from source spine index `1` to optimized spine index `2`.

## Status and failure handling

- `identity` means the spine order and the parsed body trees used to address
  text are unchanged. Non-ID attributes may differ. Ranges are still present.
- `mapped` means there is a verified row for every spine item. The source
  and optimized node paths can differ.
- `partial` means some source items have no verified mapping. Their source
  rows and corresponding optimized rows are `null`. Use only verified rows.
- `unavailable` has no range data. This includes a rollback of the entire text
  pass and a spine whose lineage the optimizer could not verify.

Treat a missing entry, an unsupported version or status, malformed data,
and out-of-bounds or ambiguous lookups as unavailable for the affected
position. Check the optimized spine size, the referenced indexes and lengths,
and the actual target body length before using a range. Missing data carries
no identity assertion. The optimizer only generates or refreshes the entry
when splitting is enabled. When a source lookup fails, reusing the incoming
XPath directly on the optimized rendition can silently resolve to other text.

The map translates positions between these two renditions. Matching them by
file hash, selecting a KOSync document key, and adapting positions from a
reader's normalized DOM are separate from this format.

## Reproduction

The local EPUB fixture runs with `python3 -m unittest discover -s tests -v`
from the repository root. The optional Gutenberg test uses the optimizer's
usual Pillow and lxml dependencies. Set `CROSSPOINT_TEST_GUTENBERG_EPUB` to the
downloaded [EPUB (older e-readers)](https://www.gutenberg.org/ebooks/11.epub.images)
and run the same command. The tested input had SHA-256
`e332bfce56f79897138d73e7b698dc59bd073d932acb87897606aa7b5df96dac`.

The X4 optimizer expanded the spine from 14 to 33 items. At source
`/body/DocFragment[4]/body/div[1]/p[1]/text()[1].1`, the text starts
“Curiouser and curiouser!” Applying that anchor directly to the optimized EPUB
lands at “Alice opened the door”. The map translates it to
`/body/DocFragment[5]/body/div[1]/p[1]/text()[1].1`, where the original text is
present. The test resolves both anchors from the archived XHTML and checks the
reverse translation.
