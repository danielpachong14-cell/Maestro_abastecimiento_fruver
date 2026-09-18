"""Instalador del Distribuidor Fruver.

Pide una carpeta destino, instala ahi el programa (Distribuidor_Fruver_Proyecto.exe
+ Input/ de ejemplo con los archivos actuales + Output/ vacia), y crea un acceso
directo en el Escritorio con icono de manzana.

Se distribuye como Instalador_Distribuidor_Fruver.exe (ver Generar_Exe.bat) --
no requiere Python instalado en la maquina destino. Sin consola (--windowed):
todo feedback al usuario pasa por messagebox, nunca por texto en una terminal
que no existe.
"""
import shutil
import subprocess
import sys
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

APP_FOLDER_NAME = "Distribuidor Fruver"
WORKER_EXE_NAME = "Distribuidor_Fruver_Proyecto.exe"
ICON_NAME = "manzana.ico"


def _payload_dir() -> Path:
    return Path(sys._MEIPASS) / "payload"


def _crear_acceso_directo(exe_path: Path, icon_path: Path) -> None:
    # El Escritorio se resuelve DENTRO de PowerShell con GetFolderPath('Desktop'):
    # Path.home()/"Desktop" da la carpeta "plana" (%USERPROFILE%\Desktop), que NO
    # existe cuando el Escritorio esta redirigido a OneDrive, y ahi Save() falla.
    ps_script = (
        "$shell = New-Object -ComObject WScript.Shell;"
        "$desktop = [Environment]::GetFolderPath('Desktop');"
        "if (-not (Test-Path $desktop)) { New-Item -ItemType Directory -Path $desktop -Force | Out-Null };"
        f"$lnk = Join-Path $desktop '{APP_FOLDER_NAME}.lnk';"
        "$s = $shell.CreateShortcut($lnk);"
        f"$s.TargetPath = '{exe_path}';"
        f"$s.WorkingDirectory = '{exe_path.parent}';"
        f"$s.IconLocation = '{icon_path},0';"
        "$s.Description = 'Ejecuta la distribucion automatica de Fruver (Input/ -> Output/)';"
        "$s.Save()"
    )
    subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_script],
        check=True, capture_output=True, text=True,
    )


def instalar(destino_base: Path) -> Path:
    payload = _payload_dir()
    destino = destino_base / APP_FOLDER_NAME
    destino.mkdir(parents=True, exist_ok=True)

    shutil.copy2(payload / WORKER_EXE_NAME, destino / WORKER_EXE_NAME)

    # No pisar Input/ si ya existe (podria tener datos reales de una instalacion anterior).
    if not (destino / "Input").exists():
        shutil.copytree(payload / "Input", destino / "Input")
    (destino / "Output").mkdir(exist_ok=True)

    # El icono se copia al destino: el del payload vive en el temp _MEIxxxx de
    # PyInstaller, que se borra al cerrar el instalador y dejaria el acceso
    # directo sin icono. El de destino persiste junto al programa.
    shutil.copy2(payload / ICON_NAME, destino / ICON_NAME)

    _crear_acceso_directo(destino / WORKER_EXE_NAME, destino / ICON_NAME)
    return destino


def main():
    root = tk.Tk()
    root.withdraw()

    destino_base = filedialog.askdirectory(title="¿Dónde quieres guardar el Distribuidor Fruver?")
    if not destino_base:
        return

    try:
        destino = instalar(Path(destino_base))
    except Exception as e:
        messagebox.showerror("Error de instalación", f"No se pudo instalar:\n{e}")
        return

    messagebox.showinfo(
        "Instalación completa",
        f"Instalado en:\n{destino}\n\n"
        "Se creó un acceso directo 'Distribuidor Fruver' en el Escritorio.\n\n"
        "Antes de correrlo, actualiza los archivos de Input/ con los datos del día.",
    )


if __name__ == "__main__":
    main()
