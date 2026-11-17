@echo off
call "%~dp0_common.bat"
set "LOG=logs\setup_log.txt"
echo ==== SETUP %date% %time% ==== > "%LOG%"
echo [1/4] Checking Python and GPU...
where python >> "%LOG%" 2>&1
%PY% --version >> "%LOG%" 2>&1
nvidia-smi >> "%LOG%" 2>&1
echo [2/4] Installing packages (a few minutes)...
%PY% -m pip install --upgrade pip >> "%LOG%" 2>&1
%PY% -c "import torch,sys;sys.exit(0 if torch.cuda.is_available() else 1)" >nul 2>nul || %PY% -m pip install --force-reinstall torch --index-url https://download.pytorch.org/whl/cu128 >> "%LOG%" 2>&1
%PY% -m pip install -r requirements.txt >> "%LOG%" 2>&1
%PY% -c "import torch;print('TORCH', torch.__version__, 'CUDA', torch.cuda.is_available(), torch.cuda.get_device_name(0) if torch.cuda.is_available() else '')" >> "%LOG%" 2>&1
echo [3/4] Smoke test on 5%% of vehicles (about 5-10 min)...
echo ==== SMOKE TEST ==== >> "%LOG%"
%PY% -u run.py --csv "%CSV%" --exp prepare A D E F stats ablation C figures report --quick --veremi-dir "%VER%" --veremi-max-scen 10 >> "%LOG%" 2>&1
echo [4/4] Done.
echo SETUP_FINISHED >> "%LOG%"
echo You can close this window.
