# Switch analysis and New Horizons roadmap

Status: experimental local analysis implemented. NSO and AArch64 ELF import, persistent Ghidra projects, bounded C-like drafts, assembly, direct calls, and a visual function map are integrated into the original application at `/#/switch` (`/switch` redirects there). Bounded Antigravity CLI source reconstruction is available. Compiler matching, NRO import, richer agent tools, and game export remain future work.


## Run the analysis preview

Run `decomp` from this checkout and choose **Switch analysis** in the sidebar. Supply an extracted NSO `main`,
an ExeFS folder containing `main`, or a linked AArch64 ELF. Confirm ownership, then click **Decompile remaining** with **Keep going** enabled. The worker crosses 500-function
checkpoints automatically until it exhausts discovered functions, encounters an error, or you click Stop.
Uncheck Keep going for a single 1–500-function checkpoint. Each checkpoint skips attempted addresses and retains results. Inputs are copied into `DECOMP_HOME/switch-analysis/<sha256>/`; the original
file is left untouched. NSP files receive an extraction-required message. This feature does not process keys.

Each Ghidra checkpoint uses at most two analysis CPUs, a 2 GiB Java heap, 300 seconds of automatic discovery, 20 seconds
per function, and a 15-minute checkpoint limit. A continuous run can exceed 15 minutes across successful checkpoints. These are task limits, not a claim of total process-memory
isolation. Cancel stops the worker process group. App interruption is reported on the next launch.
A finished project's Ghidra database can be reused; changing loader JARs or the registered Ghidra
release invalidates reuse. Exporter-only changes reuse the saved program. `functions.jsonl` streams partial results; failed attempts retain their logs.
Cumulative results are retained in `base-functions.jsonl`; the previous batch is also preserved as `previous-functions.jsonl`.

The original sidebar, theme, canvas, Functions and Activity views, and code drawer are reused. The map shows
all discovered internal functions, including those not yet drafted. Real `started` and completed-function events
drive amber pulses, blue completion rings, counts and the activity feed. During discovery the worker banner
shows the actual phase; no function activity is simulated. An idle worker produces no work animation.

**Counts are analysis results, not game-completion percentages.** Discovered functions depend on bounded
Ghidra analysis; batches visit remaining nonexternal functions in address order. The UI calls Ghidra results **decompiled**, meaning C-like output, not recovered original source. This output may
be wrong or incomplete. Verified matches remain zero. These projects are separate from the matching engine
and cannot be claimed by matching agents or exported as rebuildable games.

## Tested toolchain and setup

Tested on Apple Silicon macOS with Ghidra 12.1.4, Temurin JDK 21, and Ghidra-Switch-Loader source commit
`5d80d195bf284f8ad5f2f9b66c3bdd5c67dd66a6`. Ghidra is installed through the existing toolchain registry.
Use `JAVA_HOME` for the JDK and `DECOMPNET_TOOLCHAINS` for a custom local toolchain directory.

The loader release tagged `1.6.1-c600d05-Ghidra_12.1.2` supplies its third-party dependency JARs. The loader
itself must be rebuilt against Ghidra 12.1.4; changing its version label alone is insufficient. The helper
uses a previously downloaded source tree and unpacked release without contacting the network:

```sh
python tools/build-switch-loader.py --ghidra /absolute/path/to/ghidra \
  --source /absolute/path/to/Ghidra-Switch-Loader \
  --release-dir /absolute/path/to/unpacked/SwitchLoader \
  --jdk /absolute/path/to/jdk/Contents/Home --output /absolute/path/to/new/SwitchLoader
```

Copy the resulting `SwitchLoader` directory to `Ghidra/Extensions/` in that Ghidra installation, then restart
the app. The official loader's Gradle build is also supported upstream; consult its README for compatibility.

The Ghidra release used here did not include a macOS ARM native decompiler. Build its shipped source with
Xcode command-line tools (use a fresh build tree to avoid mixing architectures):

```sh
cd /absolute/path/to/ghidra/Ghidra/Features/Decompiler/src/decompile/cpp
make -j2 ghidra_opt ARCH_TYPE='-arch arm64' ADDITIONAL_FLAGS='-mmacosx-version-min=11.0'
mkdir -p ../../../os/mac_arm_64
cp ghidra_opt ../../../os/mac_arm_64/decompile
```

The app checks for the native executable before starting analysis. Java/Ghidra may create their usual user
preferences and cache directories. Imports are analyzed, never executed as target programs.

## Validation

`pytest tests/test_switch_analysis.py tests/test_app_server.py` covers input bounds, malformed packages,
64-bit addresses, authentication/Host/write-header checks, interrupted recovery, cancellation, stale-export
rejection, and separation from the matching database. Platform-dependent existing tests may skip when their
legacy compiler is absent. Build and analyze both original fixtures in `projects/demo-switch/` for the
Ghidra integration check; the unit suite does not download tools or require commercial game data.

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

Start with an extracted ExeFS directory or NSO `main` module supplied locally by the user. AArch64 ELF fixtures support development; NRO remains future work. Treat NSP as a package requiring a separate preparation step;
recognizing its header is not executable import. A future local package adapter should be independently
tested before integration.

Record SHA-256, format, module build ID when available, architecture, segment permissions, image base and
analysis-tool versions. Identify each function by module hash plus relative address. Keep different game
versions and updates separate. Never infer game version solely from a filename.

### Architecture

1. Add a Switch input module under `src/decompnet/analysis/` with bounded header reads, segment validation,
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

## Milestone 2: agent-assisted reconstruction (initial adapter implemented)

The original project view now has an **AI source reconstruction** panel. It detects installed Antigravity
CLI (`agy`), lists the account's models, and uses its existing sign-in. There are five independently controlled worker slots.
Each explicit run processes 1–100 eligible decompiled functions per worker; the UI defaults to 100. Each
function gets its own CLI invocation and a five-minute limit. Model dropdowns list all models returned by
`agy models`, including available Gemini, Claude and GPT-OSS variants. **Start all idle workers** launches
up to five concurrent runs; individual Start/Stop buttons and **Stop all AI** are also available. The Agents
page can launch a selected slot and open the project. No run starts automatically on page load.
CLI subscription/credit limits still apply. Reported token usage is shown without inventing a dollar cost.

Assembly and Ghidra source are supplied on stdin. Antigravity returns schema-validated C/C++ candidates,
instruction evidence and unresolved assumptions. The adapter does not give it matching tools or claim
that its output compiles. The normal CLI permissions and terminal sandbox remain in effect. Imported
program strings are explicitly treated as untrusted data. Candidate writes are performed by the app,
separate from the original Ghidra records, and are never counted as verified matches.

Workers reserve disjoint function addresses under a lock. Candidate index writes are serialized, and
each worker has a separate CLI working directory and durable job file. Failures release unfinished claims
without discarding other workers' results. The app allows at most five active AI workers across projects.

The map pulses purple on every active function. The activity feed reflects actual CLI events; it does not
expose private model reasoning. Saved candidates remain purple, and the original drawer switches between
Ghidra output and the AI candidate with its evidence. Stop AI terminates the process group and preserves
completed candidates. Restart marks unfinished runs interrupted; it does not silently spend more quota.

Still planned:


Expose bounded tools for selecting functions, inspecting references/data, renaming symbols, proposing types,
editing drafts and recording evidence. Carry corrections into the persistent analysis project. Make competing
function/type hypotheses reviewable and reversible. Test with the original fixture before a commercial title.

The current adapter starts one bounded CLI worker. Additional agents or unattended API spending need an explicit run scope
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

## Local acceptance record — October 14, 2026

- Original AArch64 ELF and NSO fixtures each yielded three discovered functions and three drafts.
  The NSO retained `_start`, `clamp_value`, and `checksum`, two call links from `_start`, and image
  base `0x7100000000`. A fresh continuation check drafted one function, then two additional functions,
  retained all three, streamed start/completion events, and reused the saved program on the second batch.
- A privately held New Horizons 1.0.0 main module, SHA-256
  `f8d248109ff72999b4600ae3749f8c7397fb960f7ad3ab8dee3018ae9e14e900`, reached **1,064 saved drafts / 0 failures**
  over successive batches. The latest bounded discovery found **49,363 functions**, including
  **48,487 internal functions** represented on the map. Discovery is partial; its inventory can change
  when rerun. Verified compiler matches: **0**.
- The initial separate Switch screen was replaced with integration into the original app's sidebar,
  project header, canvas, Functions/Activity tabs, themes and assembly/C drawer. A browser recording
  captured an actual batch advancing from **564 to 1,064** drafts, with active-function pulses and
  completion rings driven by worker events. No mock job or synthetic function events were used.
- Browser checks passed for function filtering, 64-bit address search, assembly/C selection, read-only
  drafts, search hits/misses, activity navigation, dark theme, project/home navigation, and a 390-pixel
  viewport without horizontal overflow. No JavaScript errors were observed.
- Targeted Python tests: **54 passed, 3 skipped**; the skips are existing environment-dependent checks.
- Antigravity CLI completed a real one-function New Horizons reconstruction at `0x71000001c0`, preserving
  the Ghidra draft and saving a separate candidate with instruction evidence and type/ABI uncertainties.
  The app observed live AI work, saved one candidate, and verified drawer switching. The CLI reported
  30,658 total tokens for this first run using its configured default model; the model identity was not
  separately reported, so the record labels it CLI default. No compiler match is claimed.
- A real original NSO fixture completed three consecutive one-function checkpoints automatically and stopped
  at the end of its inventory. This verifies continuous progression beyond an individual checkpoint.
- Concurrency tests processed 500 fixture functions across five workers, with no duplicate addresses or lost
  candidates. Five real fixture subprocesses used separate working directories and selected model arguments.
  Cancellation, failure recovery, legacy job migration, slot validation, and API routing passed.
- Fourteen browser checks covered five model dropdowns, the 100-function bound, start-all/stop-one/stop-all,
  cancellation during bulk launch, five active map cells, worker errors, and mobile layout. These browser
  start/stop requests were intercepted; no extra game reconstruction quota was used for UI testing.
- No game binaries, generated game drafts, assets, keys, or Ghidra databases are included in this repository.

These results qualify a local analysis preview. They do not establish a complete decompilation, native port,
rebuildable game, or TestFlight application. Further work in milestones 2–4 above remains outstanding.

Agent adapter reference: [Antigravity CLI headless mode](https://antigravity.google/docs/cli/headless/).
