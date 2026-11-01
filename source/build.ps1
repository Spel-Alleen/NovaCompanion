$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$launchCompiler = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
& $launchCompiler /nologo /target:winexe /win32icon:nova.ico /reference:System.Windows.Forms.dll /out:../NovaCompanion.exe Launcher.cs
if ($LASTEXITCODE -ne 0) { throw 'Не удалось собрать файл запуска.' }
Write-Host 'Готово: ..\NovaCompanion.exe. Оставьте папки runtime, source и agent рядом с ним.'
