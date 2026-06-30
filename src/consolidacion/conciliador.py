"""Conciliacion (consolidacion) bancaria: cruce factura <-> movimiento.

Empareja cada factura recibida con un movimiento del extracto bancario usando
criterios deterministicos:

  1. Monto: |valor_factura - valor_movimiento| <= tolerancia.
  2. Fecha: el movimiento cae dentro de +/- `ventana_dias` de la fecha de la
     factura (las facturas se pagan dias despues de emitidas).
  3. Identidad (opcional, suma puntaje): el NIT o el nombre del proveedor
     aparece en la descripcion del movimiento.

Cada factura recibe un estado:
  - 'conciliado'  : se encontro un movimiento que cumple monto + fecha.
  - 'sin_match'   : no se encontro movimiento compatible.
Y cada movimiento queda marcado como usado para no reutilizarlo.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from ..models import Factura


@dataclass
class ResultadoConciliacion:
    factura: Factura
    estado: str  # 'conciliado' | 'sin_match'
    indice_movimiento: Optional[int] = None
    fecha_movimiento: Optional[date] = None
    descripcion_movimiento: str = ""
    valor_movimiento: float = 0.0
    diferencia: float = 0.0
    puntaje: int = 0  # mayor = mejor coincidencia


@dataclass
class Conciliacion:
    resultados: list[ResultadoConciliacion] = field(default_factory=list)
    movimientos: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def conciliadas(self) -> int:
        return sum(1 for r in self.resultados if r.estado == "conciliado")

    @property
    def sin_match(self) -> int:
        return sum(1 for r in self.resultados if r.estado == "sin_match")


def _solo_digitos(s: Optional[str]) -> str:
    return re.sub(r"\D", "", s or "")


def _identidad_coincide(factura: Factura, descripcion: str) -> bool:
    desc = descripcion.lower()
    nit = _solo_digitos(factura.nit_emisor)
    if nit and len(nit) >= 5 and nit in _solo_digitos(descripcion):
        return True
    nombre = (factura.nombre_emisor or "").lower().strip()
    if nombre:
        # Coincide si alguna palabra significativa del nombre esta en la descripcion
        palabras = [p for p in re.split(r"\s+", nombre) if len(p) >= 4]
        if any(p in desc for p in palabras):
            return True
    return False


def conciliar(
    facturas: list[Factura],
    movimientos: pd.DataFrame,
    tolerancia_monto: float = 100.0,
    ventana_dias: int = 5,
    solo_debitos: bool = True,
) -> Conciliacion:
    """Concilia facturas contra movimientos bancarios.

    `solo_debitos`: las facturas recibidas son compras (salidas de dinero), por
    lo que normalmente se cruzan contra debitos. Si el extracto no distingue
    bien el tipo, pon False para considerar todos los movimientos.
    """
    df = movimientos.copy().reset_index(drop=True)
    if solo_debitos and "tipo" in df.columns:
        candidatos_mask = df["tipo"] == "debito"
    else:
        candidatos_mask = pd.Series([True] * len(df))

    usados: set[int] = set()
    resultados: list[ResultadoConciliacion] = []

    for factura in facturas:
        mejor: Optional[ResultadoConciliacion] = None

        for idx, mov in df.iterrows():
            if idx in usados or not candidatos_mask.iloc[idx]:
                continue

            diferencia = abs(factura.total - float(mov["valor"]))
            if diferencia > tolerancia_monto:
                continue

            # Filtro de fecha (si ambas fechas existen)
            f_fac = factura.fecha_emision
            f_mov = mov["fecha"] if isinstance(mov["fecha"], date) else None
            if f_fac and f_mov:
                if not (f_fac - timedelta(days=ventana_dias) <= f_mov <= f_fac + timedelta(days=ventana_dias)):
                    continue

            # Puntaje: monto exacto + identidad + cercania de fecha
            puntaje = 0
            if diferencia <= max(1.0, tolerancia_monto * 0.01):
                puntaje += 3
            else:
                puntaje += 1
            if _identidad_coincide(factura, str(mov["descripcion"])):
                puntaje += 3
            if f_fac and f_mov:
                puntaje += max(0, ventana_dias - abs((f_mov - f_fac).days))

            candidato = ResultadoConciliacion(
                factura=factura,
                estado="conciliado",
                indice_movimiento=int(idx),
                fecha_movimiento=f_mov,
                descripcion_movimiento=str(mov["descripcion"]),
                valor_movimiento=float(mov["valor"]),
                diferencia=diferencia,
                puntaje=puntaje,
            )
            if mejor is None or candidato.puntaje > mejor.puntaje:
                mejor = candidato

        if mejor is not None:
            usados.add(mejor.indice_movimiento)  # type: ignore[arg-type]
            resultados.append(mejor)
        else:
            resultados.append(ResultadoConciliacion(factura=factura, estado="sin_match"))

    return Conciliacion(resultados=resultados, movimientos=df)
