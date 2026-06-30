"""Generador de un extracto bancario de EJEMPLO a partir de las facturas.

Sirve para probar/demostrar la conciliacion cuando no se tiene un extracto real.
Toma una muestra de las facturas recibidas y crea movimientos bancarios con
descripciones "desordenadas" (como las de un banco real), mas algunos
movimientos de ruido (nomina, transferencias) que no deben conciliar.

El archivo resultante tiene las columnas estandar que entiende el lector de
extractos: Fecha, Descripcion, Valor, Tipo.
"""
from __future__ import annotations

import random
import re
from datetime import timedelta
from pathlib import Path

import pandas as pd

from ..models import Factura

# Palabras a ignorar al extraer el "nombre corto" del proveedor
_RUIDO_NOMBRE = {"sas", "s.a.s", "s.a.s.", "sa", "s.a", "s.a.", "ltda", "ltda.",
                 "de", "del", "la", "el", "los", "las", "y", "e", "bic", "esp",
                 "nit", "s", "a", "compañia", "compania"}

_PREFIJOS = ["COMPRA", "PAGO PSE", "DEB AUTOMATICO", "PAGO", "ABONO PSE",
             "COMPRA TARJETA", "PSE", "PAGO PSE COMPRA"]
_SUFIJOS = ["BOG", "MED", "CC", "PSE", "0612", "TX", "POS", "", "CALI", "MEDELLIN"]

_RUIDO = [
    ("PAGO NOMINA EMPLEADOS", 2_500_000),
    ("TRANSFERENCIA INTERBANCARIA", 1_200_000),
    ("RETIRO CAJERO AUTOMATICO", 300_000),
    ("CUOTA MANEJO TARJETA", 18_500),
    ("ABONO INTERESES AHORRO", 4_300),
    ("PAGO SERVICIOS PUBLICOS EPM", 210_000),
]


def _nombre_corto(nombre: str) -> str:
    palabras = [p for p in re.split(r"\s+", (nombre or "").strip()) if p]
    sig = [p for p in palabras if p.lower().strip(".") not in _RUIDO_NOMBRE]
    elegidas = (sig or palabras)[:2]
    return " ".join(elegidas).upper() if elegidas else "PROVEEDOR"


def generar_extracto_ejemplo(
    facturas: list[Factura],
    ruta_salida: str | Path,
    n_conciliables: int = 30,
    semilla: int = 42,
) -> Path:
    """Crea un extracto de ejemplo y lo guarda en `ruta_salida` (.xlsx)."""
    rng = random.Random(semilla)
    ruta_salida = Path(ruta_salida)

    # Muestra de facturas con fecha y valor validos
    validas = [f for f in facturas if f.total and f.fecha_emision]
    muestra = rng.sample(validas, min(n_conciliables, len(validas)))

    filas = []
    for f in muestra:
        desfase = rng.randint(0, 3)  # el pago suele ser 0-3 dias despues
        fecha = f.fecha_emision + timedelta(days=desfase)
        desc = f"{rng.choice(_PREFIJOS)} {_nombre_corto(f.nombre_emisor)} {rng.choice(_SUFIJOS)}".strip()
        filas.append({"Fecha": fecha, "Descripcion": desc, "Valor": f.total, "Tipo": "debito"})

    # Movimientos de ruido (no deben conciliar)
    base = muestra[0].fecha_emision if muestra else None
    for i, (desc, valor) in enumerate(_RUIDO):
        fecha = (base + timedelta(days=rng.randint(-10, 10))) if base else None
        filas.append({"Fecha": fecha, "Descripcion": desc, "Valor": valor, "Tipo": "debito"})

    rng.shuffle(filas)
    df = pd.DataFrame(filas, columns=["Fecha", "Descripcion", "Valor", "Tipo"])
    ruta_salida.parent.mkdir(parents=True, exist_ok=True)
    df.to_excel(ruta_salida, index=False)
    return ruta_salida
