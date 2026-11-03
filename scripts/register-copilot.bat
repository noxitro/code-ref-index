@echo off
rem Register the code-ref-index MCP server in the Copilot CLI user config (~/.copilot/mcp-config.json).
rem The server runs in Copilot's working folder and uses its .index\ (build it with index-project.bat).
setlocal
set "SERVER=%~dp0..\mcp\server.py"
for %%i in ("%SERVER%") do set "SERVER=%%~fi"
copilot mcp remove code-ref-index >nul 2>&1
copilot mcp add code-ref-index -- python -I "%SERVER%" || (pause & exit /b 1)
copilot mcp get code-ref-index
pause
