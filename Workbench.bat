@echo off
rem Retroport Workbench: browse and pick items and sets in your browser (http://127.0.0.1:8335/).
rem The first run needs the installs: Workbench.bat --bfa "<BfA install>" --retail "<retail install>"
rem   --client "<3.3.5 client>" --dbd "<WoWDBDefs\definitions>" --listfile "<listfile.csv>"
rem Later runs remember them. Close this window to stop it.
cd /d "%~dp0"
python -m retroport.cli workbench %*
if errorlevel 1 pause
