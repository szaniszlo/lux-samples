# Lux samples for the Commodore 64

A Makefile toolchain that compiles [Lux](../luxc/Lux.md) programs, assembles them
into `.prg` files, masters `.d64` disk images and launches the result in VICE.

## Requirements

| Tool | Used for |
| --- | --- |
| `../luxc/bin/luxc` | the Lux compiler (`make -C ../luxc luxc` builds it) |
| `64tass` | assembling luxc's output |
| VICE (`x64sc`, `c1541`) | running programs and writing disk images |
| Python 3 | `tools/mkd64.py` and `tools/screen.py`, standard library only |
| GNU Make 4 | |

## Quick start

```sh
make                # build everything into build/
make run-hello      # launch hello in the emulator
make check          # run every program headless and check its screen
make new NAME=foo   # start a new program
make help           # every target and variable
```

## Layout

```
src/<name>.lux        a single-file program
src/<name>/main.lux   a multi-unit program; the other .lux files beside it are its units
lib/                  units shared by every program
disks/<name>.disk     a disk manifest
assets/               host files that go onto disks
tools/                the disk builder, the screenshot reader, templates for `make new`
build/                everything generated
```

A program's name is its file or directory name, and is also a Make target. Units
are found in the program's own directory, then `lib/`, then luxc's standard
library (`../luxc/lib`).

Building `<name>` produces:

| File | What it is |
| --- | --- |
| `build/<name>.prg` | the program |
| `build/<name>.asm` | luxc's assembly output |
| `build/<name>.lst` | 64tass listing: addresses, bytes and source side by side |
| `build/<name>.labels` | labels in VICE monitor format |

Programs rebuild when any unit they import changes, standard library included,
and when the compiler itself is rebuilt.

## Disk manifests

`disks/<name>.disk` describes one image, `build/<name>.d64`. One directive per
line; files appear in the directory in the order written.

```
disk  "LUX SAMPLES" 2a           # title and two-character id
prg   hello                      # a program from src/
prg   dirlist "DIR"              # ... under another name on the disk
seq   assets/data.bin "DATA"     # a host file as SEQ, byte for byte
usr   assets/data.bin "BLOB"     # the same as USR
text  assets/notes.txt "NOTES"   # SEQ, converted from ASCII to PETSCII
raw   bpoke 1 0 0 76 85 88       # any c1541 command, verbatim
```

- A disk can hold any number of programs; each `prg` line builds its program
  first.
- The name defaults to the program name, or to the host file's name without its
  extension. Names are stored as unshifted PETSCII whatever case you type, so
  they list in upper case on a stock C64.
- `raw` is the escape hatch for shaping an image by hand: `bwrite`, `bpoke` and
  `bfill` write sectors directly, `rename` and `delete` edit the directory.
  `c1541 -help` lists the commands.
- REL files are not supported yet: c1541 cannot create them. New directives
  belong in `tools/mkd64.py`.

`make dir-<name>` prints an image's directory.

## Launching

`make run-<name>` decides how to start a program from the manifests:

- a program named in a manifest is loaded from that disk, with a true 1541 on
  drive 8;
- any other program is injected straight into RAM and run.

`PRG=1` forces the injection, and `DISK=<name>` picks the disk when a program is
on more than one. `make run-disk-<name>` boots an image the way `LOAD"*",8,1`
would.

`make debug-<name>` is the same launch with the program's labels loaded into the
VICE monitor (Alt+H), so disassembly shows Lux names and breakpoints can be set
by label, e.g. `break .main__main`.

The stock KERNAL and 1541 DOS ROM are pinned when VICE's copies are found, so a
run does not depend on your personal `vicerc`; `KERNAL=` or `DOS1541=` (empty)
turns that off. VICE settings are never saved back.

## Smoke test

`make check` runs each program in a windowless `x64sc` for `CHECK_CYCLES`
emulated cycles (default 20 million, about 20 seconds of C64 time, a few seconds
of yours), launched the same way `run-<name>` would. For each program it leaves
in `build/check/`:

- `<name>.png`, the screen as the run ended;
- `<name>.txt`, the 40x25 text read back off that picture;
- `<name>.vice.log`, the emulator's output.

Put a `.expect` file beside the program's root source (`src/hello.expect`,
`src/foo/main.expect`) and every non-blank line in it must appear somewhere on
screen, or the check fails. Without one the program is reported as `RAN`: it
built and the emulator ran it, nothing more.

The text is recovered by matching each character cell against the C64 character
ROM, so it is exact for the stock fonts. Cells showing a custom character set,
sprites or bitmap graphics read as `?`.

## Variables

| Variable | Default | Meaning |
| --- | --- | --- |
| `LUXC` | `../luxc/bin/luxc` | the compiler |
| `OPT` | `-O1` | luxc optimisation level |
| `LUXFLAGS` | | extra luxc flags, e.g. `--no-peephole` |
| `MACHINE` | `c64` | luxc `--machine` |
| `VICE` | `x64sc` | the emulator |
| `VICEFLAGS` | | extra emulator flags |
| `WARP` | | `1` runs the emulator unthrottled |
| `PRG` | | `1` injects the `.prg` even if the program is on a disk |
| `DISK` | | which disk to launch a program from |
| `CHECK_CYCLES` | `20000000` | length of a `make check` run |
