"""Ponto de entrada: python main.py"""
from __future__ import annotations

import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from core.config import load_services, load_settings  # noqa: E402
from services.logging_service import setup_logging  # noqa: E402


def main() -> None:
    settings = load_settings(BASE / "config" / "settings.json", BASE)
    for d in (settings.docs_dir, settings.logs_dir, settings.data_dir):
        d.mkdir(parents=True, exist_ok=True)
    setup_logging(settings.logs_dir)
    services = load_services(BASE / "config" / "certidoes.json")
    from gui.main_window import MainWindow
    MainWindow(settings, services).mainloop()


if __name__ == "__main__":
    main()
