@echo off
cd /d "%~dp0"
powershell -NoExit -Command "uv run python server.py"
