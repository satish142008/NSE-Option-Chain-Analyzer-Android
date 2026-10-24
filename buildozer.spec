[app]
title = NSE Option Chain Analyzer
package.name = nseoptionchain
package.domain = org.nseoca
source.dir = .
source.include_exts = py,png,txt
source.exclude_patterns = NSE_Option_Chain_Analyzer.py,tests/*,.github/*
version = 5.8
requirements = python3==3.11.5,hostpython3==3.11.5,openssl,kivy==2.3.0,requests==2.31.0,urllib3==1.26.18,chardet==3.0.4,idna,certifi,plyer,pyjnius,android
orientation = portrait
fullscreen = 0
icon.filename = %(source.dir)s/assets/icon.png
presplash.filename = %(source.dir)s/assets/presplash.png
android.presplash_color = #0d0d0d
android.permissions = INTERNET,POST_NOTIFICATIONS,WRITE_EXTERNAL_STORAGE
android.api = 33
android.minapi = 24
android.ndk = 25b
android.archs = arm64-v8a, armeabi-v7a
android.accept_sdk_license = True
android.allow_backup = True
p4a.branch = v2024.01.21

[buildozer]
log_level = 2
warn_on_root = 1
