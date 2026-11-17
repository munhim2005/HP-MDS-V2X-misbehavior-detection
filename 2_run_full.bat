@echo off
call "%~dp0_common.bat"
echo Keeping the PC awake while plugged in (no sleep)...
powercfg /change standby-timeout-ac 0 >nul 2>nul
powercfg /change hibernate-timeout-ac 0 >nul 2>nul
echo [1/2] Building full data cache (10-20 min)...
%PY% -u run.py --csv "%CSV%" --exp prepare > logs\prepare.txt 2>&1
echo PREPARE_FINISHED >> logs\prepare.txt
echo [2/2] Starting the two experiment windows...
start "HP-MDS terminal 1 (A,E,F,stats,ablation)" cmd /c "%~dp0_t1.bat"
start "HP-MDS terminal 2 (D, C)" cmd /c "%~dp0_t2.bat"
echo Both started. You can close this window; do NOT close the two new ones.
