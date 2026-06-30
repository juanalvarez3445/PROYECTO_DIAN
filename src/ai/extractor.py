"""Extraccion estructurada de facturas con el SDK de OpenAI.

Se usa cuando una factura recibida solo llega como PDF (sin XML) o cuando hay
que normalizar/validar datos. El XML, cuando existe, es siempre la fuente
primaria (ver `src/dian/parser.py`) y NO pasa por aqui.

Estrategia: se extrae el texto del PDF con pdfplumber y se envia al modelo con
"structured outputs" (response_format con esquema Pydantic), de modo que la
respuesta ya viene validada como un objeto `Factura`.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pdfplumber

from config import Settings
from ..models import Factura

SYSTEM_PROMPT = (
    "Eres un asistente experto en facturacion electronica colombiana (DIAN). "
    "Recibes el texto plano de una factura recibida y debes extraer sus datos "
    "en el esquema indicado. Reglas:\n"
    "- Los montos son numeros sin separador de miles ni simbolo de moneda "
    "(ej. 1234567.89). Usa punto como separador decimal.\n"
    "- 'nit_emisor' es el NIT del proveedor que emite; 'nit_receptor' es quien "
    "recibe la factura.\n"
    "- 'subtotal' es la base gravable antes de impuestos; 'iva' es el total de "
    "impuestos; 'total' es el valor a pagar.\n"
    "- Si un dato no aparece, dejalo nulo o en 0. No inventes valores."
)


def _leer_texto_pdf(ruta: Path) -> str:
    partes: list[str] = []
    with pdfplumber.open(str(ruta)) as pdf:
        for pagina in pdf.pages:
            txt = pagina.extract_text() or ""
            if txt:
                partes.append(txt)
    return "\n".join(partes)


def extraer_factura_de_pdf(
    ruta: str | Path,
    settings: Settings,
    cliente=None,
) -> Optional[Factura]:
    """Extrae una `Factura` desde un PDF usando OpenAI structured outputs.

    Devuelve None si no se puede leer texto del PDF o si falla la llamada.
    """
    ruta = Path(ruta)

    faltantes = settings.validar_openai()
    if faltantes:
        raise RuntimeError(
            f"Falta configurar {', '.join(faltantes)} en .env para usar OpenAI."
        )

    texto = _leer_texto_pdf(ruta)
    if not texto.strip():
        print(f"[openai] El PDF {ruta.name} no contiene texto extraible (¿escaneado?).")
        return None

    # Import diferido para no exigir la dependencia si no se usa IA
    if cliente is None:
        from openai import OpenAI

        cliente = OpenAI(api_key=settings.openai_api_key)

    completion = cliente.beta.chat.completions.parse(
        model=settings.openai_model,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": f"Texto de la factura:\n\n{texto}",
            },
        ],
        response_format=Factura,
        temperature=0,
    )

    factura = completion.choices[0].message.parsed
    if factura is None:
        return None
    factura.archivo_origen = ruta.name
    factura.fuente = "pdf-openai"
    return factura


def extraer_pdfs_directorio(
    directorio: str | Path,
    settings: Settings,
    solo_sin_xml: bool = True,
) -> list[Factura]:
    """Procesa los PDF de un directorio con OpenAI.

    Si `solo_sin_xml` es True, omite los PDF cuyo XML homonimo ya existe
    (para no duplicar lo que ya parseamos del XML).
    """
    directorio = Path(directorio)
    from openai import OpenAI

    cliente = OpenAI(api_key=settings.openai_api_key)

    facturas: list[Factura] = []
    for pdf in sorted(directorio.glob("*.pdf")):
        if solo_sin_xml and pdf.with_suffix(".xml").exists():
            continue
        try:
            f = extraer_factura_de_pdf(pdf, settings, cliente=cliente)
            if f:
                facturas.append(f)
        except Exception as exc:  # noqa: BLE001
            print(f"[openai] No se pudo procesar {pdf.name}: {exc}")
    return facturas
