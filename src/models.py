"""Modelos de datos compartidos del proyecto.

Definen el esquema canonico de una factura recibida y sus items. Tanto el
parser de XML (fuente primaria) como el extractor de OpenAI (para PDFs)
producen objetos `Factura`, de modo que el resto del pipeline trabaja con un
unico formato.
"""
from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field


class ItemFactura(BaseModel):
    """Linea de detalle de una factura."""

    descripcion: str = ""
    cantidad: float = 0.0
    valor_unitario: float = 0.0
    valor_total: float = 0.0


class Factura(BaseModel):
    """Factura electronica recibida (formato canonico del proyecto)."""

    # Identificacion
    cufe: Optional[str] = Field(default=None, description="Codigo Unico de Factura Electronica")
    numero_factura: Optional[str] = None

    # Emisor (proveedor)
    nit_emisor: Optional[str] = None
    nombre_emisor: Optional[str] = None

    # Receptor (la empresa)
    nit_receptor: Optional[str] = None
    nombre_receptor: Optional[str] = None

    # Fechas y montos
    fecha_emision: Optional[date] = None
    moneda: str = "COP"
    subtotal: float = 0.0
    iva: float = 0.0
    total: float = 0.0

    items: list[ItemFactura] = Field(default_factory=list)

    # Medio de pago reportado por la DIAN (Electronicos, Efectivo, etc.)
    medio_pago: Optional[str] = None

    # Trazabilidad
    archivo_origen: Optional[str] = None
    fuente: str = "xml"  # "xml" | "pdf-openai" | "informe-dian"

    def clave(self) -> str:
        """Clave de deduplicacion: CUFE si existe, si no nit+numero."""
        if self.cufe:
            return self.cufe
        return f"{self.nit_emisor or ''}-{self.numero_factura or ''}"
