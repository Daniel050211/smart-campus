@echo off
cd /d D:\Project\about-principle-partners-limited-about-us\outputs\tm1118

echo Starting MQTT Listener...
start "MQTT Listener" /min "" C:\django-env\Scripts\python.exe mqtt_listener.py
timeout /t 2 >nul

echo Starting Django Web Server...
start "Django Server" /min "" C:\django-env\Scripts\python.exe manage.py runserver 0.0.0.0:8000 --noreload
timeout /t 3 >nul

echo.
echo Both services started!
echo - MQTT Listener is running in background
echo - Django Server at http://127.0.0.1:8000
echo.
echo Close this window when done.
pause
