@echo off
rem 用本地服务打开原型（贴图需要 http，file:// 下浏览器不让读本地图片）
cd /d "%~dp0"

set PORT=8770
where python >nul 2>nul
if %errorlevel%==0 (
  set PY=python
) else (
  where py >nul 2>nul
  if %errorlevel%==0 (set PY=py) else (
    echo 找不到 python，请先安装 Python 3，或直接用正式版客户端。
    pause
    exit /b 1
  )
)

echo 打开 http://127.0.0.1:%PORT%/index.html
start "" "http://127.0.0.1:%PORT%/index.html"
%PY% -m http.server %PORT% --bind 127.0.0.1
