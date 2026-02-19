# SysMon — macOS System Monitor Widget

A lightweight, always-on-top desktop widget for real-time macOS system monitoring.

Built with Python + PyObjC (native Cocoa).

![macOS](https://img.shields.io/badge/macOS-12%2B-blue)
![Python](https://img.shields.io/badge/Python-3.9%2B-yellow)

## Features

### System Tab
- **Battery** — charge level, charging status, time remaining + energy impact donut chart (top 5 processes)
- **CPU / RAM / GPU** — real-time percentage with color-zoned sparkline graphs (green → yellow → red)
- **Network** — download/upload speed with auto-scaling graph

### Disk Tab
- Per-disk donut chart showing free/used space
- Available capacity, usage percentage

### Activity Tab
- All running apps sorted by CPU usage (energy impact)
- **Quit** button to terminate any app directly

### UI
- Dark theme, floating window with rounded corners
- Collapse/expand toggle
- Menu bar control (SysMon status item)
- Scrollable content area
- Tabbed interface (System / Disk / Activity)

## Quick Install

```bash
git clone https://github.com/doyeon95k/System-Monitor-for-MAC.git
cd System-Monitor-for-MAC
./install.sh
```

This installs dependencies, creates `SysMon.app` in `~/Applications`, and registers it for Spotlight.

## Manual Install

```bash
pip3 install --user -r requirements.txt
python3 ram_widget.py
```

## Build Standalone .app (no Python required)

```bash
pip3 install py2app
python3 setup.py py2app
```

The standalone `SysMon.app` will be in the `dist/` folder. Distribute this to users who don't have Python installed.

## Start on Login

```bash
osascript -e 'tell application "System Events" to make login item at end with properties {path:"'$HOME'/Applications/SysMon.app", hidden:true}'
```

## Menu Bar

Look for **SysMon** in the menu bar to:
- Show / Hide widget
- Collapse / Expand
- Quit

## Requirements

- macOS 12+
- Python 3.9+ (pre-installed with Xcode Command Line Tools)
- Dependencies: `psutil`, `pyobjc-framework-Cocoa`

## License

MIT
