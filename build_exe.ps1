$ErrorActionPreference = 'Stop'
& .\.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --name "AP-Invoice-OCR" --add-data "app\static;app\static" run.py
Write-Host "Built executable: $PWD\dist\AP-Invoice-OCR.exe"
