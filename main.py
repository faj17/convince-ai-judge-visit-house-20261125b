#!/usr/bin/env python3
"""Validate entry.png and copy it to an output directory.

Stdlib only, no network. The entry is a single 1024x1024 palette PNG
(one hand-painted invitation letter). The checks below prove the file
is a complete, well-formed image before it is copied anywhere:

  * PNG magic, file size within [100 KiB, 1 MiB]
  * IHDR present first, 13 bytes, valid CRC, 1024x1024, 8-bit palette
    colour type 3, standard compression/filter, non-interlaced
  * every chunk's CRC verified while walking the file
  * PLTE present with at least 16 colours (a real painted image, not
    a blank or trivial one)
  * at least one IDAT chunk, and the concatenated IDAT payload
    decompresses to exactly one scanline set for 1024 rows
  * IEND is the final chunk with nothing trailing after it

The validated file is then copied to the output directory and the
copy's SHA-256 is compared against the source, so the bytes that
leave here are exactly the bytes that were checked.
"""
import argparse
import hashlib
import os
import shutil
import struct
import sys
import zlib

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
MAX_BYTES = 1024 * 1024
MIN_BYTES = 100 * 1024
MIN_PALETTE_COLORS = 16
EXPECTED_W = 1024
EXPECTED_H = 1024
EXPECTED_BIT_DEPTH = 8
EXPECTED_COLOR_TYPE = 3  # palette
EXPECTED_COMPRESSION = 0
EXPECTED_FILTER = 0
EXPECTED_INTERLACE = 0
IEND_CRC = 0xAE426082


def fail(msg):
    print(f"FAIL: {msg}", file=sys.stderr)
    sys.exit(1)


def sha256_of(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def read_chunks(data):
    """Walk the PNG chunk list, verifying every chunk's CRC.

    Returns a list of (type, data) tuples. Raises SystemExit via
    fail() on the first structural problem found.
    """
    chunks = []
    pos = 8
    while pos + 8 <= len(data):
        clen, ctype = struct.unpack(">I4s", data[pos:pos + 8])
        end = pos + 12 + clen
        if end > len(data):
            fail(f"chunk {ctype!r} at offset {pos} runs past end of file")
        cdata = data[pos + 8:pos + 8 + clen]
        crc_stored = struct.unpack(">I", data[pos + 8 + clen:end])[0]
        crc = zlib.crc32(ctype + cdata) & 0xFFFFFFFF
        if crc != crc_stored:
            fail(f"chunk {ctype!r} at offset {pos}: CRC mismatch "
                 f"(stored {crc_stored:08x}, computed {crc:08x})")
        chunks.append((ctype, cdata))
        pos = end
        if ctype == b"IEND":
            break
    if not chunks or chunks[-1][0] != b"IEND":
        fail("no trailing IEND chunk")
    if pos != len(data):
        fail(f"{len(data) - pos} trailing bytes after IEND")
    return chunks


def check_ihdr(ihdr):
    if len(ihdr) != 13:
        fail(f"IHDR is {len(ihdr)} bytes, expected 13")
    width, height, depth, ctype, comp, filt, inter = struct.unpack(
        ">IIBBBBB", ihdr)
    if (width, height) != (EXPECTED_W, EXPECTED_H):
        fail(f"dimensions {width}x{height}, expected "
             f"{EXPECTED_W}x{EXPECTED_H}")
    if depth != EXPECTED_BIT_DEPTH:
        fail(f"bit depth {depth}, expected {EXPECTED_BIT_DEPTH}")
    if ctype != EXPECTED_COLOR_TYPE:
        fail(f"colour type {ctype}, expected {EXPECTED_COLOR_TYPE} "
             f"(palette)")
    if comp != EXPECTED_COMPRESSION or filt != EXPECTED_FILTER:
        fail(f"compression {comp} / filter {filt}, expected "
             f"{EXPECTED_COMPRESSION} / {EXPECTED_FILTER}")
    if inter != EXPECTED_INTERLACE:
        fail(f"interlace method {inter}, expected "
             f"{EXPECTED_INTERLACE} (non-interlaced)")
    return width, height


def check_palette(chunks):
    palette = None
    for ctype, cdata in chunks:
        if ctype == b"PLTE":
            palette = cdata
            break
        if ctype == b"IDAT":
            break
    if palette is None:
        fail("palette PNG has no PLTE chunk before IDAT")
    if len(palette) % 3 != 0:
        fail(f"PLTE length {len(palette)} is not a multiple of 3")
    colors = len(palette) // 3
    if colors < MIN_PALETTE_COLORS:
        fail(f"palette has only {colors} colours, under the "
             f"{MIN_PALETTE_COLORS} minimum for a real image")
    return colors


def check_image_data(chunks, height, width):
    idat = b"".join(cdata for ctype, cdata in chunks if ctype == b"IDAT")
    if not idat:
        fail("no IDAT chunk")
    try:
        raw = zlib.decompress(idat)
    except zlib.error as exc:
        fail(f"IDAT payload does not decompress: {exc}")
    # Palette, 8-bit: one byte per pixel plus one filter byte per row.
    expected = height * (1 + width)
    if len(raw) != expected:
        fail(f"decompressed image data is {len(raw)} bytes, expected "
             f"{expected} for {width}x{height} palette rows")
    return len(raw)


def main():
    ap = argparse.ArgumentParser(description="Validate entry.png")
    ap.add_argument("--output", default="/output",
                    help="output directory (default /output)")
    ap.add_argument("--verbose", action="store_true",
                    help="print the SHA-256")
    ap.add_argument("--quiet", action="store_true",
                    help="suppress the OK line")
    args = ap.parse_args()

    src = "entry.png"
    if not os.path.isfile(src):
        fail(f"{src} not found")
    size = os.path.getsize(src)
    if size > MAX_BYTES:
        fail(f"{src} is {size} bytes, over the 1 MiB limit")
    if size < MIN_BYTES:
        fail(f"{src} is only {size} bytes, under the {MIN_BYTES} byte "
             f"floor for a real painted image")

    with open(src, "rb") as f:
        data = f.read()

    if data[:8] != PNG_MAGIC:
        fail("bad PNG magic")
    if len(data) < 8 + 25:
        fail("file too short for IHDR")

    chunks = read_chunks(data)
    if chunks[0][0] != b"IHDR":
        fail("first chunk is not IHDR")
    width, height = check_ihdr(chunks[0][1])
    check_palette(chunks)
    check_image_data(chunks, height, width)

    digest = sha256_of(src)
    os.makedirs(args.output, exist_ok=True)
    dst = os.path.join(args.output, "entry.png")
    shutil.copyfile(src, dst)
    if sha256_of(dst) != digest:
        fail("copy SHA-256 mismatch")
    if args.verbose:
        print(f"sha256 {digest}")
    if not args.quiet:
        print(f"OK entry.png {width}x{height} {size} bytes -> {dst}")


if __name__ == "__main__":
    main()
