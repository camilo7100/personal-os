@echo off
rem Quita el PIN del portal (no se pierde ningun dato). Solo funciona con tu sesion de Windows. Al abrir el portal crearas uno nuevo.
title Restablecer PIN - PERSONAL_OS
chcp 65001 >nul
call "%~dp0pos.cmd" portal reset-pin
echo.
echo Ahora abre el Portal del Escritorio y crea un PIN nuevo.
pause
