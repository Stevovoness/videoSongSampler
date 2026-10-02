# -*- mode: python ; coding: utf-8 -*-
# Build with:  python build_exe.py   (runs setup_tools.py first so Rubber Band + Audiveris are present)
# One-folder build: Audiveris and its Java runtime are ~160 MB, which a one-file exe would have to
# unpack on every launch.
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

import basic_pitch  # noqa: E402
bp_dir = os.path.dirname(basic_pitch.__file__)

datas = [
    ("assets", "assets"),
    ("tools/rubberband", "tools/rubberband"),
    ("tools/audiveris/Audiveris", "omr"),   # short path: Audiveris has very long class-file names
    (os.path.join(bp_dir, "saved_models", "icassp_2022", "nmp.onnx"), "basic_pitch/saved_models/icassp_2022"),
]
datas += collect_data_files("librosa")
datas += collect_data_files("music21", includes=["**/*.json", "**/*.txt", "**/*.xml", "**/*.css"],
                            excludes=["corpus/**", "**/tests/**"])

hiddenimports = (
    collect_submodules("vsampler")
    + collect_submodules("basic_pitch", filter=lambda n: "train" not in n and "data" not in n)
    + collect_submodules("music21.musicxml") + collect_submodules("music21.midi")
    + ["onnxruntime", "pretty_midi", "soundfile", "PySide6.QtMultimedia", "PySide6.QtMultimediaWidgets"]
)

excludes = [
    "tkinter", "matplotlib", "IPython", "jupyter", "notebook", "pytest", "tensorflow", "torch", "coremltools",
    "tflite_runtime", "PyQt5", "PyQt6", "PySide2",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.Qt3DCore",
    "PySide6.Qt3DRender", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtQuick3D", "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtDesigner", "PySide6.QtBluetooth",
    "PySide6.QtPositioning", "PySide6.QtLocation", "PySide6.QtSensors", "PySide6.QtSerialPort", "PySide6.QtTest",
    "PySide6.QtRemoteObjects", "PySide6.QtScxml", "PySide6.QtWebSockets", "PySide6.QtHelp",
]

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)
# Unused large DLLs: OpenCV's own FFmpeg (PyAV does all video I/O) and Qt's software OpenGL fallback.
_DROP = ("opencv_videoio_ffmpeg", "opengl32sw.dll")
a.binaries = [b for b in a.binaries if not any(d in os.path.basename(b[0]).lower() for d in _DROP)]
pyz = PYZ(a.pure)

common = dict(
    name="VideoSampler",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="assets/icon.ico",
)

exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **common)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="VideoSampler")
