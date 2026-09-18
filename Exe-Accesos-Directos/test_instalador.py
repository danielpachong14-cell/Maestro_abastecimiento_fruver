"""Test de validacion del instalador (sin GUI).

Ejercita `instalador.instalar()` de punta a punta contra una carpeta temporal,
usando un payload de prueba (assets/manzana.ico + un exe dummy) en vez del real,
para no depender del .exe de PyInstaller ni de sys._MEIPASS.

Valida el arreglo del bug de OneDrive: el acceso directo debe crearse en el
Escritorio REAL (GetFolderPath('Desktop'), que respeta la redireccion a OneDrive)
y su icono debe apuntar a la carpeta instalada, no al temp _MEIxxxx.

Correr:  python Exe-Accesos-Directos/test_instalador.py
"""
import subprocess
import sys
import tempfile
from pathlib import Path

import instalador

HERE = Path(__file__).resolve().parent


def _armar_payload(payload_dir: Path) -> None:
    """Reproduce la estructura de sys._MEIPASS/payload con datos de prueba."""
    payload_dir.mkdir(parents=True, exist_ok=True)
    (payload_dir / instalador.WORKER_EXE_NAME).write_bytes(b"dummy exe")
    (payload_dir / "Input").mkdir(exist_ok=True)
    (payload_dir / "Input" / "ejemplo.txt").write_text("dato de ejemplo")
    # Icono real del repo, para que IconLocation apunte a algo valido.
    icon_src = HERE / "assets" / instalador.ICON_NAME
    (payload_dir / instalador.ICON_NAME).write_bytes(icon_src.read_bytes())


def _leer_shortcut(lnk_path: Path) -> dict:
    """Devuelve TargetPath / IconLocation / WorkingDirectory del .lnk via PowerShell."""
    ps = (
        "$s = (New-Object -ComObject WScript.Shell)."
        f"CreateShortcut('{lnk_path}');"
        "Write-Output $s.TargetPath;"
        "Write-Output $s.IconLocation;"
        "Write-Output $s.WorkingDirectory"
    )
    out = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        check=True, capture_output=True, text=True,
    ).stdout.splitlines()
    return {"target": out[0], "icon": out[1], "working": out[2]}


def _desktop_real() -> Path:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "[Environment]::GetFolderPath('Desktop')"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return Path(out)


def test_instalar(monkeypatch=None):
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        payload_dir = tmp / "payload"
        _armar_payload(payload_dir)

        # Redirigir _payload_dir() al payload de prueba (sin sys._MEIPASS).
        instalador._payload_dir = lambda: payload_dir

        destino_base = tmp / "instalacion"
        destino_base.mkdir()

        lnk = _desktop_real() / f"{instalador.APP_FOLDER_NAME}.lnk"
        creado_por_test = not lnk.exists()
        try:
            destino = instalador.instalar(destino_base)

            # 1) Contenido instalado.
            assert (destino / instalador.WORKER_EXE_NAME).exists(), "falta el exe"
            assert (destino / "Input" / "ejemplo.txt").exists(), "falta Input/"
            assert (destino / "Output").is_dir(), "falta Output/"
            assert (destino / instalador.ICON_NAME).exists(), \
                "el icono no se copio al destino (quedaria roto al borrarse el temp)"

            # 2) Acceso directo en el Escritorio REAL (respeta OneDrive).
            assert lnk.exists(), f"no se creo el acceso directo en {lnk}"

            # 3) El .lnk apunta a la carpeta instalada, no al temp _MEIxxxx.
            data = _leer_shortcut(lnk)
            assert Path(data["target"]) == destino / instalador.WORKER_EXE_NAME, \
                f"TargetPath inesperado: {data['target']}"
            assert "_MEI" not in data["icon"], \
                f"IconLocation apunta al temp de PyInstaller: {data['icon']}"
            assert str(destino) in data["icon"], \
                f"IconLocation no esta en la carpeta instalada: {data['icon']}"
        finally:
            # No dejar basura en el Escritorio real si el test lo creo.
            if creado_por_test and lnk.exists():
                lnk.unlink()

    print("OK: instalacion + acceso directo (Escritorio real, icono persistente)")


if __name__ == "__main__":
    if sys.platform != "win32":
        print("SKIP: el instalador solo aplica en Windows")
    else:
        test_instalar()
