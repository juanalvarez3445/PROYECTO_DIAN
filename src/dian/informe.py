"""Lector del 'Informe de Facturas electronicas adquiridas por ano' de la DIAN.

Este es el Excel que el usuario descarga desde MUISCA. Contiene UNICAMENTE las
facturas RECIBIDAS (adquiridas) en un ano gravable. Es la fuente de datos
principal del proyecto (mas robusta que hacer scraping del portal).

Estructura del archivo:
  - Filas superiores: metadatos (corte, ano gravable, documento del adquiriente,
    nombre/razon social, avisos legales...).
  - Una fila de ENCABEZADOS que incluye 'Identificacion Emisor Factura'.
  - Debajo, una fila por factura recibida.

Columnas tipicas de la tabla:
  Identificacion Emisor Factura | Nombre Emisor Factura | Fecha Emision |
  Valor Facturado | Valor Notas Credito | Valor Notas Debito |
  Valor Factura / Afectada con Notas Debito - Credito | Valor Susceptible
  Beneficio | Medios De Pago | Num_factura_venta | CUFE
"""
from __future__ import annotations

import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from ..models import Factura


def _norm(texto) -> str:
    """minusculas, sin tildes, sin espacios extra."""
    s = str(texto or "")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return " ".join(s.lower().split())


def _a_float(valor) -> float:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return 0.0
    if isinstance(valor, (int, float)):
        return float(valor)
    txt = str(valor).strip().replace("$", "")
    if not txt:
        return 0.0
    if "," in txt and "." in txt:        # 1.234.567,89
        txt = txt.replace(".", "").replace(",", ".")
    elif "," in txt:                      # 1234,89
        txt = txt.replace(",", ".")
    try:
        return float(txt)
    except ValueError:
        return 0.0


def _a_fecha(valor) -> Optional[date]:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    txt = str(valor).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(txt[:10], fmt).date()
        except ValueError:
            continue
    try:
        return pd.to_datetime(txt, dayfirst=True).date()
    except Exception:
        return None


def _buscar_col(columnas: dict[int, str], *claves: str) -> Optional[int]:
    """Devuelve el indice de la primera columna cuyo nombre contiene TODAS las claves."""
    for idx, nombre in columnas.items():
        n = _norm(nombre)
        if all(_norm(c) in n for c in claves):
            return idx
    return None


def _datos_receptor(df: pd.DataFrame, fila_encabezado: int) -> tuple[Optional[str], Optional[str]]:
    """Extrae NIT y nombre del adquiriente (receptor) de las filas de metadatos."""
    nit = nombre = None
    for i in range(fila_encabezado):
        clave = _norm(df.iat[i, 0]) if df.shape[1] > 0 else ""
        valor = df.iat[i, 1] if df.shape[1] > 1 else None
        if not clave:
            continue
        if nit is None and ("doc identificacion adq" in clave or clave == "nit"):
            nit = str(valor).strip() if valor is not None and not pd.isna(valor) else None
        if nombre is None and "nombre o razon social" in clave:
            nombre = str(valor).strip() if valor is not None and not pd.isna(valor) else None
    return nit, nombre


def cargar_informe_adquiridas(ruta: str | Path) -> list[Factura]:
    """Lee el informe de facturas adquiridas y devuelve una lista de `Factura`.

    Lanza ValueError si no se reconoce el formato (no se halla la fila de
    encabezados con 'Identificacion Emisor Factura').
    """
    ruta = Path(ruta)
    df = pd.read_excel(ruta, header=None, dtype=object)

    # 1) localizar la fila de encabezados
    fila_enc = None
    for i in range(min(len(df), 60)):
        fila_norm = [_norm(x) for x in df.iloc[i].tolist()]
        if any("identificacion emisor" in c for c in fila_norm):
            fila_enc = i
            break
    if fila_enc is None:
        raise ValueError(
            "No parece un 'Informe de facturas adquiridas' de la DIAN "
            "(no se encontro la columna 'Identificacion Emisor Factura')."
        )

    columnas = {idx: str(nombre) for idx, nombre in enumerate(df.iloc[fila_enc].tolist())}
    c_nit = _buscar_col(columnas, "identificacion emisor")
    c_nombre = _buscar_col(columnas, "nombre emisor")
    c_fecha = _buscar_col(columnas, "fecha emision")
    # Para conciliar usamos el valor afectado por notas credito/debito si existe
    c_valor = _buscar_col(columnas, "afectada") or _buscar_col(columnas, "valor facturado")
    c_num = _buscar_col(columnas, "num_factura") or _buscar_col(columnas, "num factura")
    c_cufe = _buscar_col(columnas, "cufe")
    c_medio = _buscar_col(columnas, "medios de pago") or _buscar_col(columnas, "medio de pago")

    nit_receptor, nombre_receptor = _datos_receptor(df, fila_enc)

    facturas: list[Factura] = []
    for i in range(fila_enc + 1, len(df)):
        fila = df.iloc[i]
        nit_emisor = fila.iat[c_nit] if c_nit is not None else None
        if nit_emisor is None or pd.isna(nit_emisor) or not str(nit_emisor).strip():
            continue  # fila vacia o pie de pagina
        total = _a_float(fila.iat[c_valor]) if c_valor is not None else 0.0
        factura = Factura(
            cufe=str(fila.iat[c_cufe]).strip() if c_cufe is not None and not pd.isna(fila.iat[c_cufe]) else None,
            numero_factura=str(fila.iat[c_num]).strip() if c_num is not None and not pd.isna(fila.iat[c_num]) else None,
            nit_emisor=str(nit_emisor).strip(),
            nombre_emisor=str(fila.iat[c_nombre]).strip() if c_nombre is not None and not pd.isna(fila.iat[c_nombre]) else None,
            nit_receptor=nit_receptor,
            nombre_receptor=nombre_receptor,
            fecha_emision=_a_fecha(fila.iat[c_fecha]) if c_fecha is not None else None,
            total=total,
            subtotal=total,
            iva=0.0,
            medio_pago=str(fila.iat[c_medio]).strip() if c_medio is not None and not pd.isna(fila.iat[c_medio]) else None,
            archivo_origen=ruta.name,
            fuente="informe-dian",
        )
        facturas.append(factura)

    return facturas
