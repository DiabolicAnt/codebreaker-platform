<<<<<<< HEAD
# codebreaker-platform
=======
# CodeBreaker API

1era parte del Backend de CodeBreaker. Analiza métricas del código y
compara su implementación con la explicación del estudiante mediante un juez
pedagógico con Claude. No ejecuta el código recibido.

## Estado del api

- API FastAPI con documentación OpenAPI y CORS configurable.
- Análisis de Python, Java y C# con Lizard; Halstead y mantenibilidad con Radon para Python.
- Juez asíncrono con puntuación de coherencia de 1 a 5, justificación y brechas de conocimiento.
- Respuesta explícita cuando falta explicación, configuración o disponibilidad del proveedor.
- Router de flashcards reservado: FSRS, endpoints de repaso y persistencia aún pendientes.

## Entorno usado para el desarrollo:

El entorno existente utiliza Python 3.14.3. `requirements.txt` fija las versiones
obtenidas de sus dependencias instaladas. No copiar `venv/` entre los equipos que usemos en nuestro desarrollo.
Se puede recrear en cada equipo con python instaldo.

Desde PowerShell, dentro de `codebreaker-api`:

```powershell
# En una instalación nueva:
python -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt

# Si .env aún no existe:
Copy-Item .env.example .env

# Iniciar el servidor con el entorno del proyecto:
.\venv\Scripts\python.exe -m uvicorn main:app --reload
```

Documentación interactiva: <http://localhost:8000/docs>.
Comprobación de arranque: `GET /`.

## Configuración para dar funcionamiento a la API KEY:

Configura `ANTHROPIC_API_KEY` en `.env` para activar el juez. Sin ella, las métricas
estáticas siguen disponibles y la coherencia indica `unavailable`.
El código y la transcripción se envían a Anthropic únicamente cuando se solicita
evaluación con explicación y existe una clave configurada. No se envían audio ni video.

| Variable | Valor predeterminado | Uso |
| --- | --- | --- |
| `ANTHROPIC_API_KEY` | Sin clave | Credencial del proveedor |
| `ANTHROPIC_MODEL` | `claude-sonnet-4-6` | Modelo con salidas estructuradas |
| `ANTHROPIC_TIMEOUT_SECONDS` | `30` | Timeout del SDK por petición |
| `ANTHROPIC_MAX_RETRIES` | `1` | Reintentos del SDK; máximo configurable 2 |
| `ANTHROPIC_MAX_TOKENS` | `2048` | Límite de salida del juez |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Orígenes separados por comas |

El SDK puede efectuar más de un intento y aplicar backoff; el tiempo total puede
superar el timeout de un intento. Las variables del entorno tienen prioridad sobre
`.env`. No guardes claves ni archivos de cuentas de servicio en Git.

## Evaluación

`POST /evaluator/analyze`

```json
{
  "code": "def sumar(a, b):\n    return a + b",
  "language": "python",
  "explanation": "La función recibe dos valores y devuelve su suma."
}
```

Lenguajes: `python`, `java`, `csharp`. Se admiten los alias `py`, `cs` y `c#`,
ignorando mayúsculas y espacios alrededor del nombre. El código tiene un límite de
40 000 caracteres y la explicación de 20 000. Código vacío o Python con sintaxis
inválida devuelve 400; lenguaje no admitido, tipos o tamaños inválidos devuelven 422.
Lizard obtiene métricas para Java y C#, pero no sustituye un compilador ni valida
completamente su sintaxis.

La respuesta incluye:

- `complexity`: promedio y máximo de complejidad ciclomática por función, métricas
  de Python y funciones detectadas. `risk_level` se calcula con la función de mayor
  complejidad: hasta 5 LOW, hasta 10 MEDIUM, hasta 20 HIGH y por encima VERY_HIGH.
  Halstead y mantenibilidad son `null` para Java y C#.
- `coherence.status`: `evaluated`, `insufficient_evidence` si falta explicación,
  o `unavailable` si falta clave, falla el proveedor o su respuesta no es válida.
- `coherence.score`: entero de 1 a 5 solo cuando la evaluación se completó.
- `coherence.reasoning`: justificación pedagógica o motivo de no evaluación.
- `coherence.knowledge_gaps`: hasta cinco conceptos para alimentar futuras flashcards.
- `coherence.source` y `coherence.model`: procedencia de una evaluación completada.
- `bad_explanation_flag`: indicador provisional de una explicación con brechas
  importantes. `true` para puntuaciones 1 o 2, `false` para 3 a 5 y `null` sin
  evaluación. El mismo valor aparece en `coherence.bad_explanation`.
- `feedback`: métricas principales y retroalimentación del juez.

### Cambios de contrato respecto al prototipo inicial

Los clientes deben aceptar `null` en `bad_explanation_flag` y
`coherence.bad_explanation` cuando la coherencia no pudo evaluarse, y en las
métricas de Radon para lenguajes distintos de Python. Se añaden `coherence` y
`max_cyclomatic`. El indicador externo se expone únicamente como
`bad_explanation_flag`; dentro de `coherence` se llama `bad_explanation`.

La nota mide correspondencia observada entre implementación y explicación. El
indicador es una sugerencia de repaso; no prueba fraude, autoría, nivel profesional ni
idoneidad laboral. Las métricas no certifican corrección ni seguridad del código.
Cuando no hay funciones detectadas se utiliza complejidad 1 como valor base;
no equivale a haber medido el flujo completo del código a nivel de módulo.

## Pruebas

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

Las pruebas usan el análisis real con Lizard/Radon, peticiones locales a la API y
respuestas simuladas de Anthropic. Incluyen un recorrido con el SDK real y transporte
simulado para verificar el JSON estructurado. No consumen tokens ni usan credenciales.

## Próximas integraciones

1. FSRS y contratos de generación, revisión y tarjetas pendientes.
2. Firebase Authentication y persistencia por usuario en Firestore.
3. Contrato con el módulo de voz, incluyendo la transcripción.
4. Integración con el sandbox administrado por infraestructura.
5. Dataset etiquetado y validación de la rúbrica y el umbral provisional del juez.


Referencia de la integración del juez:
[Salidas estructuradas de Anthropic](https://platform.claude.com/docs/en/build-with-claude/structured-outputs).
