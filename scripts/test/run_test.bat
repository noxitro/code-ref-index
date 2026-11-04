@echo off
rem Tests for make-cdb.ps1 / index-project.ps1 (needs Visual Studio with the C++ tools; index-project also needs ltidx.exe)
setlocal
cd /d "%~dp0"
python -I -W error::ResourceWarning -m unittest discover -s . -v
set TESTRC=%ERRORLEVEL%
if not defined CI pause
exit /b %TESTRC%
