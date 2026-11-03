@echo off
rem MCP server (stdio) for the code-ref-index. Runs in the current folder unless --root is given.
python -I "%~dp0server.py" %*
