"""The firmware's C implementation of the G6 protocol (firmware/peripheral_cgms/src/dexcom_g6.c),
built for the PC and run against the same golden vectors as the Python twin.

Needs a C compiler: ``cc`` / ``gcc`` / ``clang`` on PATH, or the ``ziglang`` wheel
(``uv run --with ziglang pytest tests/test_dexcom_g6_c.py``). Skipped when there is none; the
vector-freshness test needs no compiler."""

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import make_dexcom_g6_vectors as vectors

ROOT = Path(__file__).resolve().parent.parent
HOST = ROOT / "firmware" / "peripheral_cgms" / "tests" / "host"
SRC = ROOT / "firmware" / "peripheral_cgms" / "src"


def _compiler() -> list[str] | None:
    for name in ("cc", "gcc", "clang"):
        found = shutil.which(name)
        if found:
            return [found]
    if importlib.util.find_spec("ziglang"):
        return [sys.executable, "-m", "ziglang", "cc"]
    return None


def test_the_committed_vectors_are_what_the_generator_produces():
    """If this fails, run scripts/make_dexcom_g6_vectors.py: the Python and C tests share them."""
    data = vectors.build()
    data["grid_expected"] = vectors._grid_expected(data)  # pyright: ignore[reportPrivateUsage]
    committed = json.loads((ROOT / "tests" / "data" / "dexcom_g6_vectors.json").read_text("utf-8"))
    assert committed == data
    header = vectors._header(data)  # pyright: ignore[reportPrivateUsage]
    assert (HOST / "g6_vectors.h").read_text("utf-8") == header


def test_the_c_implementation_passes_the_shared_vectors(tmp_path):
    cc = _compiler()
    if cc is None:
        pytest.skip("no C compiler (cc/gcc/clang, or `uv run --with ziglang`)")
    exe = tmp_path / ("test_g6.exe" if sys.platform == "win32" else "test_g6")
    build = subprocess.run(
        [
            *cc, "-std=c99", "-Wall", "-Wextra", "-Werror", f"-I{SRC}", "-o", str(exe),
            str(HOST / "test_dexcom_g6.c"), str(SRC / "dexcom_g6.c"), str(SRC / "aes128.c"), "-lm",
        ],
        capture_output=True, text=True, check=False,
    )  # fmt: skip
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(exe)], capture_output=True, text=True, check=False)
    assert run.returncode == 0, run.stdout
    assert " 0 failed" in run.stdout
