@echo off
call "%~dp0_common.bat"
%PY% -u run.py --exp stats figures report > logs\final_report.txt 2>&1
echo Done - see results\RESULTS.md and results\figures
pause
