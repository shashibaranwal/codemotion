"""Build the Windows app folder and zip, then smoke-test the result.

    uv run python packaging/build.py            # build + test + zip
    uv run python packaging/build.py --no-zip

Output: ``build/dist/CodeMotion/`` (CodeMotion.exe, codemotion-cli.exe) and
``build/dist/CodeMotion-<version>-windows-x64.zip``.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "build"
DIST = BUILD / "dist"
APP = DIST / "CodeMotion"


def version() -> str:
    for line in (ROOT / "src" / "codemotion" / "__init__.py").read_text("utf-8").splitlines():
        if line.startswith("__version__"):
            return line.split('"')[1]
    raise SystemExit("no __version__ found")


def folder_size(path: Path) -> int:
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def check(name: str, command: list[str], timeout: float = 300) -> None:
    print(f"- {name}: {' '.join(Path(c).name if i == 0 else c for i, c in enumerate(command))}")
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout, cwd=ROOT)
    if result.returncode != 0:
        print(result.stdout[-2000:], result.stderr[-2000:], sep="\n")
        raise SystemExit(f"FAILED: {name} (exit code {result.returncode})")


def smoke_test() -> None:
    cli, gui = APP / "codemotion-cli.exe", APP / "CodeMotion.exe"
    with tempfile.TemporaryDirectory(prefix="codemotion-build-") as tmp:
        out = Path(tmp)
        check("CLI help", [str(cli), "--help"])
        check("themes", [str(cli), "themes"])
        # Runs a script in a child process and renders with two worker processes.
        check("render a script", [str(cli), "render", "examples/scripts/request_flow.py",
                                  "--preview", "--workers", "2", "--gif",
                                  "-o", str(out / "flow.mp4")])  # fmt: skip
        for name in ("flow.mp4", "flow.gif", "flow.json"):
            if not (out / name).is_file():
                raise SystemExit(f"FAILED: render did not write {name}")
        check("render a code file", [str(cli), "render", "examples/fib.py", "--duration", "4",
                                     "--preview", "-o", str(out / "fib.mp4")])  # fmt: skip
        # A user script may import any standard-library module (and numpy, which ships).
        script = out / "imports.py"
        script.write_text(
            "import csv, datetime, json, math, random, statistics, textwrap, itertools\n"
            "import numpy\n"
            "from codemotion import Text, Video\n"
            "video = Video(duration=3)\n"
            "video.add(Text(str(statistics.mean([1, 2, 3]))))\n",
            encoding="utf-8",
        )
        check("script imports", [str(cli), "plan", str(script)])
        report = out / "selftest.txt"
        check("desktop self-test", [str(gui), "--self-test", str(report)])
        print("  " + report.read_text("utf-8").strip().replace("\n", "\n  "))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--no-zip", action="store_true", help="skip the zip")
    parser.add_argument("--no-test", action="store_true", help="skip the smoke tests")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("This build script targets Windows; see docs/platforms.md.")

    if not (ROOT / "packaging" / "icon.ico").is_file():
        subprocess.run([sys.executable, str(ROOT / "packaging" / "make_icon.py")], check=True)
    print("Building with PyInstaller (a few minutes)...")
    subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
         "--distpath", str(DIST), "--workpath", str(BUILD / "work"),
         str(ROOT / "packaging" / "codemotion.spec")],
        check=True,
        cwd=ROOT,
    )  # fmt: skip
    shutil.rmtree(BUILD / "work", ignore_errors=True)  # PyInstaller's cache, ~100 MB
    print(f"Built {APP} ({folder_size(APP) / 1e6:.0f} MB)")
    if not args.no_test:
        print("Smoke tests:")
        smoke_test()
    if not args.no_zip:
        zip_base = DIST / f"CodeMotion-{version()}-windows-x64"
        archive = Path(shutil.make_archive(str(zip_base), "zip", DIST, "CodeMotion"))
        print(f"Zipped {archive} ({archive.stat().st_size / 1e6:.0f} MB)")


if __name__ == "__main__":
    main()
