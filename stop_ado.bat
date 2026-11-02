@echo off
rem Force-closes ado:3 if she disappeared or froze (closes any Python program running main.py).
powershell -NoProfile -Command "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -like '*main.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo ado:3 stopped. Start her again with the desktop shortcut.
timeout /t 3 >nul
