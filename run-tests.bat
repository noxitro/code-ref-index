@echo off
rem Run every test: ltq, the MCP server, make-cdb (needs Visual Studio), and the indexer (if indexer\build\ltidx.exe exists). Double-click to run.
setlocal
set "INTERACTIVE=1"
if defined CI set "INTERACTIVE="
set CI=1
set TESTRC=0
echo == ltq (query\test)
call "%~dp0query\test\run_test.bat" || set TESTRC=1
echo == MCP server (mcp\test)
call "%~dp0mcp\test\run_test.bat" || set TESTRC=1
echo == make-cdb / index-project (scripts\test)
call "%~dp0scripts\test\run_test.bat" || set TESTRC=1
if exist "%~dp0indexer\build\ltidx.exe" (
  echo == ltidx ^(indexer\test^)
  call "%~dp0indexer\test\run_test.bat" || set TESTRC=1
) else (
  echo == ltidx: skipped ^(not built yet; run indexer\build.bat^)
)
echo.
if %TESTRC%==0 (echo ALL PASS) else (echo SOME TESTS FAILED)
if defined INTERACTIVE pause
exit /b %TESTRC%
