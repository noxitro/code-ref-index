@echo off
rem Smoke test for the MCP server (starts server.py and talks JSON-RPC over stdio)
setlocal
cd /d "%~dp0"
python -I -W error::ResourceWarning -m unittest discover -s . -v
set RC=%ERRORLEVEL%
if not defined CI pause
exit /b %RC%
