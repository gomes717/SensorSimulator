# TCC — Data Viewer

A PyQt6 desktop application that connects to a BLE continuous glucose monitor (CGM) sensor and visualises its readings in real time.

## Features

- Scan for and connect to nearby BLE devices, with automatic pairing for devices needing it (Windows)
- Live treeview of users with their latest glucose reading, sourced from the connected sensor
- Per-user glucose graph that updates as new readings arrive
- Debug window listing every BLE message received, with detail view on click

## Project structure

```
DataViewer/
├── dataset/          # Dexcom_*.csv sample files (not currently used by the app)
├── src/
│   ├── main.py                  # Entry point
│   ├── main_window.py           # Main application window (toolbar, treeview, graph)
│   ├── ble_message_log.py       # Shared log of messages received over BLE
│   ├── ble_session.py           # Persistent BLE connection + CGM measurement decoding
│   ├── bluetooth_scanner.py     # Background BLE device scan
│   ├── bluetooth_window.py      # Device list / connect / disconnect window
│   ├── windows_ble_pairing.py   # Windows-only auto-pairing helper
│   ├── debug_window.py          # Live BLE message log window
│   └── message_detail_window.py # Single-message detail view
├── requirements.txt
├── pyproject.toml    # Pylint configuration
└── .gitignore
```

## Setup

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

## Running

```bash
python src/main.py
```

## Linting

```bash
pylint src/
```
