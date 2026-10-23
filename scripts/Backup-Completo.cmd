@echo off
rem DOBLE CLIC con el disco externo conectado: copia TODO el PC (Documentos, Imagenes, Videos, Musica, Escritorio, Descargas...).
rem La primera vez tarda horas; despues solo copia lo nuevo. Usalo cuando quieras una copia total.
title Backup COMPLETO - PERSONAL_OS
chcp 65001 >nul
call "%~dp0pos.cmd" backup external full --interactive
echo.
echo Puedes cerrar esta ventana. Si dice OK, ya puedes desconectar el disco.
pause
