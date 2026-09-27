@echo off
REM -----------------------------------------------------------------------
REM NeonFish one-click setup. This is the double-click entry.
REM What it does: build .venv -> install requirements -> download Chromium
REM -> hand over to selfcheck.py, which prints the 4-line health report.
REM The system Python interpreter and every data file are left alone.
REM
REM These comments are ASCII on purpose: on a GBK code page non-ASCII text
REM inside a .bat turns into mojibake. The single Chinese line below is an
REM echo kept OUTSIDE the if-block, because multi-byte bytes inside a
REM parenthesized block are what some cmd builds mis-parse; the chcp 65001
REM above is what makes those UTF-8 bytes render. installer.py repeats the
REM same guidance in Chinese as its own last line, so nothing important
REM depends on this one line.
REM Line endings must stay CRLF (.gitattributes: *.bat text eol=crlf).
REM -----------------------------------------------------------------------
setlocal
cd /d "%~dp0"
chcp 65001 >nul

where py >nul 2>nul
if %errorlevel%==0 (
  py -3 -X utf8 installer.py
) else (
  python -X utf8 installer.py
)

if %errorlevel% neq 0 goto _failed

echo.
pause
endlocal
exit /b 0

:_failed
echo.
echo 安装没有完成：把上面 FAIL 那一行截图发给维护者，按提示处理后重新双击本文件。
echo.
pause
endlocal
exit /b 1
