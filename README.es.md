# auto-tailored-cv

> Adapta automáticamente tu CV para cada oferta laboral que guardaste en LinkedIn — y entrega tanto `.docx` como `.pdf` — sin que parezca un relleno de keywords obvio.

`Documentación en español | [Read in English](README.md)`

---

## ⚠️ Aviso legal

Esta herramienta automatiza interacciones con LinkedIn usando tu propia sesión de navegador iniciada, vía [Browser MCP](https://github.com/browsermcp/mcp). **Los Términos de Servicio de LinkedIn prohíben el acceso automatizado** a la plataforma. Usar esta herramienta puede derivar en restricciones temporales o permanentes de tu cuenta de LinkedIn. Úsala bajo tu propio riesgo. Los autores de este proyecto no se hacen responsables de ninguna consecuencia, incluida la suspensión de tu cuenta.

El proyecto **no almacena, transmite ni vende** tus credenciales de LinkedIn. Todo el scraping ocurre localmente en tu máquina usando tu propio perfil de Chrome.

---

## Qué hace

Dada una carpeta con tu CV base en formato Word, el sistema:

1. **Extrae** — se conecta a tu Chrome vía Browser MCP, navega a la página de "Empleos guardados" de LinkedIn y obtiene de cada oferta guardada: título, empresa, ubicación, requisitos y descripción completa.
2. **Perfila** — lee tu CV base `.docx` con `python-docx` y lo estructura en secciones (experiencia, skills, educación, etc).
3. **Adapta** — llama a un LLM (vía OpenCode Go API, compatible con OpenAI) para reescribir el CV alineándolo de forma natural con los requisitos de la oferta. El prompt prohibe explícitamente:
   - inventar skills o experiencias que no tienes,
   - copiar frases literales de la oferta,
   - hacer keyword stuffing,
   - modificar fechas o roles.
4. **Evalúa** — un segundo pase del LLM revisa el CV adaptado contra la oferta y tu CV base, marcando alucinaciones, incongruencias, problemas de formato y cualquier alineación "forzada".
5. **Repara** — si el evaluador encontró problemas, un tercer pase corrige solo los issues marcados.
6. **Renderiza** — `python-docx` reconstruye el `.docx` (preservando el diseño de tu CV base). LibreOffice headless convierte `.docx` → `.pdf`.

Para cada oferta guardada se genera una carpeta:

```
output/2026-07-13_senior-data-engineer_acme/
├── cv.docx
├── cv.pdf
├── job_description.txt          # extracción cruda
├── analysis.json                # CV adaptado (estructurado)
└── evaluation.json              # veredicto del evaluador + reparaciones
```

El pipeline es **incremental**: al re-ejecutar solo procesa las ofertas nuevas. Las ya procesadas se saltan a menos que pases `--force`.

---

## Arquitectura

```
extract  →  profile  →  tailor  →  evaluate  →  repair  →  render
(BrowserMCP)        (python-docx)            (OpenCode Go, glm-5.2)        (docx + soffice)
```

Dos pases de LLM — *tailor* (reescribe) y *evaluate* (revisa) — ambos usan **GLM 5.2** vía [OpenCode Go](https://opencode.ai/docs/es/go/), suscripción de bajo costo que incluye este modelo. Ver [`docs/PROMPT_DESIGN.md`](docs/PROMPT_DESIGN.md) para la estrategia anti-sospecha del prompt.

---

## Estructura del proyecto

```
auto-tailored-cv/
├── .github/workflows/ci.yml
├── .gitignore
├── .env.example                # plantilla de secrets (se commitea)
├── .env                        # tus secrets reales (NUNCA se commitea)
├── README.md                   # este archivo (inglés)
├── README.es.md                # versión en español
├── LICENSE                     # MIT
├── pyproject.toml
├── requirements.txt
├── run.py                      # entrypoint CLI
├── input/
│   └── base_cv.docx            # tu CV base (gitignored)
├── jobs/                       # JSONs extraídos (gitignored, caché)
├── output/                     # CVs generados (gitignored)
├── src/
│   ├── config.py
│   ├── extract/linkedin_scraper.py
│   ├── profile/cv_reader.py
│   ├── tailor/
│   │   ├── llm_client.py
│   │   ├── prompts.py
│   │   ├── cv_rewriter.py
│   │   ├── evaluator.py
│   │   └── repair.py
│   ├── render/
│   │   ├── docx_writer.py
│   │   └── pdf_converter.py
│   └── utils/
│       ├── slugify.py
│       └── logging.py
└── tests/
```

---

## Instalación

### Requisitos

- macOS (probado en macOS 14+; Linux debería funcionar con ajustes menores)
- Python 3.9+
- Google Chrome, con tu sesión de LinkedIn iniciada
- Extensión [Browser MCP para Chrome](https://docs.browsermcp.io) instalada
- Una suscripción a OpenCode Go (para el LLM) — obtén tu API key en <https://opencode.ai/auth>
- LibreOffice (para convertir `.docx` → `.pdf`; el script de instalación loDescarga por ti)

### Setup

```bash
git clone https://github.com/<tu-usuario>/auto-tailored-cv.git
cd auto-tailored-cv

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# abre .env y pega tu API key de OpenCode

# coloca tu CV base aquí
cp /ruta/a/tu/cv.docx input/base_cv.docx

# instala LibreOffice (lo saltea si ya está)
./scripts/install_libreoffice.sh
```

Para Browser MCP, asegúrate de que la extensión de Chrome esté instalada y habilitada, y que el servidor MCP local esté registrado en tu config de opencode (ver `.env.example`).

---

## Uso

```bash
# Pipeline completo (extrae todos los empleos guardados, adapta cada uno, renderiza)
python run.py all

# Solo scrapear LinkedIn → jobs/*.json
python run.py extract

# Solo adaptar los empleos ya extraídos
python run.py tailor

# Solo procesar empleos guardados desde la última corrida (incremental)
python run.py all --new

# Re-procesar una URL específica, ignorando caché
python run.py all --job https://www.linkedin.com/jobs/view/<id> --force

# Dry run: muestra qué se procesaría sin llamar al LLM
python run.py all --dry-run
```

Los resultados van a `output/<fecha>_<slug>_<empresa>/`.

---

## Configuración (`.env`)

| Variable | Default | Descripción |
|---|---|---|
| `OPENCODE_API_KEY` | — | Tu API key de OpenCode (obligatoria) |
| `OPENCODE_BASE_URL` | `https://opencode.ai/zen/go/v1` | Endpoint de OpenCode Go |
| `OPENCODE_MODEL_TAILOR` | `glm-5.2` | Modelo para el pase de reescritura |
| `OPENCODE_MODEL_EVALUATOR` | `glm-5.2` | Modelo para el pase de evaluación |
| `LINKEDIN_SAVED_JOBS_URL` | `https://www.linkedin.com/my-items/saved-jobs/` | URL de empleos guardados |
| `BASE_CV_PATH` | `input/base_cv.docx` | Ruta a tu CV base |
| `JOBS_DIR` | `jobs` | Directorio de caché de empleos |
| `OUTPUT_DIR` | `output` | Dónde se escriben los CVs adaptados |
| `SOFFICE_PATH` | `soffice` | Binario de LibreOffice |
| `LOG_LEVEL` | `INFO` | `DEBUG` / `INFO` / `WARNING` / `ERROR` |

Copia `.env.example` a `.env` y completa tus valores.

---

## Seguridad / privacidad

- Tu `.env` y `input/*.docx` están gitignored — tu CV y tus keys nunca salen de tu máquina vía git.
- El scraping de LinkedIn usa tu sesión real de Chrome vía Browser MCP — ninguna contraseña se almacena en este proyecto.
- Todos los prompts al LLM van por OpenCode Go con política de cero retención de datos.

---

## Contribuciones

Es un proyecto personal pero las PRs son bienvenidas — abre primero un issue para discutir qué quieres cambiar.

---

## Licencia

MIT — ver [`LICENSE`](LICENSE).