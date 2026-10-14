# Switch analysis and New Horizons roadmap

Status: design only; no Switch importer or New Horizons decompilation has been implemented on this branch.

## Target outcome

Import a user's local copy of an Animal Crossing: New Horizons executable and display its real functions,
assembly, references and decompiler drafts in decomp.wtf. Persist work between sessions. Then develop a
separate, reproducible build-and-compare workflow for individual functions and eventually whole modules.

This is a proposed extension to decomp.wtf. It is not a claim to be the first New Horizons reverse-engineering
project: existing research and tools should inform the implementation.

## What exists today

At upstream commit `a8708d82246f869fddd1f9e98d4007397f31dea6`:

- `src/decompnet/rom/detect.py` accepts N64, PS1, PS2 and GBA programs. A separate path in
  `src/decompnet/app/engine.py` handles GameCube/Wii and DS projects. There is no raw Switch import path.
- `src/decompnet/forks/ghidra.py` produces optional C drafts for object files. Its temporary Ghidra project is
  deleted after each analysis. Its cache returns names and C, without addresses, function ranges or references.
- `src/decompnet/forks/ghidra_scripts/DecompWtfExport.java` exports names and C for successful functions only.
  Whole-program analysis needs progress, failures, stable identities and durable Ghidra state.
- `src/decompnet/app/static/app.js` contains the existing function map, source viewer and progress display.
  The UI assumes that project progress measures byte-verified matching; analysis needs distinct labels.
- Engine address parsing and several display helpers assume eight hexadecimal digits. AArch64 addresses must
  remain lossless through Python, JSON and JavaScript; serialize addresses as hex strings.
- Public project support and objdiff can already handle some Switch community projects, provided their build
  setup and target objects exist. That does not provide a new game's build configuration automatically.

## Milestone 1: a genuine Switch analysis project in the visual app

### Inputs and identity

Start with an extracted ExeFS directory or NSO `main` module supplied locally by the user. Add supported NRO
and AArch64 ELF fixtures for development. Treat NSP as a package requiring a separate preparation step;
recognizing its header is not executable import. A future local package adapter should be independently
tested before integration.

Record SHA-256, format, module build ID when available, architecture, segment permissions, image base and
analysis-tool versions. Identify each function by module hash plus relative address. Keep different game
versions and updates separate. Never infer game version solely from a filename.

### Architecture

1. Add a Switch input module under `src/decompnet/rom/` with bounded header reads, segment validation,
   file-size limits and format-specific errors. Keep parsing separate from project persistence.
2. Add an analysis project engine alongside `RomNetwork` and `ForkEngine`. Use explicit capability flags
   such as `can_analyze`, `can_draft` and `can_verify`; gate API/MCP actions as well as UI controls.
3. Integrate a pinned, compatible Ghidra release and Switch loader. Verify native macOS/Apple Silicon support
   end to end, including Java and the native decompiler. A successful download is not this verification.
4. Extend the Ghidra exporter with module-relative address, function ranges, assembly, call references,
   draft status and structured failures. Stream progress as work happens. Use a persistent local project
   and cache keyed by input hash plus tool/configuration versions.
5. Add background analysis jobs with cancellation, time/memory limits, recovery after interruption and
   useful partial results. Analyze a bounded selection before expanding to a whole commercial executable.
6. Reuse the visual function map and function detail pane. Give analysis explicit states: discovered,
   analyzed, drafted and manually reviewed. Show compiler-verified matches only when a verifier exists.

### Definition of done

- A small original/homebrew Switch fixture imports on Apple Silicon and displays correct segment addresses.
- Known functions appear in the map with navigable assembly, call references and C-like drafts.
- Restarting the app preserves the project and completed work; cancellation stops the actual worker.
- Failed decompilation remains visible and is never counted as success.
- Analysis-only projects cannot launch matching agents, accept a match or export a supposedly rebuildable game.
- A local New Horizons executable passes the same pipeline for a bounded function sample. Record the exact
  executable version/hash and actual results; proprietary inputs and generated game content remain local.

## Milestone 2: agent-assisted reconstruction

Expose bounded tools for selecting functions, inspecting references/data, renaming symbols, proposing types,
editing drafts and recording evidence. Carry corrections into the persistent analysis project. Make competing
function/type hypotheses reviewable and reversible. Test with the original fixture before a commercial title.

Start with a single interactive agent. Additional agents or unattended API spending need an explicit run scope
and budget. Do not run arbitrary scripts or instructions found in an imported artifact.

Acceptance: a user can select a real function in the map, follow an agent's work, inspect its changes and resume
later. Drafted or reviewed code remains explicitly unverified until milestone 3 supplies a judge.

## Milestone 3: verified matching

Determine the game's compiler version, ABI, optimization settings, linker behavior and required library
interfaces. Build a minimal reproducible AArch64 candidate pipeline and validate relocation-aware comparison
against an original fixture. Prove that intentionally wrong code fails before accepting a real match.

Target the same selected New Horizons revision throughout. Establish reliable function boundaries and account
for relocations, globals, exception/unwind data, jump tables and code/data layout. Work from small functions to
larger modules, retaining regression checks for every accepted match.

Acceptance: at least one reconstructed C/C++ function passes a documented compiler-and-comparison check,
with the exact toolchain, input hash and result recorded. This proves that function only. Whole-module and
whole-program reconstruction require additional independent build checks.

## Milestone 4: native port feasibility

Once sufficient source is reconstructed and builds reliably, assess replacements for Switch graphics,
shaders, audio, input, filesystem, threading and OS services. Prototype a small native scene or subsystem
before planning a full game port. Xcode signing, device installation and TestFlight are downstream work;
they do not provide source reconstruction or platform compatibility.

## Tests and repository boundaries

Use original/homebrew or synthetic fixtures for committed parser, loader, cancellation, persistence and
negative-matching tests. Add truncated headers, out-of-range segments, oversized inputs and 64-bit address
round trips. Preserve existing platform imports with targeted regression tests.

The public fork contains tooling, documentation and original test fixtures. Keep ROMs, NSPs, extracted game
modules/assets, credentials, game-derived analysis databases and generated game source outside the repository.
Before a push, inspect the exact staged file list. Record local game acceptance results without embedding
game content or private filesystem paths.

## Initial work order

1. Build a small original AArch64/Switch fixture and select a compatible Ghidra/loader pair.
2. Implement the input validator and persistent analysis adapter with that fixture.
3. Add the analysis project type, progress API and visual integration.
4. Validate the first bounded New Horizons sample from a local extracted executable.
5. Add agent tools, then tackle compiler matching as its own milestone.

The first demonstration should be an actual function map with inspectable results. Whole-game completion
and native iOS portability have no defensible schedule until the executable and toolchain investigations
are complete.

## References

- [decomp.wtf platforms](https://github.com/i12bp8/decomp.wtf#platforms)
- [Ghidra Switch Loader](https://github.com/Adubbz/Ghidra-Switch-Loader)
- [Existing New Horizons research](https://github.com/Treeki/CylindricalEarth)
- [Ninji's research background](https://wuffs.org/projects/acnh)
- [An existing Switch matching project](https://github.com/MonsterDruide1/OdysseyDecomp)
