@echo off
call "%~dp0_common.bat"
echo Terminal 2: Exp D (open-set), then Exp C (cross-dataset). Log: logs\t2.txt
%PY% -u run.py --exp D >> logs\t2.txt 2>&1
echo T2_D_FINISHED >> logs\t2.txt
%PY% -u run.py --csv "%CSV%" --exp C --veremi-dir "%VER%" --veremi-max-scen 225 >> logs\t2.txt 2>&1
echo T2_C_FINISHED >> logs\t2.txt
%PY% -u run.py --exp figures report >> logs\t2.txt 2>&1
echo T2_ALL_FINISHED >> logs\t2.txt
