"""
watcher.py
──────────
Monitorea el archivo fuente y re-ejecuta la transformación
automáticamente cada vez que detecta un cambio.

Instalación única:
    pip install watchdog

Uso:
    python watcher.py
    python watcher.py --origen "C:/ruta/archivo.xlsx" --destino "C:/ruta/salida.xlsx"

Deja esta ventana abierta mientras trabajas. Cada vez que guardes
el archivo fuente, el output se regenera solo.
"""

import argparse
import time
import logging
from pathlib import Path

from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

from transformar_portafolio import leer_fuente, transformar, escribir_excel, ORIGEN_DEFAULT, DESTINO_DEFAULT

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)s  %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger(__name__)


class PortafolioHandler(FileSystemEventHandler):
    def __init__(self, origen: Path, destino: Path):
        self.origen  = origen
        self.destino = destino
        self._last   = 0.0          # evita disparos dobles en <2 seg

    def on_modified(self, event):
        if event.is_directory:
            return
        if Path(event.src_path).resolve() != self.origen.resolve():
            return
        now = time.time()
        if now - self._last < 2:    # debounce
            return
        self._last = now
        log.info(f"Cambio detectado en {self.origen.name} — regenerando...")
        try:
            df        = leer_fuente(self.origen)
            resultado = transformar(df)
            escribir_excel(resultado, self.destino)
            log.info("✓ Listo")
        except Exception as e:
            log.error(f"Error durante la transformación: {e}")


def main():
    parser = argparse.ArgumentParser(description="Watcher portafolio Fruver")
    parser.add_argument("--origen",  default=str(ORIGEN_DEFAULT))
    parser.add_argument("--destino", default=str(DESTINO_DEFAULT))
    args = parser.parse_args()

    origen  = Path(args.origen)
    destino = Path(args.destino)

    if not origen.exists():
        log.error(f"Archivo no encontrado: {origen}")
        return

    log.info(f"Monitoreando: {origen}")
    log.info(f"Salida:       {destino}")
    log.info("Presiona Ctrl+C para detener.")

    handler  = PortafolioHandler(origen, destino)
    observer = Observer()
    observer.schedule(handler, path=str(origen.parent), recursive=False)
    observer.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        observer.stop()
        log.info("Watcher detenido.")
    observer.join()


if __name__ == "__main__":
    main()
