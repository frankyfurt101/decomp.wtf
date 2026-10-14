#!/usr/bin/env python3
"""Build an original AArch64 ELF and uncompressed NSO fixture (clang + ld.lld)."""
import hashlib
import shutil
import struct
import subprocess
from pathlib import Path
from elftools.elf.elffile import ELFFile

root = Path(__file__).resolve().parent
build = root / 'build'
build.mkdir(exist_ok=True)
clang, linker = shutil.which('clang'), shutil.which('ld.lld')
if not clang or not linker:
    raise SystemExit('clang and ld.lld must be on PATH')
subprocess.run([clang, '--target=aarch64-none-elf', '-O1', '-fno-inline', '-ffreestanding',
                '-fno-stack-protector', '-fno-unwind-tables', '-fno-asynchronous-unwind-tables',
                '-c', str(root / 'demo.c'), '-o', str(build / 'demo.o')], check=True)
subprocess.run([linker, '-Ttext=0x7100000000', '-e', '_start', str(build / 'demo.o'),
                '-o', str(build / 'demo.elf')], check=True)
# The fixture's NSO is separately linked at low module-relative segment addresses.
subprocess.run([clang, '--target=aarch64-none-elf', '-c', str(root / 'bootstrap.s'), '-o', str(build / 'bootstrap.o')], check=True)
script = build / 'nso.ld'
script.write_text('SECTIONS { . = 0; .text : { *(.text.bootstrap) *(.text*) } . = 0x1000; .rodata : { *(.rodata.mod0) *(.rodata*) } . = 0x2000; .data : { *(.data.dynamic) *(.data*) } .bss : { __bss_start = .; *(.bss*) __bss_end = .; } }')
subprocess.run([linker, '-T', str(script), '-e', '_module_start', str(build / 'bootstrap.o'), str(build / 'demo.o'), '-o', str(build / 'nso.elf')], check=True)
header = bytearray(0x100)
header[:4] = b'NSO0'
struct.pack_into('<I', header, 0xC, 0x38)
payload = bytearray()
with (build / 'nso.elf').open('rb') as f:
    elf = ELFFile(f)
    for i, name in enumerate(('.text', '.rodata', '.data')):
        section = elf.get_section_by_name(name)
        data = section.data() if section else b''
        address = section['sh_addr'] if section else i * 0x1000
        bss = elf.get_section_by_name('.bss')
        extra = bss['sh_size'] if i == 2 and bss else 0
        struct.pack_into('<IIII', header, 0x10 + i * 16, 0x100 + len(payload), address, len(data), extra)
        header[0xA0 + i * 32:0xC0 + i * 32] = hashlib.sha256(data).digest()
        payload.extend(data)
header[0x40:0x60] = hashlib.sha256(payload).digest()
(build / 'demo.nso').write_bytes(header + payload)
print('Built original fixtures:', build / 'demo.elf', build / 'demo.nso')
