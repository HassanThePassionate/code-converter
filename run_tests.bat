@echo off
setlocal
cd /d "%~dp0"
.\.venv\Scripts\pytest.exe cobol2go-poc\tests -v %*
