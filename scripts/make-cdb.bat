@echo off
rem Make compile_commands.json from a Visual Studio solution/project or a CMake project.
rem Usage: make-cdb.bat [-Root <src>] [-Project <.slnx|.sln|.vcxproj|CMakeLists.txt>] [-Configuration Debug] [-Platform x64] [-Out <dir>]
pwsh -NoProfile -ExecutionPolicy Bypass -File "%~dp0make-cdb.ps1" %*
if "%~1"=="" pause
