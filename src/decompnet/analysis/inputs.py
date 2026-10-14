"""Bounded validation of extracted Switch NSO and AArch64 ELF inputs.

NSO layout reference: https://github.com/SciresM/hactool/blob/master/nso.h
No package decryption, key handling or execution of imported programs occurs here.
"""
from __future__ import annotations

import hashlib
import stat
import struct
from pathlib import Path

from elftools.elf.elffile import ELFFile

MAX_FILE = 512 << 20
MAX_MEMORY = 1 << 30
NSP_MESSAGE = ("This is an NSP/PFS0 package, not an executable. Prepare its ExeFS locally and select "
               "the extracted main NSO. Switch analysis does not decrypt packages.")


class InputError(ValueError):
    pass


def _range(offset: int, size: int, limit: int, label: str) -> None:
    if offset < 0 or size < 0 or offset > limit or size > limit - offset:
        raise InputError(f"{label} extends outside its allowed range")


def _overlaps(ranges: list[tuple[int, int]], label: str) -> None:
    ordered = sorted((a, a + n) for a, n in ranges if n)
    if any(b > c for (_, b), (c, _) in zip(ordered, ordered[1:])):
        raise InputError(f"overlapping {label}")


def inspect(path: Path) -> dict:
    path = Path(path).expanduser().resolve()
    if path.is_dir():
        path = path / "main"
    try:
        st = path.stat()
        if not stat.S_ISREG(st.st_mode):
            raise InputError("select a regular executable file")
        with path.open("rb") as f:
            head = f.read(0x100)
            if head[:4] == b"PFS0":
                raise InputError(NSP_MESSAGE)
            if st.st_size > MAX_FILE:
                raise InputError("executable exceeds the 512 MiB analysis limit")
            if head[:4] == b"NSO0":
                info = _nso(head, st.st_size)
            elif head[:4] == b"\x7fELF":
                f.seek(0)
                info = _elf(f, st.st_size)
            else:
                raise InputError("select an extracted NSO main module or a little-endian AArch64 ELF executable")
            f.seek(0)
            hasher, read = hashlib.sha256(), 0
            while chunk := f.read(min(1 << 20, MAX_FILE + 1 - read)):
                read += len(chunk)
                if read > MAX_FILE:
                    raise InputError("executable grew beyond the 512 MiB analysis limit")
                hasher.update(chunk)
            if read != st.st_size:
                raise InputError("executable changed while reading; retry the import")
            digest = hasher.hexdigest()
        return info | {"sha256": digest, "size": st.st_size, "source_name": path.name, "path": str(path)}
    except InputError:
        raise
    except (OSError, ValueError, struct.error) as e:
        raise InputError(f"cannot read executable: {e}") from e


def _nso(head: bytes, file_size: int) -> dict:
    if len(head) < 0x100:
        raise InputError("truncated NSO header")
    version, reserved, flags = struct.unpack_from("<III", head, 4)
    if version != 0 or reserved != 0 or flags & ~0x3F:
        raise InputError("unsupported NSO header version or flags")
    segments, disk, memory = [], [], []
    for i, name in enumerate((".text", ".rodata", ".data")):
        off, address, size, extra = struct.unpack_from("<IIII", head, 0x10 + 16 * i)
        stored = struct.unpack_from("<I", head, 0x60 + 4 * i)[0] if flags & (1 << i) else size
        if size and (off < 0x100 or not stored):
            raise InputError(f"invalid {name} file offset or size")
        _range(off, stored, file_size, name)
        memsize = size + (extra if i == 2 else 0)
        _range(address, memsize, MAX_MEMORY, name)
        if i == 0 and (size < 4 or address % 4):
            raise InputError("invalid NSO text segment")
        disk.append((off, stored))
        memory.append((address, memsize))
        segments.append({"name": name, "address": hex(address), "size": size,
                         "memory_size": memsize, "compressed": bool(flags & (1 << i)),
                         "permissions": ("r-x", "r--", "rw-")[i]})
    _overlaps(disk, "NSO file segments")
    _overlaps(memory, "NSO memory segments")
    return {"format": "NSO", "architecture": "Switch NSO (loader detected)", "build_id": head[0x40:0x60].hex(),
            "segments": segments}


def _elf(f, file_size: int) -> dict:
    try:
        elf = ELFFile(f)
        if elf.elfclass != 64 or not elf.little_endian or elf["e_machine"] != "EM_AARCH64":
            raise InputError("ELF must be 64-bit little-endian AArch64")
        if elf["e_type"] not in ("ET_EXEC", "ET_DYN"):
            raise InputError("ELF must be a linked executable or shared module")
        count = elf["e_phnum"]
        if count > 128 or elf["e_shnum"] > 8192:
            raise InputError("ELF has too many headers")
        _range(elf["e_phoff"], count * elf["e_phentsize"], file_size, "ELF program headers")
        _range(elf["e_shoff"], elf["e_shnum"] * elf["e_shentsize"], file_size, "ELF section headers")
        segments, memory, total = [], [], 0
        for s in elf.iter_segments():
            if s["p_type"] != "PT_LOAD":
                continue
            off, size, memsize, addr = (s[k] for k in ("p_offset", "p_filesz", "p_memsz", "p_vaddr"))
            _range(off, size, file_size, "ELF load segment")
            _range(addr, memsize, 1 << 64, "ELF virtual address")
            if size > memsize:
                raise InputError("ELF file segment exceeds its memory size")
            total += memsize
            if total > MAX_MEMORY:
                raise InputError("ELF load memory exceeds 1 GiB")
            memory.append((addr, memsize))
            flags = s["p_flags"]
            segments.append({"name": f"LOAD{len(segments)}", "address": hex(addr), "size": size,
                             "memory_size": memsize, "permissions": "".join(c if flags & n else "-"
                                                                                  for c, n in (("r", 4), ("w", 2), ("x", 1)))})
        _overlaps(memory, "ELF load segments")
        if not any("x" in s["permissions"] and s["size"] for s in segments):
            raise InputError("ELF has no executable segment")
        return {"format": "ELF", "architecture": "AArch64", "entry": hex(elf["e_entry"]),
                "build_id": None, "segments": segments}
    except InputError:
        raise
    except Exception as e:
        raise InputError(f"malformed ELF: {e}") from e
