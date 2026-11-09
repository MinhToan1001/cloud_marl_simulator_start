"""Kubernetes resource.Quantity parsing without the Kubernetes Python client."""
import re
from decimal import Decimal

PATTERN = re.compile(r"^([+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)([KMGTPE]i|[numkKMGTPE])?$")
DECIMAL = {"": 0, "n": -9, "u": -6, "m": -3, "k": 3, "K": 3, "M": 6, "G": 9, "T": 12, "P": 15, "E": 18}


def quantity(value: str) -> Decimal:
    match = PATTERN.fullmatch(str(value))
    if not match:
        raise ValueError(f"Invalid Kubernetes quantity: {value!r}")
    numeric, suffix = match.groups()
    suffix = suffix or ""
    factor = Decimal(1024) ** ("KMGTPE".index(suffix[0]) + 1) if suffix.endswith("i") else Decimal(10) ** DECIMAL[suffix]
    return Decimal(numeric) * factor


def cpu_cores(value: str) -> float:
    if str(value).endswith("i"):
        raise ValueError("CPU requires decimal units")
    return float(quantity(value))


def memory_bytes(value: str) -> int:
    result = quantity(value)
    if result != result.to_integral_value():
        raise ValueError("Memory quantity must resolve to whole bytes")
    return int(result)
