@echo off
rem Register the code-ref-index MCP server in the Copilot CLI user config (~/.copilot/mcp-config.json).
rem The server runs in Copilot's working folder and uses its .index\ (build it with index-project.bat).
setlocal
rem copilot is copilot.cmd when installed from npm, so use call to come back here
set "SERVER=%~dp0..\mcp\server.py"
for %%i in ("%SERVER%") do set "SERVER=%%~fi"
call copilot mcp remove code-ref-index >nul 2>&1
call copilot mcp add code-ref-index -- python -I "%SERVER%" || (pause & exit /b 1)
call copilot mcp get code-ref-index
pause
