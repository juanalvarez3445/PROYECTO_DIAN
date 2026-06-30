"""Carga y normalizacion de extractos bancarios.

Soporta CSV, Excel (.xlsx/.xls) y PDF. Cada banco usa nombres de columna
distintos, asi que se aplica un mapeo flexible por palabras clave para llevar
todo a un formato comun:

    fecha | descripcion | valor | tipo | referencia

`valor` es siempre positivo; `tipo` es 'debito' (salida de dinero) o 'credito'
(entrada). Para conciliar facturas de compra interesan normalmente los debitos.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

# Palabras clave para detectar cada columna (en minusculas, sin tildes)
ALIAS = {
    "fecha": ["fecha", "date", "f. transaccion", "fecha movimiento", "fecha operacion"],
    "descripcion": ["descripcion", "detalle", "concepto", "referencia 2", "description", "transaccion"],
    "valor": ["valor", "monto", "importe", "amount", "valor transaccion"],
    "debito": ["debito", "debe", "cargo", "salida", "retiro"],
    "credito": ["credito", "haber", "abono", "entrada", "consignacion", "deposito"],
    "referencia": ["referencia", "documento", "comprobante", "num. documento", "reference"],
    "tipo": ["tipo", "naturaleza", "transaccion debito/credito", "d/c", "debito/credito"],
}


def _normaliza_texto(s: str) -> str:
    s = s.strip().lower()
    reemplazos = (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"), ("ñ", "n"))
    for a, b in reemplazos:
        s = s.replace(a, b)
    return s


def _buscar_columna(columnas: list[str], claves: list[str]) -> Optional[str]:
    norm = {c: _normaliza_texto(str(c)) for c in columnas}
    for clave in claves:
        for original, n in norm.items():
            if clave in n:
                return original
    return None


def _a_numero(valor) -> float:
    """Convierte texto monetario colombiano a float. '$ 1.234.567,89' -> 1234567.89"""
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return 0.0
    if isinstance(valor, (int, float)):
        return float(valor)
    txt = str(valor).strip()
    if not txt:
        return 0.0
    negativo = txt.startswith("-") or (txt.startswith("(") and txt.endswith(")"))
    txt = re.sub(r"[^\d,.\-]", "", txt)
    # Formato colombiano: '.' miles, ',' decimal
    if "," in txt and "." in txt:
        txt = txt.replace(".", "").replace(",", ".")
    elif "," in txt:
        txt = txt.replace(",", ".")
    try:
        num = abs(float(txt))
    except ValueError:
        return 0.0
    return -num if negativo else num


def _interpretar_tipo(valor_tipo, bruto: float) -> str:
    """Interpreta una columna de tipo/naturaleza -> 'debito' | 'credito'."""
    t = _normaliza_texto(str(valor_tipo or ""))
    if t:
        if t.startswith("d") or "debit" in t or "debe" in t or "cargo" in t or "salida" in t or "retiro" in t:
            return "debito"
        if t.startswith("c") or "credit" in t or "abono" in t or "haber" in t or "consign" in t or "deposito" in t:
            return "credito"
    return "debito" if bruto < 0 else "credito"


def _a_fecha(valor) -> Optional[date]:
    if valor is None or (isinstance(valor, float) and pd.isna(valor)):
        return None
    if isinstance(valor, datetime):
        return valor.date()
    if isinstance(valor, date):
        return valor
    txt = str(valor).strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y", "%Y/%m/%d", "%d/%m/%y"):
        try:
            return datetime.strptime(txt[:10], fmt).date()
        except ValueError:
            continue
    # Ultimo intento con pandas
    try:
        return pd.to_datetime(txt, dayfirst=True).date()
    except Exception:
        return None


def _leer_crudo(ruta: Path) -> pd.DataFrame:
    suf = ruta.suffix.lower()
    if suf == ".csv":
        # Intenta autodetectar separador
        return pd.read_csv(ruta, sep=None, engine="python", dtype=str)
    if suf in (".xlsx", ".xls"):
        return pd.read_excel(ruta, dtype=str)
    if suf == ".pdf":
        return _leer_pdf_tabla(ruta)
    raise ValueError(f"Formato de extracto no soportado: {suf}")


def _leer_pdf_tabla(ruta: Path) -> pd.DataFrame:
    import pdfplumber

    filas: list[list[str]] = []
    encabezado: Optional[list[str]] = None
    with pdfplumber.open(str(ruta)) as pdf:
        for pagina in pdf.pages:
            for tabla in pagina.extract_tables() or []:
                if not tabla:
                    continue
                if encabezado is None:
                    encabezado = [str(c or "") for c in tabla[0]]
                    filas.extend(tabla[1:])
                else:
                    filas.extend(tabla)
    if not encabezado:
        raise ValueError("No se detectaron tablas en el PDF del extracto.")
    ancho = len(encabezado)
    filas = [f for f in filas if any(c for c in f)]
    filas = [(f + [None] * ancho)[:ancho] for f in filas]
    return pd.DataFrame(filas, columns=encabezado)


def cargar_extracto(ruta: str | Path) -> pd.DataFrame:
    """Carga un extracto y lo normaliza a: fecha, descripcion, valor, tipo, referencia."""
    ruta = Path(ruta)
    crudo = _leer_crudo(ruta)
    crudo = crudo.dropna(how="all")
    columnas = list(crudo.columns)

    col_fecha = _buscar_columna(columnas, ALIAS["fecha"])
    col_desc = _buscar_columna(columnas, ALIAS["descripcion"])
    col_ref = _buscar_columna(columnas, ALIAS["referencia"])
    col_valor = _buscar_columna(columnas, ALIAS["valor"])
    col_debito = _buscar_columna(columnas, ALIAS["debito"])
    col_credito = _buscar_columna(columnas, ALIAS["credito"])
    col_tipo = _buscar_columna(columnas, ALIAS["tipo"])

    registros = []
    for _, fila in crudo.iterrows():
        fecha = _a_fecha(fila.get(col_fecha)) if col_fecha else None
        descripcion = str(fila.get(col_desc, "") or "").strip() if col_desc else ""
        referencia = str(fila.get(col_ref, "") or "").strip() if col_ref else ""

        # Determinar valor y tipo
        if col_debito or col_credito:
            # Dos columnas separadas de debito y credito
            deb = _a_numero(fila.get(col_debito)) if col_debito else 0.0
            cre = _a_numero(fila.get(col_credito)) if col_credito else 0.0
            if deb:
                valor, tipo = abs(deb), "debito"
            else:
                valor, tipo = abs(cre), "credito"
        elif col_tipo:
            # Una columna de valor + una columna explicita de tipo/naturaleza
            bruto = _a_numero(fila.get(col_valor)) if col_valor else 0.0
            valor = abs(bruto)
            tipo = _interpretar_tipo(fila.get(col_tipo), bruto)
        else:
            # Solo valor: el signo indica el tipo (negativo = salida = debito)
            bruto = _a_numero(fila.get(col_valor)) if col_valor else 0.0
            valor = abs(bruto)
            tipo = "debito" if bruto < 0 else "credito"

        if valor == 0.0 and not descripcion:
            continue

        registros.append(
            {
                "fecha": fecha,
                "descripcion": descripcion,
                "valor": valor,
                "tipo": tipo,
                "referencia": referencia,
            }
        )

    df = pd.DataFrame(registros, columns=["fecha", "descripcion", "valor", "tipo", "referencia"])
    return df
