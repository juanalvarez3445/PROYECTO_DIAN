"""Parseo de facturas electronicas DIAN en formato XML UBL 2.1.

Las facturas electronicas colombianas son XML UBL 2.1. Frecuentemente la DIAN
las entrega envueltas en un `AttachedDocument`, que contiene la factura real
embebida como texto (CDATA) dentro de `cac:Attachment/.../cbc:Description`.
Este modulo detecta ese caso, extrae el `Invoice` interno y luego mapea los
campos al modelo canonico `Factura`.

Es la fuente PRIMARIA de datos: cuando hay XML, no se necesita IA.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Optional

from lxml import etree

from ..models import Factura, ItemFactura

# Espacios de nombres usados en UBL 2.1 / DIAN
NS = {
    "cac": "urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2",
    "cbc": "urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2",
    "sts": "dian:gov:co:facturaelectronica:Structures-2-1",
}


def _texto(nodo, xpath: str) -> Optional[str]:
    """Devuelve el texto del primer match de `xpath`, o None."""
    encontrados = nodo.xpath(xpath, namespaces=NS)
    if not encontrados:
        return None
    el = encontrados[0]
    valor = el if isinstance(el, str) else el.text
    return valor.strip() if isinstance(valor, str) and valor.strip() else None


def _numero(nodo, xpath: str) -> float:
    txt = _texto(nodo, xpath)
    if not txt:
        return 0.0
    try:
        return float(txt)
    except ValueError:
        return 0.0


def _fecha(txt: Optional[str]) -> Optional[date]:
    if not txt:
        return None
    # UBL usa formato ISO 'YYYY-MM-DD'
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%d/%m/%Y"):
        try:
            return datetime.strptime(txt[:19] if "T" in txt else txt, fmt).date()
        except ValueError:
            continue
    return None


def _desenvolver_invoice(root: etree._Element) -> etree._Element:
    """Si el root es un AttachedDocument, extrae el <Invoice> embebido.

    Devuelve el elemento Invoice/CreditNote/DebitNote a parsear.
    """
    tag = etree.QName(root).localname
    if tag in ("Invoice", "CreditNote", "DebitNote"):
        return root

    if tag == "AttachedDocument":
        # La factura va como CDATA dentro de cbc:Description
        descripciones = root.xpath(
            ".//cac:Attachment//cbc:Description/text()", namespaces=NS
        )
        for desc in descripciones:
            contenido = desc.strip()
            if contenido.startswith("<"):
                interno = etree.fromstring(contenido.encode("utf-8"))
                return interno
        raise ValueError("AttachedDocument sin Invoice embebido reconocible")

    # Caso desconocido: devolver root y dejar que el mapeo extraiga lo que pueda
    return root


def _parsear_items(invoice: etree._Element) -> list[ItemFactura]:
    items: list[ItemFactura] = []
    for linea in invoice.xpath(".//cac:InvoiceLine | .//cac:CreditNoteLine", namespaces=NS):
        items.append(
            ItemFactura(
                descripcion=(
                    _texto(linea, ".//cac:Item/cbc:Description")
                    or _texto(linea, ".//cac:Item/cbc:Name")
                    or ""
                ),
                cantidad=_numero(linea, "./cbc:InvoicedQuantity")
                or _numero(linea, "./cbc:CreditedQuantity"),
                valor_unitario=_numero(linea, ".//cac:Price/cbc:PriceAmount"),
                valor_total=_numero(linea, "./cbc:LineExtensionAmount"),
            )
        )
    return items


def parsear_xml(ruta: str | Path) -> Factura:
    """Parsea un archivo XML de factura DIAN y devuelve una `Factura`."""
    ruta = Path(ruta)
    arbol = etree.parse(str(ruta))
    invoice = _desenvolver_invoice(arbol.getroot())

    # Emisor (AccountingSupplierParty) y receptor (AccountingCustomerParty)
    emisor = "./cac:AccountingSupplierParty/cac:Party"
    receptor = "./cac:AccountingCustomerParty/cac:Party"

    factura = Factura(
        cufe=_texto(invoice, "./cbc:UUID"),
        numero_factura=_texto(invoice, "./cbc:ID"),
        nit_emisor=_texto(
            invoice, f"{emisor}/cac:PartyTaxScheme/cbc:CompanyID"
        )
        or _texto(invoice, f"{emisor}/cac:PartyIdentification/cbc:ID"),
        nombre_emisor=_texto(invoice, f"{emisor}/cac:PartyName/cbc:Name")
        or _texto(invoice, f"{emisor}/cac:PartyTaxScheme/cbc:RegistrationName"),
        nit_receptor=_texto(invoice, f"{receptor}/cac:PartyTaxScheme/cbc:CompanyID")
        or _texto(invoice, f"{receptor}/cac:PartyIdentification/cbc:ID"),
        nombre_receptor=_texto(invoice, f"{receptor}/cac:PartyName/cbc:Name")
        or _texto(invoice, f"{receptor}/cac:PartyTaxScheme/cbc:RegistrationName"),
        fecha_emision=_fecha(_texto(invoice, "./cbc:IssueDate")),
        moneda=_texto(invoice, "./cbc:DocumentCurrencyCode") or "COP",
        subtotal=_numero(invoice, "./cac:LegalMonetaryTotal/cbc:LineExtensionAmount"),
        iva=_numero(invoice, "./cac:TaxTotal/cbc:TaxAmount"),
        total=_numero(invoice, "./cac:LegalMonetaryTotal/cbc:PayableAmount"),
        items=_parsear_items(invoice),
        archivo_origen=ruta.name,
        fuente="xml",
    )
    return factura


def parsear_directorio(directorio: str | Path) -> list[Factura]:
    """Parsea todos los .xml de un directorio. Errores por archivo no detienen el lote."""
    directorio = Path(directorio)
    facturas: list[Factura] = []
    for xml in sorted(directorio.glob("*.xml")):
        try:
            facturas.append(parsear_xml(xml))
        except Exception as exc:  # noqa: BLE001 - se reporta y se continua
            print(f"[parser] No se pudo parsear {xml.name}: {exc}")
    return facturas
