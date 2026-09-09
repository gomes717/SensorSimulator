@echo off
rem Launch the desktop app from cmd.exe even when `uv` is not on PATH.
rem   run              -> uv run python src\main.py
rem   run -m pytest -q -> uv run python -m pytest -q   (args pass through)
setlocal
cd /d "%~dp0"

set "UV="
where uv >nul 2>nul && set "UV=uv"
if not defined UV if exist "%USERPROFILE%\.local\bin\uv.exe" set "UV=%USERPROFILE%\.local\bin\uv.exe"
if not defined UV if exist "%LOCALAPPDATA%\Programs\uv\uv.exe" set "UV=%LOCALAPPDATA%\Programs\uv\uv.exe"
if not defined UV (
  echo uv not found. Install from https://docs.astral.sh/uv/ or add %%USERPROFILE%%\.local\bin to PATH and reopen the terminal.
  exit /b 1
)

if "%~1"=="" ("%UV%" run python src\main.py) else ("%UV%" run python %*)
exit /b %ERRORLEVEL%
