@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Tests MaroIFRS
set PY=
where python >nul 2>nul && set PY=python
if "%PY%"=="" where py >nul 2>nul && set PY=py
if "%PY%"=="" (
  echo Python n'est pas installe.
  pause
  exit /b 1
)
%PY% tests\run_tests.py
pause
