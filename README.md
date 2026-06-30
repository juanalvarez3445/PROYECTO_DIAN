# Agente de Facturas Recibidas DIAN + Consolidación Bancaria (con OpenAI)

Agente en Python que:

1. **Carga las facturas recibidas** desde el *"Informe de Facturas electrónicas adquiridas por año"* que se descarga de MUISCA (`muisca.dian.gov.co`). Ese Excel contiene únicamente las facturas **recibidas/adquiridas** del año.
2. **Concilia las facturas con el banco usando el SDK de OpenAI**: la IA decide qué movimiento bancario corresponde a cada factura, entendiendo descripciones desordenadas (ej. *"COMPRA RAPPI SAS BOGOTA PSE"* → proveedor *RAPPI S.A.S*).
3. Genera un **reporte en Excel** con facturas, movimientos y el estado de la conciliación.

> ℹ️ **Por qué NO hacemos scraping de la DIAN:** el portal de factura electrónica (catalogo-vpfe) está protegido por **Cloudflare Turnstile + Azure WAF**, que bloquean la automatización. En cambio, descargar el informe de MUISCA manualmente es trivial y confiable. Por eso el flujo es: **tú descargas el informe → el robot hace el resto.** (El scraping con Playwright quedó disponible en `src/dian/scraper.py` como referencia, pero no es el camino recomendado.)

## Requisitos

- Python 3.11 o superior

## Instalación

```bash
python -m venv .venv
# Windows (PowerShell):
.venv\Scripts\Activate.ps1
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
playwright install chromium
```

## Configuración

```bash
# Copia la plantilla y completa tus credenciales reales
copy .env.example .env      # Windows
# cp .env.example .env      # macOS/Linux
```

Edita `.env` con tus credenciales del portal DIAN y tu `OPENAI_API_KEY`.
**Nunca subas `.env` a git** (ya está en `.gitignore`).

## Uso

### 1) Cargar las facturas recibidas (informe de MUISCA)

Descarga de MUISCA el *"Informe de Facturas electrónicas adquiridas por año"*
(.xlsx) y cárgalo:

```bash
python main.py informe --archivo "C:/ruta/al/report.xlsx"
```

- Lee solo las facturas **recibidas**, las muestra y las deja guardadas para conciliar.

### 2) Conciliar contra el extracto bancario (con OpenAI)

```bash
python main.py consolidar --extracto data/extractos/mi_extracto.xlsx
```

- `--motor ia` (por defecto si hay `OPENAI_API_KEY`): la IA decide cada cruce.
- `--motor reglas`: conciliación determinística (sin IA).
- `--motor auto`: IA si hay API key, si no reglas.
- `--solo-debitos` (por defecto) cruza solo contra salidas de dinero; `--todos` contra todos los movimientos.
- Genera el reporte Excel en `data/salida/`.

### (Opcional / referencia) Scraping del portal y PDFs

```bash
python main.py extraer --desde 2026-01-01 --hasta 2026-01-31 --headed   # scraping (no recomendado)
python main.py procesar-pdf data/descargas/factura.pdf                    # estructurar un PDF con OpenAI
```

## Estructura del proyecto

```
config.py                      # configuración y rutas (lee .env)
main.py                        # CLI (comandos: extraer, consolidar, procesar-pdf)
src/
  models.py                    # esquema canónico de Factura
  dian/scraper.py              # Playwright: login + descarga
  dian/parser.py               # parseo de XML UBL 2.1
  ai/extractor.py              # OpenAI: estructuración de PDFs
  banco/loader.py              # carga de extractos bancarios
  consolidacion/conciliador.py # cruce factura ↔ movimiento
  reporte/excel.py             # generación del reporte .xlsx
data/
  descargas/  extractos/  salida/
tests/                         # pruebas del parser y el conciliador
```

## Notas y advertencias

- Los **selectores del portal DIAN pueden cambiar**; están centralizados en `src/dian/scraper.py` para ajustarlos fácilmente.
- `data/` contiene información fiscal y bancaria sensible y está excluido de git.
- La conciliación es **determinística** (monto + fecha + NIT/nombre); ajusta la tolerancia y la ventana de días en `.env`.
