[app]
title = Рядом: аптеки и банкоматы
package.name = nearbyfinder
package.domain = org.example

source.dir = .
source.include_exts = py,png,jpg,kv,atlas,ttf,db
source.include_dirs = ru_data

version = 1.22

requirements = python3,kivy==2.3.1,filetype,plyer,requests,kivy_garden.mapview,pyjnius,android,libffi,openssl,setuptools,six,certifi

orientation = portrait
fullscreen = 0

icon.filename = %(source.dir)s/assets/icon.png
presplash.filename = %(source.dir)s/assets/presplash.png
android.presplash_color = #0d1117

android.permissions = INTERNET,ACCESS_FINE_LOCATION,ACCESS_COARSE_LOCATION
android.api = 33
android.minapi = 21
android.archs = arm64-v8a
android.accept_sdk_license = True
android.allow_backup = True

p4a.fork = kivy
p4a.branch = release-2024.01.21

[buildozer]
log_level = 2
warn_on_root = 1
