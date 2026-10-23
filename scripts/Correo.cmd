@echo off
rem Abre Thunderbird con el perfil de PERSONAL_OS (aqui ves TODAS tus cuentas y el sistema las lee en solo lectura).
rem El perfil vive en data\mail-profile (fuera de AppData a proposito). Dejalo abierto un rato para que sincronice.
start "" "%LOCALAPPDATA%\Programs\Thunderbird\thunderbird.exe" -profile "%~dp0..\data\mail-profile"
