@echo off
cd /d "%~dp0"
python -m pip install -q -r requirements.txt pyinstaller
python -m PyInstaller --noconsole --onefile --name WindowTint --paths src --collect-data sv_ttk src\main.py
echo Built dist\WindowTint.exe
