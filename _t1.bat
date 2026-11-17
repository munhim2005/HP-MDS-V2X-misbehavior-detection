@echo off
call "%~dp0_common.bat"
echo Terminal 1: Exp A, E, F, stats, then ablations. Log: logs\t1.txt
%PY% -u run.py --exp A E F stats --seeds 5 >> logs\t1.txt 2>&1
echo T1_PART1_FINISHED >> logs\t1.txt
%PY% -u run.py --exp ablation >> logs\t1.txt 2>&1
echo T1_ABLATION_FINISHED >> logs\t1.txt
%PY% -u run.py --exp stats figures report >> logs\t1.txt 2>&1
echo T1_ALL_FINISHED >> logs\t1.txt
