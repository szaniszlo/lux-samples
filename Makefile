# Lux -> Commodore 64 toolchain: compile, assemble, master disks, launch in VICE.
#
#   src/<name>.lux         single-file program   -> build/<name>.prg
#   src/<name>/main.lux    multi-unit program    -> build/<name>.prg
#   lib/                   units shared by every program
#   disks/<name>.disk      disk manifest         -> build/<name>.d64
#
# `make help` lists the targets. README.md documents the manifest format.

.DEFAULT_GOAL := all
SHELL := /bin/sh
MAKEFLAGS += --no-builtin-rules
.SUFFIXES:
.DELETE_ON_ERROR:
.SECONDEXPANSION:

# ── Tools and knobs (override on the command line or in the environment) ──
LUXC      ?= ../luxc/bin/luxc
TASS      ?= 64tass
C1541     ?= c1541
VICE      ?= x64sc
PYTHON    ?= python3
MACHINE   ?= c64
OPT       ?= -O1
LUXFLAGS  ?=
VICEFLAGS ?=

SRC := src
LIB := lib
B   := build

# ── Programs ──────────────────────────────────────────────────────────────
SINGLE   := $(patsubst $(SRC)/%.lux,%,$(wildcard $(SRC)/*.lux))
MULTI    := $(patsubst $(SRC)/%/main.lux,%,$(wildcard $(SRC)/*/main.lux))
PROGRAMS := $(sort $(SINGLE) $(MULTI))
DISKS    := $(patsubst disks/%.disk,%,$(wildcard disks/*.disk))

ifneq ($(filter $(SINGLE),$(MULTI)),)
$(error both $(SRC)/<name>.lux and $(SRC)/<name>/main.lux exist for: $(filter $(SINGLE),$(MULTI)))
endif

# Every program is also a target of its own name, so it cannot be called after
# one of the Makefile's.
RESERVED := all programs images check new list help clean
ifneq ($(filter $(RESERVED),$(PROGRAMS)),)
$(error program name is reserved by the Makefile: $(filter $(RESERVED),$(PROGRAMS)))
endif

# root: the source luxc is pointed at.  inc: the unit search path. A multi-unit
# program's own directory comes before lib/, so a private unit wins over a
# shared one of the same name.
root = $(if $(filter $1,$(MULTI)),$(SRC)/$1/main.lux,$(SRC)/$1.lux)
inc  = $(if $(filter $1,$(MULTI)),-I $(SRC)/$1) -I $(LIB)

LUXARGS = --machine=$(MACHINE) $(OPT) $(LUXFLAGS)

# The compiler is a prerequisite of every program: a rebuilt luxc rebuilds them.
LUXC_PATH := $(if $(findstring /,$(LUXC)),$(LUXC),$(shell command -v $(LUXC) 2>/dev/null))
ifeq ($(LUXC_PATH),)
LUXC_PATH := $(LUXC)
endif

.PHONY: all programs images check new list help clean

all: programs images

programs: $(PROGRAMS:%=$(B)/%.prg)

images: $(DISKS:%=$(B)/%.d64)

$(B) $(B)/check:
	@mkdir -p $@

# This Makefile never builds the compiler; it only says where it looked.
$(LUXC_PATH):
	@echo "error: Lux compiler not found at '$(LUXC)'." >&2
	@echo "       Build it (make -C ../luxc luxc) or pass LUXC=/path/to/luxc." >&2
	@exit 1

# One merged .asm per program. --emit-deps then records every unit luxc loaded,
# stdlib included, so editing any of them rebuilds exactly the programs using it.
# Each unit also gets an empty rule, which keeps a deleted unit from being an
# error before the next compile has had the chance to drop it.
$(PROGRAMS:%=$(B)/%.asm): $(B)/%.asm: $$(call root,$$*) $(LUXC_PATH) | $(B)
	$(LUXC) $(LUXARGS) $(call inc,$*) -o $@ $<
	@$(LUXC) $(LUXARGS) $(call inc,$*) --emit-deps $< \
		| awk -F'\t' '{ print "$@: " $$2; print $$2 ":" }' > $(B)/$*.d

-include $(PROGRAMS:%=$(B)/%.d)

# -C is required, not optional: Lux identifiers are case-sensitive and 64tass is
# not by default. The listing and the VICE label file come for free.
$(PROGRAMS:%=$(B)/%.prg): $(B)/%.prg: $(B)/%.asm
	$(TASS) -C -q --cbm-prg --vice-labels -l $(B)/$*.labels -L $(B)/$*.lst -o $@ $<

.PHONY: $(PROGRAMS)
$(PROGRAMS): %: $(B)/%.prg

# ── Disks ─────────────────────────────────────────────────────────────────
# build/disks.mk is generated from the manifests. It gives each image its
# prerequisites and tells the launch rules which programs live on which disk:
#
#   build/demo.d64: build/hello.prg assets/notes.txt
#   DISK_PROGS_demo := hello        programs on the disk, in directory order
#   DISKS_OF_hello  += demo         disks carrying the program
#   CBM_demo_hello  := hello        the program's file name on that disk
#
# It depends on the disks/ directory as well as the manifests, so removing a
# manifest regenerates it too.
ifneq ($(DISKS),)
ifneq ($(filter-out clean help new,$(or $(MAKECMDGOALS),all)),)
include $(B)/disks.mk
endif
endif

$(B)/disks.mk: $(DISKS:%=disks/%.disk) disks tools/mkd64.py | $(B)
	@$(PYTHON) tools/mkd64.py deps --build $(B) $(filter %.disk,$^) > $@

$(DISKS:%=$(B)/%.d64): $(B)/%.d64: disks/%.disk tools/mkd64.py | $(B)
	$(PYTHON) tools/mkd64.py build --build $(B) --c1541 $(C1541) -o $@ $<

.PHONY: $(DISKS:%=disk-%) $(DISKS:%=dir-%)
$(DISKS:%=disk-%): disk-%: $(B)/%.d64

$(DISKS:%=dir-%): dir-%: $(B)/%.d64
	@$(C1541) $< -list

# ── Launching ─────────────────────────────────────────────────────────────
# A program named in a disk manifest starts from that disk, with a true 1541 on
# drive 8; any other program is injected straight into RAM. PRG=1 forces the
# injection, DISK=<name> picks the disk when a program is on several.
disk_of = $(if $(filter 1,$(PRG)),,$(or $(DISK),$(firstword $(DISKS_OF_$1))))

launch_file = $(if $(call disk_of,$1),$(B)/$(call disk_of,$1).d64,$(B)/$1.prg)

launch_args = $(if $(call disk_of,$1),$(VICE_DISK) -autostart "$(B)/$(call disk_of,$1).d64:$(CBM_$(call disk_of,$1)_$1)",$(VICE_INJECT) -autostart $(B)/$1.prg)

check_disk = $(if $(call disk_of,$1),$(if $(CBM_$(call disk_of,$1)_$1),,$(error program '$1' is not on disk '$(call disk_of,$1)')))

# The stock KERNAL and 1541 DOS are pinned when VICE's own copies can be found,
# so a run does not depend on what ~/.config/vice/vicerc selects. KERNAL= or
# DOS1541= (empty) leaves that choice to vicerc. +saveres: never write settings
# back.
VICE_DATA ?= $(firstword $(wildcard /opt/homebrew/share/vice /usr/local/share/vice /usr/share/vice))
KERNAL    ?= $(wildcard $(VICE_DATA)/C64/kernal-901227-03.bin)
DOS1541   ?= $(wildcard $(VICE_DATA)/DRIVES/dos1541-325302-01+901229-05.bin)

VICE_BASE   = +saveres $(if $(KERNAL),-kernal $(KERNAL)) $(if $(filter 1,$(WARP)),-warp) $(VICEFLAGS)
VICE_INJECT = -autostartprgmode 1
VICE_DISK   = -drive8type 1541 -drive8truedrive $(if $(DOS1541),-dos1541 $(DOS1541))

# VICE narrates its whole boot on stdout; that goes to a log, shown only if the
# emulator fails.
define launch
	@echo "$(VICE) $(strip $1)"
	@$(VICE) $(VICE_BASE) $1 > $2 2>&1 || { tail -n 15 $2 >&2; echo "emulator failed; full log: $2" >&2; exit 1; }
endef

.PHONY: $(PROGRAMS:%=run-%) $(PROGRAMS:%=debug-%) $(PROGRAMS:%=zp-%) $(DISKS:%=run-disk-%)

$(PROGRAMS:%=run-%): run-%: $$(call launch_file,$$*)
	$(call check_disk,$*)
	$(call launch,$(call launch_args,$*),$(B)/$*.vice.log)

# Same launch with the program's labels loaded into the monitor (Alt+H), so
# disassembly shows Lux names and `break .main__main` works.
$(PROGRAMS:%=debug-%): debug-%: $$(call launch_file,$$*) $(B)/%.prg
	$(call check_disk,$*)
	$(call launch,-moncommands $(B)/$*.labels $(call launch_args,$*),$(B)/$*.vice.log)

# Boot a disk the way LOAD"*",8,1 would: its first file.
$(DISKS:%=run-disk-%): run-disk-%: $(B)/%.d64
	$(call launch,$(VICE_DISK) -autostart $<,$(B)/$*.d64.vice.log)

$(PROGRAMS:%=zp-%): zp-%: $$(call root,$$*) $(LUXC_PATH)
	@$(LUXC) $(LUXARGS) $(call inc,$*) --emit-zp-map -o /dev/null $< 2>&1 | grep -v '^luxc: assembly written'

# ── Headless smoke test ───────────────────────────────────────────────────
# Each program runs in a windowless x64sc for CHECK_CYCLES emulated cycles,
# launched exactly as run-<name> would launch it. VICE writes a screenshot as it
# exits and tools/screen.py reads the text back off it. A <root>.expect file
# beside the program's root source lists lines that must be on screen; without
# one the check only proves the program built and the emulator ran it.
#
# -limitcycles is the run's duration, not a cap: nothing ends it earlier.
CHECK_CYCLES ?= 20000000
CHECK_TIMEOUT = $(shell expr $(CHECK_CYCLES) / 900000 + 60)

expect = $(wildcard $(basename $(call root,$1)).expect)

.PHONY: $(PROGRAMS:%=check-%)

check: $(PROGRAMS:%=check-%)

$(PROGRAMS:%=check-%): check-%: $$(call launch_file,$$*) $$(call expect,$$*) tools/screen.py | $(B)/check
	$(call check_disk,$*)
	@rm -f $(B)/check/$*.png $(B)/check/$*.txt
	@timeout -s KILL $(CHECK_TIMEOUT) $(VICE) $(VICE_BASE) -console -warp -sounddev dummy \
		-limitcycles $(CHECK_CYCLES) +autostart-delay-random \
		-exitscreenshot $(B)/check/$*.png $(call launch_args,$*) \
		> $(B)/check/$*.vice.log 2>&1; true
	@$(PYTHON) tools/screen.py --name $* --log $(B)/check/$*.vice.log \
		$(if $(VICE_DATA),--chargen $(VICE_DATA)/C64/chargen-901225-01.bin) \
		$(if $(call expect,$*),--expect $(call expect,$*)) \
		-o $(B)/check/$*.txt $(B)/check/$*.png

# ── Scaffolding ───────────────────────────────────────────────────────────
# NAME is restricted to what luxc accepts as a unit path segment.
new:
	@case "$(NAME)" in \
		''|[!a-z]*|*[!a-z0-9]*) \
			echo "usage: make new NAME=<name> [MULTI=1]" >&2; \
			echo "       NAME is lowercase letters and digits, starting with a letter" >&2; \
			exit 1 ;; \
	esac
	@case " $(RESERVED) " in *" $(NAME) "*) echo "error: '$(NAME)' is reserved by the Makefile" >&2; exit 1 ;; esac
	@if [ -e $(SRC)/$(NAME).lux ] || [ -e $(SRC)/$(NAME) ]; then \
		echo "error: program '$(NAME)' already exists" >&2; exit 1; fi
	@mkdir -p $(SRC)
ifeq ($(MULTI),1)
	@mkdir $(SRC)/$(NAME)
	@for f in tools/templates/multi/*.lux; do \
		sed 's/@NAME@/$(NAME)/g' $$f > $(SRC)/$(NAME)/$$(basename $$f); \
		echo "created $(SRC)/$(NAME)/$$(basename $$f)"; \
	done
else
	@sed 's/@NAME@/$(NAME)/g' tools/templates/single.lux > $(SRC)/$(NAME).lux
	@echo "created $(SRC)/$(NAME).lux"
endif
	@echo "build it with 'make $(NAME)', launch it with 'make run-$(NAME)'"

# ── Housekeeping ──────────────────────────────────────────────────────────
list:
	@echo "Programs:"
	@$(foreach p,$(PROGRAMS),printf '  %-16s %-28s %s\n' '$p' '$(call root,$p)' \
		'$(if $(DISKS_OF_$p),on disk: $(DISKS_OF_$p),prg only)';)
	@echo "Disks:"
	@$(foreach d,$(DISKS),printf '  %-16s %-28s %s\n' '$d' 'disks/$d.disk' 'programs: $(or $(DISK_PROGS_$d),none)';)

help:
	@echo "Build"
	@echo "  make                    build every program and disk image into $(B)/"
	@echo "  make <name>             build one program ($(B)/<name>.prg, .asm, .lst, .labels)"
	@echo "  make disk-<disk>        build one disk image ($(B)/<disk>.d64)"
	@echo "  make list               show programs, disks and which program is on which disk"
	@echo "  make new NAME=x         scaffold $(SRC)/x.lux (MULTI=1: $(SRC)/x/main.lux plus a unit)"
	@echo "  make clean              remove $(B)/"
	@echo "Run"
	@echo "  make run-<name>         launch in $(VICE): from its disk if a manifest names it,"
	@echo "                          otherwise injected into RAM"
	@echo "  make run-disk-<disk>    boot a disk image (its first file)"
	@echo "  make debug-<name>       run-<name> with the program's labels in the VICE monitor"
	@echo "Inspect"
	@echo "  make dir-<disk>         print a disk image's directory"
	@echo "  make zp-<name>          print the program's zero-page frame layout"
	@echo "  make check              headless run of every program; screens in $(B)/check/"
	@echo "  make check-<name>       the same for one program"
	@echo "Variables"
	@echo "  PRG=1                   inject the .prg even if the program is on a disk"
	@echo "  DISK=<disk>             choose the disk when a program is on several"
	@echo "  WARP=1                  run the emulator unthrottled"
	@echo "  OPT=-O2  LUXFLAGS=...   optimisation level, extra luxc flags"
	@echo "  LUXC=$(LUXC)  VICE=$(VICE)  VICEFLAGS=..."
	@echo "  CHECK_CYCLES=$(CHECK_CYCLES)   length of a check run, in emulated cycles"

clean:
	rm -rf $(B)
