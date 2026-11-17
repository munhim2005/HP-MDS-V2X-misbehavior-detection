@echo off
call "%~dp0_common.bat"
echo Terminal 3: Exp B (density), G (delay + errors), ONNX, pseudonym check; then 5-seed top-ups. Log: logs\t3.txt
%PY% -m pip install onnx onnxruntime onnxscript >> logs\t3.txt 2>&1
%PY% -u run.py --exp B G onnx --b-seeds 3 >> logs\t3.txt 2>&1
echo T3_BG_FINISHED >> logs\t3.txt
%PY% -u run.py --csv "%CSV%" --stream-by pseudo --exp prepare A --seeds 3 --no-secondary --out results_pseudo >> logs\t3.txt 2>&1
echo T3_PSEUDO_FINISHED >> logs\t3.txt
:wait1
findstr /c:"T1_ALL_FINISHED" logs\t1.txt >nul 2>nul || (timeout /t 60 /nobreak >nul & goto wait1)
:wait2
findstr /c:"T2_ALL_FINISHED" logs\t2.txt >nul 2>nul || (timeout /t 60 /nobreak >nul & goto wait2)
echo T3_TOPUP_STARTED >> logs\t3.txt
%PY% -u run.py --exp D --open-seeds 5 >> logs\t3.txt 2>&1
%PY% -u run.py --exp ablation --abl-seeds 5 >> logs\t3.txt 2>&1
%PY% -u run.py --csv "%CSV%" --exp C --veremi-dir "%VER%" --veremi-max-scen 225 --c-seeds 5 >> logs\t3.txt 2>&1
%PY% -u run.py --exp B --b-seeds 5 >> logs\t3.txt 2>&1
%PY% -u run.py --exp stats figures report >> logs\t3.txt 2>&1
echo T3_ALL_FINISHED >> logs\t3.txt
