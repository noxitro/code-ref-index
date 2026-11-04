@echo off
rem First-time setup: choose the LLVM folder (saved to indexer\llvm-root.txt), build ltidx, then build the index of a project.
rem Usage: setup.bat [-LlvmRoot <llvm>] [-Root <src>] [-NoIndex]   (no arguments: asks for both; empty root skips the index)
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1" %*
if "%~1"=="" pause
