#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# SPDX-FileCopyrightText: 2026 lori28167
"""Run Samsung's libScalable_Encoder.so (aarch64) under Unicorn as a reference encoder.

Only malloc/free/memcpy/memset/__stack_chk_fail/__cxa_* are imported by the library,
so those are hooked in Python and no libc is needed.

    ssc_oracle.py LIB.so [--fs 48000] [--ch 2] [--bits 16] [--rate 128000] [--frames 8]
"""
import argparse
import struct
import sys

from elftools.elf.elffile import ELFFile
from elftools.elf.relocation import RelocationSection
from unicorn import (UC_ARCH_ARM64, UC_HOOK_CODE, UC_MODE_ARM, Uc, UcError)
from unicorn.arm64_const import (UC_ARM64_REG_CPACR_EL1, UC_ARM64_REG_LR,
                                 UC_ARM64_REG_PC, UC_ARM64_REG_SP,
                                 UC_ARM64_REG_TPIDR_EL0, UC_ARM64_REG_X0,
                                 UC_ARM64_REG_X1, UC_ARM64_REG_X2,
                                 UC_ARM64_REG_X3, UC_ARM64_REG_X4)

PAGE = 0x1000
BASE = 0x10000000
STUB = 0x08000000      # one 8-byte slot per hooked import
RET_MAGIC = 0x08ff0000  # return address that stops emulation
HEAP = 0x20000000
HEAP_SIZE = 64 << 20
STACK = 0x30000000
STACK_SIZE = 4 << 20
TLS = 0x40000000

R_ABS64, R_GLOB_DAT, R_JUMP_SLOT, R_RELATIVE = 257, 1025, 1026, 1027


def up(v):
    return (v + PAGE - 1) & ~(PAGE - 1)


class Oracle:
    def __init__(self, path):
        self.uc = Uc(UC_ARCH_ARM64, UC_MODE_ARM)
        uc = self.uc
        # FP/SIMD must be enabled for the NEON code in the library
        uc.reg_write(UC_ARM64_REG_CPACR_EL1, 3 << 20)
        self.heap_top = HEAP
        uc.mem_map(HEAP, HEAP_SIZE)
        uc.mem_map(STACK, STACK_SIZE)
        uc.mem_map(TLS, PAGE)
        uc.mem_write(TLS + 0x28, struct.pack("<Q", 0x5ca1ab1e0badc0de))  # stack canary
        uc.reg_write(UC_ARM64_REG_TPIDR_EL0, TLS)
        uc.mem_map(STUB, PAGE * 16)
        self.stubs = {}
        self.syms = {}
        self._load(path)
        uc.hook_add(UC_HOOK_CODE, self._on_code, begin=STUB, end=STUB + PAGE * 16)

    def _load(self, path):
        uc = self.uc
        with open(path, "rb") as fh:
            elf = ELFFile(fh)
            for seg in elf.iter_segments():
                if seg["p_type"] != "PT_LOAD":
                    continue
                lo = BASE + (seg["p_vaddr"] & ~(PAGE - 1))
                hi = BASE + up(seg["p_vaddr"] + seg["p_memsz"])
                try:
                    uc.mem_map(lo, hi - lo)
                except UcError:
                    pass  # overlapping page with the previous segment
                uc.mem_write(BASE + seg["p_vaddr"], seg.data())
            dynsym = elf.get_section_by_name(".dynsym")
            for s in dynsym.iter_symbols():
                if s["st_shndx"] != "SHN_UNDEF" and s.name:
                    self.syms[s.name] = BASE + s["st_value"]
            for sec in elf.iter_sections():
                if not isinstance(sec, RelocationSection):
                    continue
                symtab = elf.get_section(sec["sh_link"])
                for r in sec.iter_relocations():
                    t, where = r["r_info_type"], BASE + r["r_offset"]
                    add = r["r_addend"]
                    if t == R_RELATIVE:
                        val = BASE + add
                    elif t in (R_ABS64, R_GLOB_DAT, R_JUMP_SLOT):
                        sym = symtab.get_symbol(r["r_info_sym"])
                        if sym["st_shndx"] == "SHN_UNDEF":
                            val = self._stub(sym.name)
                        else:
                            val = BASE + sym["st_value"] + (add if t == R_ABS64 else 0)
                    else:
                        raise RuntimeError("unhandled reloc type %d" % t)
                    uc.mem_write(where, struct.pack("<Q", val))

    def _stub(self, name):
        if name not in self.stubs:
            self.stubs[name] = STUB + 8 * len(self.stubs)
        return self.stubs[name]

    def _malloc(self, n):
        p = (self.heap_top + 15) & ~15
        self.heap_top = p + max(n, 1)
        if self.heap_top > HEAP + HEAP_SIZE:
            raise MemoryError("oracle heap exhausted")
        return p

    def _on_code(self, uc, addr, size, _):
        name = next((n for n, a in self.stubs.items() if a == addr), None)
        x = [uc.reg_read(UC_ARM64_REG_X0 + i) for i in range(3)]
        ret = 0
        if name == "malloc":
            ret = self._malloc(x[0])
        elif name == "memcpy":
            uc.mem_write(x[0], bytes(uc.mem_read(x[1], x[2])))
            ret = x[0]
        elif name == "memset":
            uc.mem_write(x[0], bytes([x[1] & 0xff]) * x[2])
            ret = x[0]
        elif name in ("free", "__cxa_atexit", "__cxa_finalize"):
            ret = 0
        elif name == "__stack_chk_fail":
            raise RuntimeError("stack check failed inside the library")
        else:
            raise RuntimeError("unhandled import %r" % name)
        uc.reg_write(UC_ARM64_REG_X0, ret & 0xFFFFFFFFFFFFFFFF)
        uc.reg_write(UC_ARM64_REG_PC, uc.reg_read(UC_ARM64_REG_LR))

    def call(self, name, *args):
        uc = self.uc
        regs = (UC_ARM64_REG_X0, UC_ARM64_REG_X1, UC_ARM64_REG_X2,
                UC_ARM64_REG_X3, UC_ARM64_REG_X4)
        for r, a in zip(regs, args):
            uc.reg_write(r, a & 0xFFFFFFFFFFFFFFFF)
        uc.reg_write(UC_ARM64_REG_SP, STACK + STACK_SIZE - 0x100)
        uc.reg_write(UC_ARM64_REG_LR, RET_MAGIC)
        uc.mem_map(RET_MAGIC, PAGE) if not self._ret_mapped() else None
        uc.emu_start(self.syms[name], RET_MAGIC)
        return uc.reg_read(UC_ARM64_REG_X0)

    def _ret_mapped(self):
        return any(lo <= RET_MAGIC < hi for lo, hi, _ in self.uc.mem_regions())

    def alloc(self, data=b"", size=None):
        p = self._malloc(size if size is not None else len(data))
        if data:
            self.uc.mem_write(p, data)
        return p

    def read(self, addr, n):
        return bytes(self.uc.mem_read(addr, n))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("lib")
    ap.add_argument("--fs", type=int, default=48000)
    ap.add_argument("--ch", type=int, default=2)
    ap.add_argument("--bits", type=int, default=16, help="4th argument of ssc_encoder_init/create")
    ap.add_argument("--rate", type=int, default=128000, help="bitrate in bit/s")
    ap.add_argument("--frame", type=int, default=0, help="samples per channel per frame (0 = ask the library)")
    ap.add_argument("--frames", type=int, default=4)
    a = ap.parse_args()

    o = Oracle(a.lib)
    size = o.call("ssc_encoder_get_size", a.ch, a.fs) & 0xFFFFFFFF
    print("ssc_encoder_get_size(ch=%d, fs=%d) = %d" % (a.ch, a.fs, size))
    err = o.alloc(size=4)
    st = o.call("ssc_encoder_create", a.fs, a.ch, a.bits, err)
    print("ssc_encoder_create -> st=0x%x err=%d" % (st, struct.unpack("<i", o.read(err, 4))[0]))
    frame = a.frame or 864
    print("frame size (samples/channel):", frame)

    import math
    # int32 container, value in the low bits: 16-bit samples as is, 24-bit/UHQ samples scaled by 256
    # (ssc_preemphasis shifts by 12 for 16-bit and by 4 otherwise, so both end up on the same scale)
    nbytes = 4
    shift = 0 if a.bits == 16 else 8
    pcm = bytearray()
    n = 0
    out = o.alloc(size=16384)
    for f in range(a.frames):
        pcm.clear()
        for _ in range(frame):
            for c in range(a.ch):
                v = int(8000 * math.sin(2 * math.pi * (440 * (c + 1)) * n / a.fs)) << shift
                pcm += struct.pack("<i", v)[:nbytes]
            n += 1
        pp = o.alloc(bytes(pcm))
        r = o.call("ssc_encode", st, pp, frame, out, a.rate) & 0xFFFFFFFF
        r = r - (1 << 32) if r & 0x80000000 else r
        data = o.read(out, max(r, 0))
        print("frame %d: ret=%d  %s" % (f, r, data[:24].hex(" ")))


if __name__ == "__main__":
    sys.exit(main())
