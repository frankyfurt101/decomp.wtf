#!/usr/bin/env python3
"""Rebuild a reviewed SwitchLoader source tree against an installed Ghidra.

Dependencies come from an already downloaded upstream SwitchLoader release.
No network access, key handling, or global installation. Java 21+ required.
"""
import argparse
import os
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--ghidra', required=True, type=Path)
p.add_argument('--source', required=True, type=Path)
p.add_argument('--release-dir', required=True, type=Path, help='unpacked SwitchLoader directory containing lib/')
p.add_argument('--jdk', required=True, type=Path)
p.add_argument('--output', required=True, type=Path, help='new staging directory; must not already exist')
a = p.parse_args()
props = dict(line.split('=', 1) for line in (a.ghidra / 'Ghidra/application.properties').read_text().splitlines() if '=' in line)
version = props['application.version']
deps = [a.release_dir / 'lib' / name for name in ('commons-primitives-1.0.jar', 'lz4-java-1.10.2.jar', 'zstd-jni-1.5.7-1.jar')]
for path in deps:
    if not path.is_file():
        p.error(f'missing dependency: {path}')
if a.output.exists():
    p.error('output exists; choose a new staging directory')
sources = list((a.source / 'src/main/java').rglob('*.java'))
if not sources:
    p.error('no loader Java sources found')
a.output.mkdir(parents=True)
(a.output / 'lib').mkdir()
classpath = os.pathsep.join(map(str, list((a.ghidra / 'Ghidra').rglob('*.jar')) + deps))
with tempfile.TemporaryDirectory() as scratch:
    subprocess.run([str(a.jdk / 'bin/javac'), '-cp', classpath, '-d', scratch, *map(str, sources)], check=True)
    subprocess.run([str(a.jdk / 'bin/jar'), '--create', '--file', str(a.output / 'lib/SwitchLoader.jar'), '-C', scratch, '.'], check=True)
for dep in deps:
    shutil.copyfile(dep, a.output / 'lib' / dep.name)
for name in ('data', 'ghidra_scripts'):
    if (a.source / name).is_dir():
        shutil.copytree(a.source / name, a.output / name)
for name in ('Module.manifest', 'LICENSE.txt', 'README.md'):
    if (a.source / name).is_file():
        shutil.copyfile(a.source / name, a.output / name)
text = (a.source / 'extension.properties').read_text().replace('@extname@', 'SwitchLoader').replace('@extversion@', version)
(a.output / 'extension.properties').write_text(text)
with zipfile.ZipFile(a.output / 'lib/SwitchLoader-source.zip', 'w', zipfile.ZIP_DEFLATED) as z:
    for source in sources:
        z.write(source, source.relative_to(a.source))
print(f'Built SwitchLoader for Ghidra {version}: {a.output}')
