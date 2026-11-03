@echo off
rem Build the code-ref-index for a project. Usage: index-project.bat [-Root <src>] [-Cdb <dir>] [-Jobs N]
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0index-project.ps1" %*
if "%~1"=="" pause
