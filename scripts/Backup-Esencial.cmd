@echo off
rem DOBLE CLIC con el disco externo conectado: copia lo ESENCIAL (~0,4 GB: sistema, proyectos, estudio).
rem Traslada la copia preparada al disco y recorta lo local para que no se acumule. Tarda pocos minutos.
title Backup ESENCIAL - PERSONAL_OS
chcp 65001 >nul
call "%~dp0pos.cmd" backup external essential --interactive
echo.
echo Puedes cerrar esta ventana. Si dice OK, ya puedes desconectar el disco.
pause
