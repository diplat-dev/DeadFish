@echo off
setlocal EnableExtensions

set "ROOT=%~dp0"
set "LAUNCH_GUI=1"
set "PAUSE_ON_FAIL=1"
set "PYTHON_LAUNCHER="
set "PYTHON_ARGS="
set "PYTHONW_LAUNCHER="
set "PYTHONW_ARGS="

:parse_args
if "%~1"=="" goto :after_args
if /I "%~1"=="--no-launch" (
    set "LAUNCH_GUI=0"
    shift
    goto :parse_args
)
if /I "%~1"=="--no-pause" (
    set "PAUSE_ON_FAIL=0"
    shift
    goto :parse_args
)

:after_args

cd /d "%ROOT%"
if errorlevel 1 goto :fail

call :resolve_python
if errorlevel 1 goto :fail

if "%LAUNCH_GUI%"=="1" (
    echo [1/6] Checking existing engine build...
    call :engine_build_current
    if not errorlevel 1 (
        call :gui_runtime_available
        if not errorlevel 1 (
            echo Existing engine build is current. Launching GUI without rebuild.
            call :launch_gui %*
            set "EXIT_CODE=0"
            goto :end
        )
        echo Existing engine build is current, but GUI runtime is not ready. Continuing with setup...
    ) else (
        echo Existing engine build is missing or stale. Rebuilding...
    )
)

echo [1/6] Clearing generated files...
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "foreach ($path in @('build', '.tmp_pydeps', '.gui_pydeps', '.wheelhouse')) { " ^
    "  if (Test-Path $path) { Remove-Item -LiteralPath $path -Recurse -Force -ErrorAction SilentlyContinue } " ^
    "}; " ^
    "Get-ChildItem -Path '%ROOT%' -Recurse -Directory -Filter __pycache__ -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue; " ^
    "Get-ChildItem -Path '%ROOT%' -Recurse -File -Include *.pyc -ErrorAction SilentlyContinue | Remove-Item -Force -ErrorAction SilentlyContinue; " ^
    "exit 0"
if errorlevel 1 goto :fail

echo [2/6] Rebuilding engine...
powershell -NoProfile -ExecutionPolicy Bypass -File "%ROOT%scripts\build_native.ps1" -Target All
if errorlevel 1 goto :fail

echo [3/6] Running native verification tests...
"%ROOT%build\deadfish_tests.exe"
if errorlevel 1 goto :fail

"%ROOT%build\deadfish_tests_native.exe"
if errorlevel 1 goto :fail

echo [4/6] Running Python verification tests...
%PYTHON_LAUNCHER% %PYTHON_ARGS% "%ROOT%scripts\uci_smoke.py"
if errorlevel 1 goto :fail

%PYTHON_LAUNCHER% %PYTHON_ARGS% "%ROOT%scripts\tactical_suite.py"
if errorlevel 1 goto :fail

echo [5/6] Setting up GUI runtime...
set "GUI_RUNTIME_READY=0"
%PYTHON_LAUNCHER% %PYTHON_ARGS% -c "import sys; sys.path.insert(0, r'%ROOT%.gui_pydeps'); import chess; raise SystemExit(0 if hasattr(chess, 'Board') else 1)" >nul 2>&1
if not errorlevel 1 set "GUI_RUNTIME_READY=1"

if "%GUI_RUNTIME_READY%"=="0" (
    %PYTHON_LAUNCHER% %PYTHON_ARGS% -c "import pathlib, sys; vendor = pathlib.Path(r'%ROOT%vendor'); matches = sorted(vendor.glob('chess-*')); raise SystemExit(1 if not matches else 0)" >nul 2>&1
    if not errorlevel 1 (
        %PYTHON_LAUNCHER% %PYTHON_ARGS% -c "import pathlib, sys; vendor = pathlib.Path(r'%ROOT%vendor'); matches = sorted(vendor.glob('chess-*')); sys.path.insert(0, str(matches[-1])); import chess; raise SystemExit(0 if hasattr(chess, 'Board') else 1)" >nul 2>&1
        if not errorlevel 1 set "GUI_RUNTIME_READY=1"
    )
)

if "%GUI_RUNTIME_READY%"=="0" (
    %PYTHON_LAUNCHER% %PYTHON_ARGS% -m pip install --upgrade --target "%ROOT%.gui_pydeps" -r "%ROOT%gui\requirements.txt"
    if not errorlevel 1 (
        %PYTHON_LAUNCHER% %PYTHON_ARGS% -c "import sys; sys.path.insert(0, r'%ROOT%.gui_pydeps'); import chess; raise SystemExit(0 if hasattr(chess, 'Board') else 1)" >nul 2>&1
        if not errorlevel 1 set "GUI_RUNTIME_READY=1"
    )
)

if "%GUI_RUNTIME_READY%"=="0" (
    echo Repo-local pip target is unavailable. Falling back to a vendored chess source extract...
    %PYTHON_LAUNCHER% %PYTHON_ARGS% -m pip download chess -d "%ROOT%.wheelhouse"
    if errorlevel 1 goto :fail

    powershell -NoProfile -ExecutionPolicy Bypass -Command ^
        "$vendor = Join-Path '%ROOT%' 'vendor'; " ^
        "if (!(Test-Path $vendor)) { New-Item -ItemType Directory -Path $vendor | Out-Null }; " ^
        "Get-ChildItem -Path $vendor -Directory -Filter 'chess-*' -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue; " ^
        "$archive = Get-ChildItem -Path '%ROOT%.wheelhouse' -Filter 'chess-*.tar.gz' | Sort-Object LastWriteTime -Descending | Select-Object -First 1; " ^
        "if ($null -eq $archive) { throw 'No chess source archive was downloaded.' }; " ^
        "tar -xf $archive.FullName -C $vendor; " ^
        "exit 0"
    if errorlevel 1 goto :fail

    %PYTHON_LAUNCHER% %PYTHON_ARGS% -c "import pathlib, sys; vendor = pathlib.Path(r'%ROOT%vendor'); matches = sorted(vendor.glob('chess-*')); sys.path.insert(0, str(matches[-1])); import chess; raise SystemExit(0 if hasattr(chess, 'Board') else 1)"
    if errorlevel 1 goto :fail
    set "GUI_RUNTIME_READY=1"
)

if "%GUI_RUNTIME_READY%"=="0" goto :fail

%PYTHON_LAUNCHER% %PYTHON_ARGS% "%ROOT%scripts\gui_smoke.py"
if errorlevel 1 goto :fail

if "%LAUNCH_GUI%"=="0" (
    echo [6/6] Quickstart completed. GUI launch skipped.
    set "EXIT_CODE=0"
    goto :end
)

echo [6/6] Launching GUI...
call :launch_gui %*
set "EXIT_CODE=0"
goto :end

:fail
set "EXIT_CODE=%ERRORLEVEL%"
if "%EXIT_CODE%"=="" set "EXIT_CODE=1"
echo.
echo Quickstart failed with exit code %EXIT_CODE%.
if "%PAUSE_ON_FAIL%"=="1" pause

:end
endlocal & exit /b %EXIT_CODE%

:resolve_python
where python >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_LAUNCHER=python"
    where pythonw >nul 2>&1
    if not errorlevel 1 set "PYTHONW_LAUNCHER=pythonw"
    goto :verify_python
)

if exist "C:\Python314\python.exe" (
    set "PYTHON_LAUNCHER=C:\Python314\python.exe"
    if exist "C:\Python314\pythonw.exe" set "PYTHONW_LAUNCHER=C:\Python314\pythonw.exe"
    goto :verify_python
)

where py >nul 2>&1
if not errorlevel 1 (
    set "PYTHON_LAUNCHER=py"
    set "PYTHON_ARGS=-3"
    where pyw >nul 2>&1
    if not errorlevel 1 (
        set "PYTHONW_LAUNCHER=pyw"
        set "PYTHONW_ARGS=-3"
    )
    goto :verify_python
)

echo Python 3 was not found. Install Python 3 and then run quickstart again.
exit /b 1

:verify_python
%PYTHON_LAUNCHER% %PYTHON_ARGS% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo Python 3.10+ is required to run DeadFish tools and the GUI.
    exit /b 1
)
exit /b 0

:engine_build_current
powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$root = '%ROOT%'; " ^
    "$artifacts = @('build\deadfish.exe', 'build\deadfish_native.exe'); " ^
    "foreach ($artifact in $artifacts) { if (!(Test-Path (Join-Path $root $artifact))) { exit 1 } }; " ^
    "$oldestArtifact = $artifacts | ForEach-Object { (Get-Item (Join-Path $root $_)).LastWriteTimeUtc } | Sort-Object | Select-Object -First 1; " ^
    "$inputs = @('engine', 'cli', 'third_party\fathom', 'scripts\build_native.ps1'); " ^
    "$latestInput = [DateTime]::MinValue; " ^
    "foreach ($input in $inputs) { " ^
    "  $path = Join-Path $root $input; " ^
    "  if (!(Test-Path $path)) { continue }; " ^
    "  $item = Get-Item $path; " ^
    "  if ($item.PSIsContainer) { " ^
    "    $latest = Get-ChildItem -LiteralPath $path -Recurse -File -ErrorAction SilentlyContinue | Sort-Object LastWriteTimeUtc -Descending | Select-Object -First 1; " ^
    "    if ($null -ne $latest -and $latest.LastWriteTimeUtc -gt $latestInput) { $latestInput = $latest.LastWriteTimeUtc } " ^
    "  } elseif ($item.LastWriteTimeUtc -gt $latestInput) { $latestInput = $item.LastWriteTimeUtc } " ^
    "}; " ^
    "if ($latestInput -gt $oldestArtifact) { exit 1 }; " ^
    "exit 0"
exit /b %ERRORLEVEL%

:gui_runtime_available
%PYTHON_LAUNCHER% %PYTHON_ARGS% -c "import gui; import chess; raise SystemExit(0 if hasattr(chess, 'Board') else 1)" >nul 2>&1
exit /b %ERRORLEVEL%

:launch_gui
if defined PYTHONW_LAUNCHER (
    start "" "%PYTHONW_LAUNCHER%" %PYTHONW_ARGS% -m gui %*
) else (
    start "" "%PYTHON_LAUNCHER%" %PYTHON_ARGS% -m gui %*
)
exit /b 0
