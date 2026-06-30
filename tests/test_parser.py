"""Pruebas del parser de XML UBL 2.1."""
from pathlib import Path

from src.dian.parser import parsear_xml

FIXTURE = Path(__file__).parent / "fixtures" / "factura_ejemplo.xml"


def test_parsea_campos_principales():
    f = parsear_xml(FIXTURE)
    assert f.numero_factura == "FE-9001"
    assert f.cufe and len(f.cufe) == 64
    assert f.nit_emisor == "901555444"
    assert f.nombre_emisor == "Proveedor Ejemplo S.A.S."
    assert f.nit_receptor == "900123456"
    assert str(f.fecha_emision) == "2026-01-15"
    assert f.subtotal == 1000000.00
    assert f.iva == 190000.00
    assert f.total == 1190000.00
    assert f.moneda == "COP"
    assert f.fuente == "xml"


def test_parsea_items():
    f = parsear_xml(FIXTURE)
    assert len(f.items) == 1
    item = f.items[0]
    assert item.descripcion == "Servicio de consultoria"
    assert item.cantidad == 2
    assert item.valor_unitario == 500000.00
    assert item.valor_total == 1000000.00


def test_clave_usa_cufe():
    f = parsear_xml(FIXTURE)
    assert f.clave() == f.cufe
