#!/usr/bin/env python3
"""Build a .d64 from a disk manifest, or describe the manifests to Make.

    mkd64.py build --build build -o build/demo.d64 disks/demo.disk
    mkd64.py deps  --build build disks/*.disk

A manifest is one directive per line; files land in the directory in the order
they are written. `#` starts a comment and names with spaces are double-quoted.

    disk  "LUX SAMPLES" 2a           title and two-character id (optional line)
    prg   hello                      a program from src/, as build/hello.prg
    prg   dirlist "DIR"              ... under a different name on the disk
    seq   assets/data.bin "DATA"     a host file as a SEQ file, byte for byte
    usr   assets/data.bin "BLOB"     the same as a USR file
    text  assets/notes.txt "NOTES"   a SEQ file converted from ASCII to PETSCII
    raw   bpoke 18 0 2 65            any c1541 command, passed through verbatim

Host paths are relative to the directory make runs in. A file name defaults to
the host file's stem.

Names are written as unshifted PETSCII whatever their case here, so "NOTES" and
"notes" both read NOTES on a stock C64. Anything the named directives cannot
express (shifted characters, patched directory entries, sectors outside any
file) goes through `raw`, which reaches c1541's bwrite, bpoke and bfill.

This file is the place to grow directives for listing art, REL files and the
like. c1541 cannot create REL files, so that one will have to write its side
sectors here rather than delegate.
"""
import argparse
import os
import re
import shlex
import subprocess
import sys
import tempfile

FILE_TYPES = {"prg": "p", "seq": "s", "usr": "u", "text": "s"}
# c1541 parses these out of a file name itself: type suffix, drive prefix,
# rename separator and wildcards.
FORBIDDEN = set(',:=*?"')


class ManifestError(Exception):
    pass


def cbm_name(name, where):
    if not 1 <= len(name) <= 16:
        raise ManifestError(f"{where}: name '{name}' must be 1 to 16 characters")
    bad = sorted(set(name) & FORBIDDEN)
    if bad or not name.isascii() or not name.isprintable():
        raise ManifestError(
            f"{where}: name '{name}' has characters c1541 would interpret"
            f"{' (' + ' '.join(bad) + ')' if bad else ''}; use a raw directive")
    return name.lower()


def parse(path, build_dir):
    """Return (title, id, entries). An entry is a dict with at least `kind`."""
    stem = os.path.splitext(os.path.basename(path))[0]
    title, disk_id, entries = stem, "00", []
    with open(path, encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            where = f"{path}:{lineno}"
            try:
                words = shlex.split(line, comments=True)
            except ValueError as e:
                raise ManifestError(f"{where}: {e}")
            if not words:
                continue
            kind, args = words[0], words[1:]
            if kind == "disk":
                if not 1 <= len(args) <= 2:
                    raise ManifestError(f'{where}: expected: disk "TITLE" [id]')
                title = cbm_name(args[0], where)
                if len(args) == 2:
                    disk_id = args[1].lower()
                    if len(disk_id) != 2:
                        raise ManifestError(f"{where}: disk id must be two characters")
            elif kind in FILE_TYPES:
                if not 1 <= len(args) <= 2:
                    raise ManifestError(f'{where}: expected: {kind} <source> ["NAME"]')
                source = args[0]
                if kind == "prg":
                    host = os.path.join(build_dir, source + ".prg")
                    default = source
                else:
                    host = source
                    default = os.path.splitext(os.path.basename(source))[0]
                entries.append({
                    "kind": kind, "where": where, "host": host,
                    "program": source if kind == "prg" else None,
                    "name": cbm_name(args[1] if len(args) == 2 else default, where),
                })
            elif kind == "raw":
                if not args:
                    raise ManifestError(f"{where}: expected: raw <c1541 command> [args]")
                entries.append({"kind": "raw", "where": where, "args": args})
            else:
                raise ManifestError(f"{where}: unknown directive '{kind}'")
    return title, disk_id, entries


def ascii_to_petscii(data):
    """Unshifted letters for lower case, shifted for upper, CR for a line end."""
    out = bytearray()
    for b in data.replace(b"\r\n", b"\n"):
        if b == 0x0A:
            out.append(0x0D)
        elif 0x61 <= b <= 0x7A:
            out.append(b - 0x20)
        elif 0x41 <= b <= 0x5A:
            out.append(b + 0x80)
        else:
            out.append(b)
    return bytes(out)


def c1541(tool, args, where):
    """Run one c1541 command. Its exit status is not reliable on its own -- a
    failed write still exits 0 -- so the output is checked as well."""
    try:
        r = subprocess.run([tool] + args, capture_output=True, text=True, errors="replace")
    except FileNotFoundError:
        raise ManifestError(f"cannot run '{tool}': is VICE installed?")
    output = (r.stdout + r.stderr).strip()
    if r.returncode != 0 or re.search(r"fail|cannot|invalid|unknown|error", output, re.I):
        raise ManifestError(f"{where}: c1541 {' '.join(args[2:])}\n{output}")


def build(manifest, out, build_dir, tool):
    title, disk_id, entries = parse(manifest, build_dir)
    for e in entries:
        if e["kind"] != "raw" and not os.path.isfile(e["host"]):
            raise ManifestError(f"{e['where']}: no such file: {e['host']}")

    # Master into a temporary image and rename at the end, so a failed build
    # never leaves a half-written .d64 that Make would take for up to date.
    tmp = out + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    try:
        c1541(tool, ["-format", f"{title},{disk_id}", "d64", tmp], manifest)
        for e in entries:
            if e["kind"] == "raw":
                c1541(tool, ["-attach", tmp, "-" + e["args"][0]] + e["args"][1:], e["where"])
                continue
            host = e["host"]
            converted = None
            if e["kind"] == "text":
                with open(host, "rb") as f:
                    data = ascii_to_petscii(f.read())
                fd, converted = tempfile.mkstemp(dir=os.path.dirname(out) or ".")
                with os.fdopen(fd, "wb") as f:
                    f.write(data)
                host = converted
            try:
                c1541(tool, ["-attach", tmp, "-write", host,
                             f"{e['name']},{FILE_TYPES[e['kind']]}"], e["where"])
            finally:
                if converted:
                    os.remove(converted)
        os.replace(tmp, out)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def deps(manifests, build_dir):
    """A Make fragment: each image's prerequisites, and which program is where."""
    for manifest in manifests:
        disk = os.path.splitext(os.path.basename(manifest))[0]
        _, _, entries = parse(manifest, build_dir)
        files = [e["host"] for e in entries if e["kind"] != "raw"]
        print(f"{build_dir}/{disk}.d64: {' '.join(files)}")
        programs = []
        for e in entries:
            if e["kind"] != "prg" or e["program"] in programs:
                continue
            programs.append(e["program"])
            print(f"DISKS_OF_{e['program']} += {disk}")
            print(f"CBM_{disk}_{e['program']} := {e['name']}")
        print(f"DISK_PROGS_{disk} := {' '.join(programs)}")


def main():
    ap = argparse.ArgumentParser(description="Build a .d64 from a disk manifest.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build", help="master one disk image")
    b.add_argument("manifest")
    b.add_argument("-o", "--output", required=True)
    b.add_argument("--build", default="build", help="where <program>.prg files are")
    b.add_argument("--c1541", default="c1541")
    d = sub.add_parser("deps", help="print a Make fragment for the manifests")
    d.add_argument("manifests", nargs="*")
    d.add_argument("--build", default="build")
    args = ap.parse_args()
    try:
        if args.cmd == "build":
            build(args.manifest, args.output, args.build, args.c1541)
        else:
            deps(args.manifests, args.build)
    except (ManifestError, OSError) as e:
        print(f"mkd64: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
