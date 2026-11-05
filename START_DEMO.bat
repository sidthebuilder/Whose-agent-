@echo off
rem The demo video: double-click this file. It wakes the model, makes a fresh room, then opens Edge full
rem screen and runs the demo by itself, speaking the narration. The video is saved to demo\out\demo.mp4.
cd /d "%~dp0"
title Whose agent is this? - demo
echo.
echo  Whose agent is this? - the demo video
echo.
echo  Before Edge opens: turn on Do Not Disturb, and close anything that could pop up.
echo  Once Edge opens, don't touch the mouse or keyboard until it closes (about 3 minutes).
echo  The narration plays aloud. The video is saved to demo\out\demo.mp4 by itself.
echo.
if not exist ".venv\Scripts\python.exe" (
  echo  No .venv: python -m venv .venv  then  .venv\Scripts\python -m pip install -r demo\requirements.txt modal
  pause
  exit /b 1
)
".venv\Scripts\python.exe" scripts\record_demo.py %*
echo.
if exist "demo\out\demo.mp4" echo  Video: %cd%\demo\out\demo.mp4
pause
