# 第三方组件

Windows 安装包内置下列运行时组件，原作者保留其版权。项目代码采用 GPL-3.0-only；第三方组件的许可证及版权声明不因此改变。

| 组件 | 版本 | 声明与源码 |
|---|---|---|
| CPython | 3.13.7（本次构建） | Python Software Foundation License；安装目录 `PYTHON_LICENSE.txt`；[源码与发布](https://www.python.org/downloads/release/python-3137/) |
| PySide6 / Shiboken6 | 6.11.2 | Qt for Python 开源许可；[对应版本源码](https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.11.2-src/)；[许可说明](https://doc.qt.io/qtforpython-6/licenses.html) |
| Qt | 6.11.2 | Qt 开源许可，适用 GPLv3 / LGPLv3 等上游条款；[Qt 源码发布目录](https://download.qt.io/archive/qt/6.11/6.11.2/)；[许可说明](https://doc.qt.io/qt-6/licensing.html) |
| tomlkit | 0.13.3 | MIT；[对应版本源码](https://github.com/python-poetry/tomlkit/tree/0.13.3) |
| PyInstaller bootloader | 6.22.3 | GPLv2 或更新版本附引导程序例外；[许可与例外](https://pyinstaller.org/en/stable/license.html)；[源码](https://github.com/pyinstaller/pyinstaller/tree/v6.22.3) |
| Inno Setup 简体中文翻译 | 构建时使用的上游版本 | MIT，Copyright (c) 2019 - 2020 kirakira；许可文件在 `packaging/ChineseSimplified-LICENSE.txt` 及安装目录 `THIRD_PARTY_LICENSES`；[源码](https://github.com/kira-96/Inno-Setup-Chinese-Simplified-Translation) |

包内保留 Python 的完整许可证及各 Python 发行包的许可元数据；PyInstaller 许可放在 `THIRD_PARTY_LICENSES`。上游框架中的第三方组件继续遵循其各自声明。应用使用系统字体，没有打包额外字体文件。

GitHub Release 应同时提供该版本的项目源码 ZIP 与构建脚本。重新构建时使用 `requirements.txt`、`requirements-build.txt` 和 `packaging` 中的文件；若更换运行时版本，应同步更新本说明及对应源码位置。
