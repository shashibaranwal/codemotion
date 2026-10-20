# PyInstaller spec: one folder with CodeMotion.exe (desktop) and codemotion-cli.exe (CLI).
# Build with:  uv run python packaging/build.py
# ruff: noqa
import importlib.util
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ROOT = Path(SPECPATH).parent
ICON = str(ROOT / "packaging" / "icon.ico")

# Fonts (with licenses), themes, the icon; and the bundled examples (File -> Open example).
datas = collect_data_files("codemotion", includes=["assets/**/*", "themes/*.toml"])
datas += [(str(ROOT / "examples"), "examples")]

# Video scripts are user code: they may import any standard-library module, so bundle it
# all (minus GUI toolkits and test suites), not just what CodeMotion itself imports.
SKIP_STDLIB = {"tkinter", "turtle", "turtledemo", "idlelib", "test", "ensurepip", "venv",
               "lib2to3", "pydoc_data", "distutils", "msilib", "this", "antigravity"}
stdlib = [m for m in sorted(sys.stdlib_module_names)
          if not m.startswith("_") and m not in SKIP_STDLIB
          and importlib.util.find_spec(m) is not None]  # skips other platforms' modules
hiddenimports = collect_submodules("codemotion") + stdlib

# Qt modules CodeMotion doesn't use (the PySide6 wheels ship all of them).
excludes = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtWebSockets", "PySide6.QtQml", "PySide6.QtQuick",
    "PySide6.QtQuick3D", "PySide6.QtQuickWidgets", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtGraphs",
    "PySide6.QtDataVisualization", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtBluetooth",
    "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtRemoteObjects", "PySide6.QtScxml",
    "PySide6.QtTextToSpeech", "PySide6.QtSpatialAudio", "PySide6.QtHttpServer",
    "tkinter", "pytest", "pytestqt", "mypy", "ruff", "IPython",
]


def analysis(entry):
    return Analysis(
        [str(ROOT / "packaging" / entry)],
        pathex=[str(ROOT / "src")],
        datas=datas,
        hiddenimports=hiddenimports,
        excludes=excludes,
        noarchive=False,
    )


gui = analysis("entry_gui.py")
cli = analysis("entry_cli.py")

# Files the hooks collect but CodeMotion never loads: the virtual-keyboard input plugin
# (which drags in Qt Quick and QML), the PDF image plugin (Qt PDF) and Qt's UI
# translations (the app is English). opengl32sw.dll stays: Qt's software-rendering
# fallback for machines without working graphics drivers.
DROP = ("qt6virtualkeyboard", "qtvirtualkeyboard", "qt6quick", "qt6qml", "qt6pdf",
        "imageformats/qpdf", "pyside6/translations/")


def trim(toc):
    return [entry for entry in toc
            if not any(part in entry[0].replace("\\", "/").lower() for part in DROP)]


gui.binaries, gui.datas = trim(gui.binaries), trim(gui.datas)
cli.binaries, cli.datas = trim(cli.binaries), trim(cli.datas)

gui_exe = EXE(
    PYZ(gui.pure), gui.scripts, [], exclude_binaries=True, name="CodeMotion",
    console=False, icon=ICON, upx=False,
)
cli_exe = EXE(
    PYZ(cli.pure), cli.scripts, [], exclude_binaries=True, name="codemotion-cli",
    console=True, icon=ICON, upx=False,
)
COLLECT(
    gui_exe, gui.binaries, gui.datas,
    cli_exe, cli.binaries, cli.datas,
    name="CodeMotion", upx=False,
)
