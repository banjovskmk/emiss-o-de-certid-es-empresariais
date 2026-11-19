"""Validação e formatação de CNPJ (numérico e alfanumérico)."""
from __future__ import annotations

import re
from typing import Optional

_NON_ALNUM = re.compile(r"[^0-9A-Za-z]")
_PATTERN = re.compile(r"[0-9A-Z]{12}[0-9]{2}")
_W1 = [5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]
_W2 = [6, 5, 4, 3, 2, 9, 8, 7, 6, 5, 4, 3, 2]


def clean_cnpj(value: str) -> str:
    return _NON_ALNUM.sub("", value or "").upper()


def _check_digit(chars: str, weights: list) -> int:
    total = sum((ord(c) - 48) * w for c, w in zip(chars, weights))
    rest = total % 11
    return 0 if rest < 2 else 11 - rest


def is_valid_cnpj(value: str) -> bool:
    c = clean_cnpj(value)
    if len(c) != 14 or not _PATTERN.fullmatch(c) or len(set(c)) == 1:
        return False
    d1 = _check_digit(c[:12], _W1)
    d2 = _check_digit(c[:12] + str(d1), _W2)
    return c[12:] == f"{d1}{d2}"


def format_cnpj(value: str) -> str:
    c = clean_cnpj(value)[:14]
    out = c[:2]
    if len(c) > 2:
        out += "." + c[2:5]
    if len(c) > 5:
        out += "." + c[5:8]
    if len(c) > 8:
        out += "/" + c[8:12]
    if len(c) > 12:
        out += "-" + c[12:14]
    return out


def cnpj_error(value: str) -> Optional[str]:
    c = clean_cnpj(value)
    if not c:
        return "Informe o CNPJ da empresa."
    if len(c) != 14:
        return f"O CNPJ deve ter 14 caracteres (informados: {len(c)})."
    if not _PATTERN.fullmatch(c):
        return "CNPJ inválido: os dois últimos caracteres devem ser números."
    if not is_valid_cnpj(c):
        return "CNPJ inválido: dígitos verificadores incorretos."
    return None
