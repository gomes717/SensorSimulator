# TCC — Data Viewer

A PyQt6 desktop application that replays Dexcom continuous glucose monitor (CGM) data from CSV files and visualises it in real time.

## Features

- Live treeview of users with their latest glucose reading
- Per-user glucose graph that updates as new readings are delivered
- Debug window listing every emitted message, with detail view on click
- CSV playback loops automatically when the end of a file is reached

## Project structure

```
DataViewer/
├── dataset/          # Dexcom_*.csv source files
├── src/
│   ├── main.py               # Entry point
│   ├── main_window.py        # Main application window
│   ├── data_thread.py        # Background CSV replay thread
│   ├── debug_window.py       # Live message log window
│   └── message_detail_window.py  # Single-message detail view
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
