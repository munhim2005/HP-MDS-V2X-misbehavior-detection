@echo off
call "%~dp0_common.bat"
echo Final checks: time jitter, adversarial, permutation importance, memory, OSCR. Log: logs\t4.txt
%PY% -m pip install psutil >> logs\t4.txt 2>&1
%PY% -u run.py --exp H >> logs\t4.txt 2>&1
%PY% -u run.py --exp figures report >> logs\t4.txt 2>&1
echo T4_ALL_FINISHED >> logs\t4.txt
