@echo off
rem Build ltidx (Release). LLVM_ROOT is the extracted official LLVM release
rem (clang+llvm-<ver>-x86_64-pc-windows-msvc). If LLVM_ROOT is not set, the first line of
rem llvm-root.txt next to this file is used (not in git; write your own LLVM folder there).
setlocal
if not defined LLVM_ROOT if exist "%~dp0llvm-root.txt" set /p LLVM_ROOT=<"%~dp0llvm-root.txt"
if not defined LLVM_ROOT (
  echo LLVM_ROOT is not set. Set it, or write the LLVM folder to %~dp0llvm-root.txt
  exit /b 1
)
if not exist "%LLVM_ROOT%\lib\cmake\clang\ClangConfig.cmake" (
  echo LLVM not found: %LLVM_ROOT%  ^(set LLVM_ROOT or edit llvm-root.txt^)
  exit /b 1
)
set "VSWHERE=%ProgramFiles(x86)%\Microsoft Visual Studio\Installer\vswhere.exe"
for /f "usebackq delims=" %%i in (`"%VSWHERE%" -latest -products * -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath`) do set "VSDIR=%%i"
if not defined VSDIR (
  echo Visual Studio with the C++ tools not found
  exit /b 1
)
call "%VSDIR%\VC\Auxiliary\Build\vcvars64.bat" >nul || exit /b 1
set "VSCMAKE=%VSDIR%\Common7\IDE\CommonExtensions\Microsoft\CMake"
set "PATH=%VSCMAKE%\CMake\bin;%VSCMAKE%\Ninja;%PATH%"
cd /d "%~dp0"
cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release "-DClang_DIR=%LLVM_ROOT%\lib\cmake\clang" || exit /b 1
ninja -C build -j 4
