@echo off
rem Regression test: index sample.cpp and compare with expected*.tsv
setlocal
cd /d "%~dp0"
if not exist out mkdir out
set "D=%~dp0"
set "D=%D:\=/%"
> out\compile_commands.json echo [{"directory":"%D%","file":"%D%sample.cpp","arguments":["cl.exe","/c","/std:c++17","sample.cpp"]}]
..\build\ltidx.exe -p out -root . -o out\sample.tsv 2> out\stderr.txt || (type out\stderr.txt & exit /b 1)
set RC=0
fc /n expected.tsv out\sample.tsv >nul || (echo FAIL: sample.tsv differs & fc /n expected.tsv out\sample.tsv & set RC=1)
fc /n expected.tsv.skip.tsv out\sample.tsv.skip.tsv >nul || (echo FAIL: sample.tsv.skip.tsv differs & set RC=1)
rem /WX: clang-only warnings must not fail the TU
if not exist out\werror mkdir out\werror
> out\werror\compile_commands.json echo [{"directory":"%D%","file":"%D%werror.cpp","arguments":["cl.exe","/c","/std:c++17","/W4","/WX","werror.cpp"]}]
..\build\ltidx.exe -p out\werror -root . -o out\werror\werror.tsv 2> out\werror\stderr.txt || (echo FAIL: werror.cpp failed with /WX & type out\werror\stderr.txt & set RC=1)
rem The source line of a column-1 reference must not include the previous line
findstr /c:"werror.cpp:11:1" out\werror\werror.tsv >nul || (echo FAIL: no reference at werror.cpp:11:1 & set RC=1)
findstr /c:"Value(); Base" out\werror\werror.tsv >nul && (echo FAIL: source line joined with the previous line & set RC=1)
if %RC%==0 echo PASS
pause
exit /b %RC%
