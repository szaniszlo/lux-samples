#!/usr/bin/env python3
"""Read the 40x25 text screen back off a VICE screenshot and check it.

    screen.py --name hello --chargen <chargen.bin> [--expect hello.expect]
              [--log vice.log] [-o hello.txt] hello.png

A headless x64sc can be told to save a screenshot as it exits, but not to dump
memory, so the picture is the only record of what the program left on screen.
Each 8x8 cell is matched against the character ROM, which makes the result
exact for the stock fonts; a cell showing anything else (a custom charset,
sprites, bitmap mode) comes out as `?`.

Exit status is 0 if the screenshot exists and every non-blank line of the
--expect file occurs somewhere on screen, 1 otherwise.
"""
import argparse
import struct
import sys
import zlib

COLS, ROWS = 40, 25
# Where the text area starts in x64sc's PAL screenshot (384x272 with borders).
ORIGIN = (32, 35)


def read_png(path):
    """Decode an 8-bit RGB or RGBA PNG into (width, height, rows of RGB tuples).
    Hand-rolled so the check needs nothing beyond the standard library."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("not a PNG file")
    pos, idat, header = 8, b"", None
    while pos < len(data):
        length, kind = struct.unpack(">I4s", data[pos:pos + 8])
        body = data[pos + 8:pos + 8 + length]
        if kind == b"IHDR":
            header = struct.unpack(">IIBBBBB", body)
        elif kind == b"IDAT":
            idat += body
        pos += 12 + length
    width, height, depth, colour, _, _, interlace = header
    if depth != 8 or colour not in (2, 6) or interlace:
        raise ValueError(f"unsupported PNG (depth {depth}, colour type {colour})")
    bpp = 3 if colour == 2 else 4
    raw = zlib.decompress(idat)
    stride = width * bpp
    rows, prev = [], bytearray(stride)
    for y in range(height):
        start = y * (stride + 1)
        filt, line = raw[start], bytearray(raw[start + 1:start + 1 + stride])
        for i in range(stride):
            a = line[i - bpp] if i >= bpp else 0
            b = prev[i]
            c = prev[i - bpp] if i >= bpp else 0
            if filt == 1:
                line[i] = (line[i] + a) & 255
            elif filt == 2:
                line[i] = (line[i] + b) & 255
            elif filt == 3:
                line[i] = (line[i] + (a + b) // 2) & 255
            elif filt == 4:
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pred = a if pa <= pb and pa <= pc else b if pb <= pc else c
                line[i] = (line[i] + pred) & 255
        rows.append([tuple(line[x * bpp:x * bpp + 3]) for x in range(width)])
        prev = line
    return width, height, rows


def to_ascii(code, lowercase):
    """Screen code -> the ASCII character it shows, or `.` for graphics."""
    code &= 0x7F                      # reverse video shows the same character
    if code == 0:
        return "@"
    if 1 <= code <= 26:
        return chr((0x60 if lowercase else 0x40) + code)
    if 27 <= code <= 31:
        return "[.]^."[code - 27]     # pound and left-arrow have no ASCII form
    if 32 <= code <= 63:
        return chr(code)
    if lowercase and 65 <= code <= 90:
        return chr(code)
    return "."


def decode(rows, chargen, origin):
    """Return the screen as 25 strings, using whichever font explains it best."""
    ox, oy = origin
    area = [rows[oy + y][ox:ox + COLS * 8] for y in range(ROWS * 8)]
    counts = {}
    for line in area:
        for px in line:
            counts[px] = counts.get(px, 0) + 1
    background = max(counts, key=counts.get)

    best = None
    for lowercase in (False, True):
        base = 2048 if lowercase else 0
        glyphs = {}
        for code in range(255, -1, -1):           # lowest code wins a duplicate
            glyphs[bytes(chargen[base + code * 8:base + code * 8 + 8])] = code
        text, known = [], 0
        for row in range(ROWS):
            out = []
            for col in range(COLS):
                cell = [area[row * 8 + y][col * 8:col * 8 + 8] for y in range(8)]
                colours = {px for line in cell for px in line}
                if colours == {background}:
                    out.append(" ")
                    continue
                # Foreground is whatever is not the screen background; in a cell
                # with no background at all, either colour may be it.
                candidates = [c for c in colours if c != background] or list(colours)
                code = None
                if len(colours) <= 2:
                    for fg in candidates:
                        bits = bytes(sum(0x80 >> x for x in range(8) if line[x] == fg)
                                     for line in cell)
                        code = glyphs.get(bits)
                        if code is not None:
                            break
                if code is None:
                    out.append("?")
                elif code & 0x7F == 0x20:
                    out.append("#" if code & 0x80 else " ")   # reversed space: a block
                    known += 1
                else:
                    out.append(to_ascii(code, lowercase))
                    known += 1
            text.append("".join(out).rstrip())
        if best is None or known > best[0]:
            best = (known, text)
    return best[1]


def main():
    ap = argparse.ArgumentParser(description="Decode and check a VICE screenshot.")
    ap.add_argument("png")
    ap.add_argument("--name", default="screen")
    ap.add_argument("--chargen", help="C64 character ROM (chargen-901225-01.bin)")
    ap.add_argument("--expect", help="file of lines that must appear on screen")
    ap.add_argument("--log", help="emulator log, quoted when the run fails")
    ap.add_argument("-o", "--output", help="write the decoded screen here")
    args = ap.parse_args()

    def fail(reason):
        print(f"FAIL  {args.name}: {reason}")
        if args.log:
            print(f"      emulator log: {args.log}")
        sys.exit(1)

    try:
        _, _, rows = read_png(args.png)
    except FileNotFoundError:
        fail("the emulator wrote no screenshot")
    except (ValueError, zlib.error, struct.error, TypeError) as e:
        fail(f"unreadable screenshot: {e}")

    if not args.chargen:
        if args.expect:
            fail("no character ROM to read the screen with (set VICE_DATA)")
        print(f"RAN   {args.name}: screenshot at {args.png} (no character ROM, text not decoded)")
        return
    with open(args.chargen, "rb") as f:
        chargen = f.read()
    try:
        text = decode(rows, chargen, ORIGIN)
    except IndexError:
        fail("screenshot is not the size x64sc's PAL output should be")
    if args.output:
        with open(args.output, "w") as f:
            f.write("\n".join(text) + "\n")

    if not args.expect:
        print(f"RAN   {args.name}: no .expect file, screen saved to {args.output or args.png}")
        return
    with open(args.expect) as f:
        wanted = [line.strip() for line in f if line.strip()]
    missing = [w for w in wanted if not any(w in line for line in text)]
    if missing:
        print(f"FAIL  {args.name}: not on screen: " + "; ".join(repr(m) for m in missing))
        print("      screen was:")
        for line in text:
            if line:
                print(f"      | {line}")
        sys.exit(1)
    print(f"PASS  {args.name}: {len(wanted)} expected line(s) on screen")


if __name__ == "__main__":
    main()
