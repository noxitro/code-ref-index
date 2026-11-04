@echo off
rem Regression test for ltq (synthetic indexes: merged configurations, re-located lines, freshness)
setlocal
cd /d "%~dp0"
python -I -W error::ResourceWarning -m unittest discover -s . -v
set RC=%ERRORLEVEL%
pause
exit /b %RC%
