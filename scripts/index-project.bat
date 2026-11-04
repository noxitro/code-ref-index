@echo off
rem Build the code-ref-index for a project (makes compile_commands.json if needed).
rem Usage: index-project.bat [-Root <src>] [-Cdb <dir> | -Project <.slnx|.sln|.vcxproj|CMakeLists.txt>] [-Configuration Debug] [-Platform x64] [-Name <config>] [-Jobs N] [-Update]
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0index-project.ps1" %*
if "%~1"=="" pause
