"""Verifica que el paquete saas_radar es importable correctamente."""

from __future__ import annotations


def test_package_importable() -> None:
    """El paquete no levanta ModuleNotFoundError al importarse."""
    import saas_radar

    assert saas_radar is not None


def test_package_has_version() -> None:
    """El paquete expone __version__ como string."""
    import saas_radar

    assert isinstance(saas_radar.__version__, str)
    assert len(saas_radar.__version__) > 0
