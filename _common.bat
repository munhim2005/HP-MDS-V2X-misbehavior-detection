@echo off
cd /d "%~dp0"
if not exist logs mkdir logs
set "CSV=..\VeReMi_Extension Dataset for Misbehaviors in VANETs\mixalldata_clean.csv"
set "VER=..\veremi_original"
set PY=python
python --version >nul 2>nul || set PY=py
set PYTHONUNBUFFERED=1
