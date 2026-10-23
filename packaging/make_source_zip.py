"""Write the source archive of a release: only files that belong to the project.

usage: make_source_zip.py ROOT OUT.zip VERSION

With git, that is every tracked file plus new files that are not ignored, so unrelated folders sitting in the project
directory never end up in a public download. Without git it falls back to a skip list.
"""
import pathlib
import subprocess
import sys
import zipfile

root, out, version = pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3]
SKIP = {".build", "dist", ".git", "__pycache__", ".pytest_cache", ".venv", ".claude", "graphify-out"}
try:
    listing = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "-z"], cwd=root, capture_output=True, check=True).stdout
    files = sorted(pathlib.Path(root, n.decode("utf-8")) for n in listing.split(b"\0") if n)
except (OSError, subprocess.CalledProcessError):
    files = sorted(p for p in root.rglob("*") if not (set(p.relative_to(root).parts) & SKIP))
count = 0
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
    for p in files:
        if p.is_file() and p.suffix != ".pyc":
            z.write(p, f"shapeshifter-{version}/{p.relative_to(root).as_posix()}")
            count += 1
print(f"{count} files, {pathlib.Path(out).stat().st_size / 1024:.0f} KB")
