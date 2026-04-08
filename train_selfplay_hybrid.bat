@echo off
setlocal

set "GAMES=%~1"
if not defined GAMES set "GAMES=500"

set "WORKERS=%~2"
if not defined WORKERS set "WORKERS=20"

set "TEACHER_NODES=%~3"
if not defined TEACHER_NODES set "TEACHER_NODES=50000"

set "EPOCHS=%~4"
if not defined EPOCHS set "EPOCHS=8"

set "SELFPLAY_TC=%~5"
if not defined SELFPLAY_TC set "SELFPLAY_TC=1+0.01"

set "GATE_MODE=%~6"
if not defined GATE_MODE set "GATE_MODE=promote"

set "ENGINE=.\build\deadfish_native.exe"

echo.
echo DeadFish Classical-Teacher Residual NNUE Champion Loop
echo   games: %GAMES%
echo   worker budget: %WORKERS%
echo   teacher nodes: %TEACHER_NODES%
echo   epochs: %EPOCHS%
echo   self-play tc: %SELFPLAY_TC%
echo   gate mode: %GATE_MODE%
echo.

if not exist "%ENGINE%" (
  echo Native engine build not found. Building now...
  powershell -ExecutionPolicy Bypass -File .\scripts\build_native.ps1 -Target All
  if errorlevel 1 goto :fail
)

if not exist "%ENGINE%" (
  echo Failed to produce %ENGINE%
  goto :fail
)

python .\training\run_selfplay_hybrid_loop.py --games %GAMES% --workers %WORKERS% --teacher-nodes %TEACHER_NODES% --epochs %EPOCHS% --selfplay-tc %SELFPLAY_TC% --gate-mode %GATE_MODE%
if errorlevel 1 goto :fail

echo.
echo Champion loop finished successfully.
echo.
exit /b 0

:fail
echo.
echo Champion loop failed.
echo.
exit /b 1
