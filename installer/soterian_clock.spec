# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for Soterian Floating Clock Widget.
Produces a single-directory bundle with all dependencies.

Usage:
    cd /opt/soteria_global/engines/calendar
    pyinstaller installer/soterian_clock.spec
"""

import sys
import os

block_cipher = None
src_dir = os.path.abspath(os.path.join(SPECPATH, '..'))

a = Analysis(
    [os.path.join(src_dir, 'soterian_clock.py')],
    pathex=[src_dir],
    binaries=[],
    datas=[
        # Bundle the translations dir so the runtime _t() helper can find
        # locale files. en.json is the source-of-truth fallback; other
        # locales are layered on top via _detect_language().
        (os.path.join(src_dir, 'translations'), 'translations'),
    ],
    hiddenimports=[
        'pystray',
        'pystray._xorg',    # Linux
        'pystray._win32',   # Windows
        'pystray._darwin',  # macOS
        'PIL',
        'PIL.Image',
        'PIL.ImageDraw',
        'zoneinfo',
        'requests',
        'certifi',
        # OS-keyring backends — keyring picks the right one at runtime per
        # platform, so PyInstaller needs all three discoverable.
        'keyring',
        'keyring.backends.SecretService',  # Linux (libsecret)
        'keyring.backends.macOS',           # macOS (Keychain)
        'keyring.backends.Windows',         # Windows (Credential Locker)
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'matplotlib', 'numpy', 'scipy', 'pandas',
        'pytest', 'unittest', 'setuptools',
        'flask', 'skyfield', 'astropy',
    ],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='soterian-clock',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # No terminal window
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='soterian-clock',
)
