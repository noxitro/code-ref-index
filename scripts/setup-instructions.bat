@echo off
rem Add or remove the cxxref usage instructions in <root>\.github\copilot-instructions.md.
rem Usage: setup-instructions.bat [-Root <src>] [-Action Add|Remove]   (no arguments: asks for both)
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup-instructions.ps1" %*
if "%~1"=="" pause
