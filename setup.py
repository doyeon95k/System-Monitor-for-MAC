"""
py2app build script for SysMon.

Build standalone .app:
    pip3 install py2app
    python3 setup.py py2app

The output will be in dist/SysMon.app
"""

from setuptools import setup

APP = ['ram_widget.py']
OPTIONS = {
    'argv_emulation': False,
    'plist': {
        'CFBundleName': 'SysMon',
        'CFBundleIdentifier': 'com.user.sysmon',
        'CFBundleVersion': '1.0',
        'CFBundleShortVersionString': '1.0',
        'LSUIElement': True,
    },
    'packages': ['psutil'],
    'frameworks': [],
}

setup(
    app=APP,
    name='SysMon',
    options={'py2app': OPTIONS},
    setup_requires=['py2app'],
)
