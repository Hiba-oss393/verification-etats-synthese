@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Plateforme IFRS

set PY=
where python >nul 2>nul && set PY=python
if "%PY%"=="" where py >nul 2>nul && set PY=py
if "%PY%"=="" (
  echo Python n'est pas installe sur ce PC.
  echo Installez-le depuis python.org en cochant "Add Python to PATH", puis relancez ce fichier.
  pause
  exit /b 1
)

rem --- Verification de TOUS les composants (pas seulement Streamlit) et des fichiers du projet ---
%PY% verifier_dependances.py
set CODE=%errorlevel%
if "%CODE%"=="2" (
  pause
  exit /b 1
)
if "%CODE%"=="3" (
  pause
  exit /b 1
)
if "%CODE%"=="1" (
  echo.
  echo Installation des composants manquants ^(Internet necessaire une seule fois^)...
  %PY% -m pip install --disable-pip-version-check -r requirements.txt
  if errorlevel 1 (
    echo Installation groupee echouee : essai composant par composant...
    for /f "usebackq tokens=* delims=" %%P in ("requirements.txt") do %PY% -m pip install --disable-pip-version-check "%%P"
  )
  echo.
  echo Nouvelle verification...
  %PY% verifier_dependances.py --apres-installation
  if errorlevel 1 (
    echo.
    echo Des composants indispensables manquent toujours. Verifiez la connexion Internet, puis relancez ce fichier.
    pause
    exit /b 1
  )
)

rem --- Fonctionnement hors ligne : pas de question e-mail au premier lancement, pas d'envoi de statistiques ---
if not exist "%USERPROFILE%\.streamlit\credentials.toml" (
  mkdir "%USERPROFILE%\.streamlit" >nul 2>nul
  echo [general]> "%USERPROFILE%\.streamlit\credentials.toml"
  echo email = "">> "%USERPROFILE%\.streamlit\credentials.toml"
)
set STREAMLIT_BROWSER_GATHER_USAGE_STATS=false
set OPTIONS=--server.address localhost --browser.gatherUsageStats false

echo.
echo Demarrage de la plateforme... Le navigateur va s'ouvrir.
echo NE FERMEZ PAS cette fenetre noire tant que vous utilisez la plateforme.
%PY% -m streamlit run app.py %OPTIONS%

set NB=0
:relance
set /a NB+=1
if %NB% gtr 10 (
  echo.
  echo Le serveur s'est arrete trop souvent. Lisez le message d'erreur ci-dessus, puis relancez ce fichier.
  pause
  exit /b 1
)
echo.
echo Le serveur s'est arrete. Redemarrage automatique dans 3 secondes... ^(essai %NB% sur 10^)
echo La page deja ouverte se reconnecte toute seule. Fermez cette fenetre pour quitter definitivement.
timeout /t 3 /nobreak >nul
%PY% -m streamlit run app.py %OPTIONS% --server.headless true
goto relance
