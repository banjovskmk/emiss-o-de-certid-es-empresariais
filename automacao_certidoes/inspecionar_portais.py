"""Ponto de entrada na raiz para inspeção dos portais oficiais.

Executa: python inspecionar_portais.py [opções]
"""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from scripts.inspecionar_portais import main

if __name__ == "__main__":
    sys.exit(main())
