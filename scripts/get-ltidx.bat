@echo off
rem Download the prebuilt ltidx (no LLVM or Visual Studio build needed) to %LOCALAPPDATA%\cxxref\ltidx.
rem Usage: get-ltidx.bat [-Version <x.y.z>] [-Zip <local zip>] [-Dest <folder>]
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0get-ltidx.ps1" %*
set RC=%ERRORLEVEL%
if "%~1"=="" pause
exit /b %RC%
