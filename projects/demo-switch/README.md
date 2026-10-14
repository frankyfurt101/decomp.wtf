# Original Switch analysis fixture

`demo.c` and `bootstrap.s` are original CC0-1.0 test inputs. No game content or Nintendo SDK is included.
These are static-analysis fixtures, not bootable games.

With clang, LLVM `ld.lld`, and the project's Python dependencies installed:

```sh
python projects/demo-switch/build.py
```

Import `build/demo.elf` or `build/demo.nso` through **Switch analysis** in the app and analyze 16 functions.
Both should discover and draft `_start`, `clamp_value`, and `checksum`. The NSO contains a minimal MOD0,
ELF dynamic symbol table, and SysV symbol hash table, so the loader can find the original function names.
It intentionally has no relocations or GOT; the loader may log a missing-GOT message.
The expected NSO image base is `0x7100000000`. Inspect the selected function's assembly and call links.

Generated binaries are ignored. Rebuilding with a different compiler may change their hashes and code.
The fixture demonstrates import and draft generation, not compiler-equivalence verification.
