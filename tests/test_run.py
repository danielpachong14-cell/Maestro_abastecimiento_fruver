"""run.py: si el Excel de salida está abierto, mensaje claro en vez de traceback."""
import logging
from pathlib import Path

import pytest

import run


def test_guardar_con_archivo_abierto_muestra_mensaje_y_termina(tmp_path, monkeypatch, caplog):
    def _bloqueado(self, data):
        raise PermissionError(13, 'Permission denied')

    monkeypatch.setattr(Path, 'write_bytes', _bloqueado)
    with caplog.at_level(logging.ERROR, logger='run'), pytest.raises(SystemExit) as e:
        run._guardar(tmp_path / 'distribucion_fruver_x.xlsx', b'xx')
    assert e.value.code == 1
    assert 'está abierto' in caplog.text and 'distribucion_fruver_x.xlsx' in caplog.text


def test_guardar_escribe_normal(tmp_path):
    destino = tmp_path / 'ok.xlsx'
    run._guardar(destino, b'datos')
    assert destino.read_bytes() == b'datos'
