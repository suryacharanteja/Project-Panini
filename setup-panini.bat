@echo off
setlocal EnableExtensions
cd /d "%~dp0" || exit /b 1
echo PANINI Windows setup
echo.

where winget >nul 2>&1
if errorlevel 1 set "PANINI_NO_WINGET=1"

call :check_node
if errorlevel 1 (
  call :install OpenJS.NodeJS.LTS "Node.js 22.19 or newer"
  if errorlevel 1 exit /b 1
  call :check_node
  if errorlevel 1 goto refresh_path
)

call :check_python
if errorlevel 1 (
  call :install Python.Python.3.12 "Python 3.12"
  if errorlevel 1 exit /b 1
  call :check_python
  if errorlevel 1 goto refresh_path
)

call :check_dotnet
if errorlevel 1 (
  call :install Microsoft.DotNet.SDK.10 ".NET 10 SDK"
  if errorlevel 1 exit /b 1
  call :check_dotnet
  if errorlevel 1 goto refresh_path
)

echo.
echo Installing locked Node and Python dependencies...
call npm.cmd run setup
if errorlevel 1 goto failed

node -e "const fs=require('node:fs'); require.resolve('esbuild'); require.resolve('vite'); if(!fs.existsSync(require('electron'))) process.exit(1)" >nul 2>&1
if errorlevel 1 (
  echo PANINI build dependencies are missing after npm setup.
  goto failed
)

if not exist ".env" (
  copy /y ".env.example" ".env" >nul
  if errorlevel 1 goto failed
  echo Created .env from .env.example. Add your provider keys to enable online features.
)

echo.
echo Building PANINI runtime...
call npm.cmd run panini:build
if errorlevel 1 goto failed

if not exist "panini-core\apps\shell\dist\main\main.cjs" goto failed
if not exist "panini-core\capabilities\desktop\Panini.RuntimeHost\bin\Release\net10.0-windows\Panini.RuntimeHost.exe" goto failed
if not exist "panini-core\capabilities\desktop\Panini.Desktop.Broker\bin\Release\net10.0-windows\Panini.Desktop.Broker.exe" goto failed
echo.
echo PANINI setup complete. Run: npm run panini
exit /b 0

:check_node
where node >nul 2>&1 || exit /b 1
where npm.cmd >nul 2>&1 || exit /b 1
node -e "const v=process.versions.node.split('.').map(Number); process.exit(v[0] < 22 || (v[0] === 22 && v[1] < 19) ? 1 : 0)" >nul 2>&1
exit /b %errorlevel%

:check_python
where py >nul 2>&1 || exit /b 1
py -3.12 -c "import sys; assert sys.version_info[:2] == (3, 12)" >nul 2>&1
exit /b %errorlevel%

:check_dotnet
where dotnet >nul 2>&1 || exit /b 1
dotnet --list-sdks | findstr /r /b "10\." >nul
exit /b %errorlevel%

:install
echo Missing %~2.
if defined PANINI_NO_WINGET (
  echo Install %~2, then run setup-panini.bat again. Windows Package Manager ^(winget^) is unavailable.
  exit /b 1
)
echo Installing %~2 with Windows Package Manager...
winget install --id %~1 --exact --source winget --accept-package-agreements --accept-source-agreements
if errorlevel 1 (
  echo Installation failed. Install %~2 manually and rerun setup-panini.bat.
  exit /b 1
)
exit /b 0

:refresh_path
echo A prerequisite was installed. Open a new terminal and run setup-panini.bat again so Windows refreshes PATH.
exit /b 1

:failed
echo PANINI setup failed. See the error above; npm run panini is not ready yet.
exit /b 1
