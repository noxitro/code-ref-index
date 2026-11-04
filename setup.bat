@echo off
rem First-time setup: choose the LLVM folder (saved to indexer\llvm-root.txt), build ltidx, build the index of a project,
rem add the usage instructions, and register the MCP server in the Copilot CLI.
rem Usage: setup.bat [-LlvmRoot <llvm>] [-Root <src>] [-NoIndex] [-Instructions] [-RegisterCopilot]
rem   (no arguments: asks for everything; empty root skips the index)
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup.ps1" %*
if "%~1"=="" pause
