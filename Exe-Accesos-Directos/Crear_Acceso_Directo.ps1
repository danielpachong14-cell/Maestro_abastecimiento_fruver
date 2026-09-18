# Crea (o actualiza) el acceso directo del Distribuidor Fruver en el Escritorio.
# Se puede correr varias veces sin duplicar accesos directos (CreateShortcut
# sobreescribe el mismo archivo .lnk si ya existe).

$appDir = Join-Path $PSScriptRoot "..\Distribucion-Fruver"
$exePath = Join-Path $appDir "Distribuidor_Fruver_Proyecto.exe"
$iconPath = Join-Path $PSScriptRoot "assets\manzana.ico"

if (-not (Test-Path $exePath)) {
    Write-Host "[ERROR] No se encontro $exePath -- corre Generar_Exe.bat primero." -ForegroundColor Red
    exit 1
}

$exePath = (Resolve-Path $exePath).Path
$appDir = (Resolve-Path $appDir).Path

$desktop = [Environment]::GetFolderPath("Desktop")
$lnkPath = Join-Path $desktop "Distribuidor Fruver.lnk"

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($lnkPath)
$shortcut.TargetPath = $exePath
$shortcut.WorkingDirectory = $appDir
$shortcut.IconLocation = "$iconPath,0"
$shortcut.Description = "Ejecuta la distribucion automatica de Fruver (Input/ -> Output/)"
$shortcut.Save()

Write-Host "[OK] Acceso directo creado/actualizado: $lnkPath"
