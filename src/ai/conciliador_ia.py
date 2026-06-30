"""Conciliacion bancaria asistida por OpenAI.

La IA es el "cerebro" del cruce factura <-> movimiento bancario. Los extractos
traen descripciones desordenadas (p. ej. "COMPRA RAPPI SAS BOGOTA 0612 PSE") que
una regla rigida no siempre casa; OpenAI entiende que eso corresponde al
proveedor "RAPPI S.A.S".

Estrategia (precisa y economica):
  1. Para cada factura se calculan MOVIMIENTOS CANDIDATOS de forma deterministica
     (mismo tipo debito/credito, monto dentro de tolerancia y, si hay fechas,
     dentro de la ventana de dias). Esto reduce drasticamente el trabajo de la IA.
  2. Si una factura no tiene candidatos -> 'sin_match' (sin gastar una llamada).
  3. Si los tiene, OpenAI elige el mejor candidato (o ninguno) devolviendo
     indice, confianza y motivo, mediante 'structured outputs' (Pydantic).
  4. Un movimiento ya usado no se reutiliza para otra factura.

Reutiliza `ResultadoConciliacion`/`Conciliacion` para que el reporte Excel
funcione igual que con el motor de reglas.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional

import pandas as pd
from pydantic import BaseModel, Field

from config import Settings
from ..consolidacion.conciliador import Conciliacion, ResultadoConciliacion
from ..models import Factura

SYSTEM_PROMPT = (
    "Eres un contador experto en conciliacion bancaria en Colombia. Recibes UNA "
    "factura recibida y una lista de movimientos bancarios CANDIDATOS (ya "
    "prefiltrados por monto y fecha). Debes decidir cual movimiento corresponde "
    "al PAGO de esa factura.\n"
    "Criterios:\n"
    "- El nombre del proveedor (o su NIT) suele aparecer dentro de la descripcion "
    "del movimiento, a veces abreviado o con ruido (PSE, COMPRA, ciudad, codigos).\n"
    "- El monto debe coincidir o ser muy cercano.\n"
    "- La fecha del pago suele ser igual o posterior a la de la factura.\n"
    "- Si NINGUN candidato corresponde con seguridad razonable, devuelve "
    "indice_movimiento = null.\n"
    "Devuelve SIEMPRE un indice que este en la lista de candidatos, o null."
)


class DecisionMatch(BaseModel):
    """Decision de la IA para una factura."""

    indice_movimiento: Optional[int] = Field(
        default=None, description="Indice del movimiento candidato elegido, o null si ninguno"
    )
    confianza: float = Field(default=0.0, description="Confianza de 0 a 1")
    motivo: str = Field(default="", description="Breve justificacion")


def _candidatos(
    factura: Factura,
    df: pd.DataFrame,
    usados: set[int],
    tolerancia_monto: float,
    ventana_dias: int,
    solo_debitos: bool,
) -> list[int]:
    indices = []
    for idx, mov in df.iterrows():
        if idx in usados:
            continue
        if solo_debitos and "tipo" in df.columns and mov["tipo"] != "debito":
            continue
        if abs(factura.total - float(mov["valor"])) > tolerancia_monto:
            continue
        f_fac, f_mov = factura.fecha_emision, mov.get("fecha")
        if f_fac and isinstance(f_mov, date):
            if not (f_fac - timedelta(days=ventana_dias) <= f_mov <= f_fac + timedelta(days=ventana_dias)):
                continue
        indices.append(int(idx))
    return indices


def _prompt_usuario(factura: Factura, df: pd.DataFrame, candidatos: list[int]) -> str:
    lineas = [
        "FACTURA:",
        f"  Proveedor: {factura.nombre_emisor}",
        f"  NIT emisor: {factura.nit_emisor}",
        f"  Fecha: {factura.fecha_emision}",
        f"  Valor: {factura.total:,.2f}",
        "",
        "MOVIMIENTOS CANDIDATOS (usa el campo 'indice'):",
    ]
    for idx in candidatos:
        mov = df.loc[idx]
        lineas.append(
            f"  indice={idx} | fecha={mov.get('fecha')} | valor={float(mov['valor']):,.2f} "
            f"| desc={mov.get('descripcion')}"
        )
    lineas.append("\n¿Cual indice corresponde al pago de la factura? (o null)")
    return "\n".join(lineas)


def conciliar_con_ia(
    facturas: list[Factura],
    movimientos: pd.DataFrame,
    settings: Settings,
    tolerancia_monto: float = 100.0,
    ventana_dias: int = 5,
    solo_debitos: bool = True,
    umbral_confianza: float = 0.5,
    cliente=None,
) -> Conciliacion:
    """Concilia usando OpenAI para decidir cada match. Devuelve `Conciliacion`."""
    faltantes = settings.validar_openai()
    if faltantes:
        raise RuntimeError(f"Falta {', '.join(faltantes)} en .env para usar el motor IA.")

    if cliente is None:
        from openai import OpenAI

        cliente = OpenAI(api_key=settings.openai_api_key)

    df = movimientos.copy().reset_index(drop=True)
    usados: set[int] = set()
    resultados: list[ResultadoConciliacion] = []

    for factura in facturas:
        candidatos = _candidatos(factura, df, usados, tolerancia_monto, ventana_dias, solo_debitos)
        if not candidatos:
            resultados.append(ResultadoConciliacion(factura=factura, estado="sin_match"))
            continue

        decision = _decidir(cliente, settings, factura, df, candidatos)
        idx = decision.indice_movimiento
        if idx is not None and idx in candidatos and idx not in usados and decision.confianza >= umbral_confianza:
            mov = df.loc[idx]
            usados.add(idx)
            resultados.append(
                ResultadoConciliacion(
                    factura=factura,
                    estado="conciliado",
                    indice_movimiento=idx,
                    fecha_movimiento=mov.get("fecha") if isinstance(mov.get("fecha"), date) else None,
                    descripcion_movimiento=str(mov.get("descripcion", "")),
                    valor_movimiento=float(mov["valor"]),
                    diferencia=abs(factura.total - float(mov["valor"])),
                    puntaje=int(round(decision.confianza * 100)),
                )
            )
        else:
            resultados.append(ResultadoConciliacion(factura=factura, estado="sin_match"))

    return Conciliacion(resultados=resultados, movimientos=df)


def _decidir(cliente, settings: Settings, factura: Factura, df: pd.DataFrame, candidatos: list[int]) -> DecisionMatch:
    try:
        completion = cliente.beta.chat.completions.parse(
            model=settings.openai_model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": _prompt_usuario(factura, df, candidatos)},
            ],
            response_format=DecisionMatch,
            temperature=0,
        )
        return completion.choices[0].message.parsed or DecisionMatch()
    except Exception as exc:  # noqa: BLE001
        print(f"[ia] Error consultando OpenAI para factura {factura.numero_factura}: {exc}")
        return DecisionMatch()
