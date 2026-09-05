# Third-party components

RoomPilot uses the unmodified runtimes listed below. Copyright remains with their respective authors. License texts collected from the installed distributions are in `licenses/` in the Windows package.

| Component | Version | Project / source |
|---|---|---|
| Python | 3.13.12 | https://www.python.org/downloads/release/python-31312/ |
| PySide6 / Shiboken / Qt | 6.11.2 | https://code.qt.io/cgit/pyside/pyside-setup.git/ and https://download.qt.io/archive/qt/6.11/6.11.2/ |
| NumPy | 2.3.5 | https://github.com/numpy/numpy/tree/v2.3.5 |
| SciPy | 1.17.1 | https://github.com/scipy/scipy/tree/v1.17.1 |
| javaobj-py3 | 0.6.1 | https://github.com/tcalmant/python-javaobj |
| PyInstaller bootloader | 6.22.2 | https://pyinstaller.org/ |

NumPy and SciPy include additional notices for their numerical libraries in their license files. Qt contains third-party components; see the [Qt licensing and third-party inventory](https://doc.qt.io/qt-6/licensing.html) and [Qt for Python notices](https://doc.qt.io/qtforpython-6/licenses.html). Qt multimedia plugins may include FFmpeg; see the [Qt Multimedia attribution](https://doc.qt.io/qt-6/qtmultimedia-attribution-ffmpeg.html).

The Qt / PySide community libraries are distributed separately as dynamically loaded DLLs under `_internal`. The source code and build script for RoomPilot are supplied alongside this package. Users may replace compatible third-party libraries and rebuild the application; no restriction is imposed on modification or reverse engineering needed to debug those library modifications. See the included LGPL / GPL texts and the [official Qt license documentation](https://doc.qt.io/qt-6/licensing.html) for the applicable component terms.

RoomPilot does not bundle REW, REW program code, a Java runtime, or the user's measurement files. REW is a separate product; [its official website](https://www.roomeqwizard.com/) provides downloads and documentation.
