"""Ponto de entrada raiz da Automação de Certidões."""
from __future__ import annotations

from pathlib import Path
import runpy
import sys

BASE_DIR = Path(__file__).resolve().parent / "automacao_certidoes"
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

if __name__ == "__main__":
    main_file = BASE_DIR / "main.py"
    runpy.run_path(str(main_file), run_name="__main__")

