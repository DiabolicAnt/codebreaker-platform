# CodeBreaker API

1era parte del Backend de CodeBreaker. Analiza métricas del código y
compara su implementación con la explicación del estudiante mediante un juez
pedagógico con Claude. No ejecuta el código recibido.

## Estado del api

- API FastAPI con documentación OpenAPI y CORS configurable.
- Análisis de Python, Java y C# con Lizard; Halstead y mantenibilidad con Radon para Python.
- Juez asíncrono con puntuación de coherencia de 1 a 5, justificación y brechas de conocimiento.
- Respuesta explícita cuando falta explicación, configuración o disponibilidad del proveedor.
- Flashcards con FSRS, creación manual, generación con Claude y persistencia local de desarrollo.
- Firebase Authentication obligatorio para evaluación y flashcards; consulta de identidad con `GET /auth/me`.
- Repositorios intercambiables SQLite/Firestore, con transacciones y aislamiento por UID verificado.
- Historial inmutable de evaluaciones por usuario, con consulta paginada y reintentos por `session_id`.

La guía de configuración y pruebas autenticadas está en [FIREBASE_SETUP.md](FIREBASE_SETUP.md).
Los contratos y ejemplos del historial están en [EVALUATION_HISTORY.md](EVALUATION_HISTORY.md).
`GET /`, `/docs` y `/openapi.json` son públicos. Los demás endpoints requieren
`Authorization: Bearer <ID_TOKEN_DE_FIREBASE>`, también cuando se usa SQLite.

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
| `FLASHCARDS_DB_PATH` | `data/flashcards.sqlite3` | Base SQLite local; ruta relativa a la carpeta de la API |
| `FSRS_DESIRED_RETENTION` | `0.9` | Retención objetivo del motor FSRS; configurable entre 0.7 y 0.99 |
| `FIREBASE_PROJECT_ID` | Sin proyecto | Proyecto emisor de los ID tokens |
| `GOOGLE_APPLICATION_CREDENTIALS` | Sin archivo | Ruta al JSON local; si falta se usan ADC |
| `FIREBASE_CHECK_REVOKED` | `true` | Rechaza sesiones revocadas y usuarios deshabilitados; requiere consulta a Authentication |
| `FIREBASE_TIMEOUT_SECONDS` | `10` | Timeout HTTP del SDK Admin |
| `FLASHCARDS_BACKEND` | `sqlite` | `sqlite` o `firestore`; no hay cambio automático ante errores |
| `FIRESTORE_DATABASE_ID` | `(default)` | ID de base, independiente de su ubicación |
| `FIRESTORE_TIMEOUT_SECONDS` | `10` | Timeout por lectura de Firestore; no es el tiempo total de una transacción |
| `EVALUATIONS_BACKEND` | `sqlite` | Almacenamiento del historial: `sqlite` o `firestore`, sin fallback automático |
| `EVALUATIONS_DB_PATH` | `data/evaluations.sqlite3` | Archivo SQLite del historial, independiente del de flashcards |

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

- `session_id` y `created_at`: identificador UUID y fecha UTC de la sesión guardada.
  La evaluación se guarda antes de devolver 200. Un fallo del historial devuelve
  503; no se presenta como evaluación guardada. Puede enviarse `session_id` en la
  petición para recuperar el resultado en un reintento con el mismo contenido.

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

## Flashcards y repetición espaciada

El motor usa `fsrs==6.3.2`, ya incluido en las dependencias. SQLite conserva las
tarjetas, el estado completo de FSRS y el historial de revisiones para desarrollo
local. La base y sus archivos auxiliares quedan excluidos de Git. Firestore conserva
las mismas garantías al seleccionar `FLASHCARDS_BACKEND=firestore`.
El repositorio de datos está separado del motor y de las rutas.

| Endpoint | Función |
| --- | --- |
| `POST /flashcards/` | Crear tarjetas con pregunta y respuesta, sin llamadas a Claude |
| `POST /flashcards/generate` | Generar una tarjeta por concepto débil con Claude |
| `GET /flashcards/due` | Consultar tarjetas pendientes del usuario autenticado |
| `GET /flashcards/{card_id}` | Obtener el estado actual de una tarjeta propia |
| `POST /flashcards/review` | Registrar una calificación y calcular la siguiente fecha FSRS |

### Creación manual

```json
{
  "cards": [{
    "concept": "Recursión",
    "question": "¿Para qué sirve el caso base en una función recursiva?",
    "answer": "Detiene la recursión cuando se cumple una condición. Cada llamada debe avanzar hacia ese caso."
  }]
}
```

Se permiten hasta 20 tarjetas por petición y una tarjeta por usuario y concepto.
Cada concepto admite hasta 200 caracteres, la pregunta 1000 y la respuesta 4000.
Repetir un concepto, incluso con diferencias de mayúsculas o espacios, devuelve la
tarjeta existente sin sustituir su contenido ni reiniciar su progreso. La respuesta
incluye `cards`, `created_count` y `reused_count`.

### Generación desde el evaluador

Envía los conceptos de `coherence.knowledge_gaps` a `/flashcards/generate`:

```json
{
  "knowledge_gaps": ["Recursión", "Complejidad temporal"],
  "context": "El estudiante explicó una solución recursiva pero omitió el caso base."
}
```

Se admiten hasta cinco conceptos; `context` es opcional y tiene un límite de 8000
caracteres. Una lista vacía devuelve un lote vacío sin llamar al proveedor. La
generación requiere `ANTHROPIC_API_KEY`. Si falta la clave, falla el proveedor o la
respuesta no corresponde a los conceptos, devuelve 503 y no crea tarjetas. Las
preguntas y respuestas generadas deben revisarse como contenido pedagógico.

### Revisión y siguientes repasos

```json
{
  "card_id": "UUID_DEVUELTO_EN_LA_CREACION",
  "review_id": "UUID_NUEVO_PARA_ESTE_REPASO",
  "expected_version": 0,
  "rating": 3,
  "review_duration_ms": 1500
}
```

`card_id` y `review_id` deben ser UUID válidos. Obtén `expected_version` del campo
`version` de la tarjeta; cada revisión nueva lo incrementa. `review_duration_ms`
es opcional, admite de 0 a 3 600 000 y representa milisegundos.

| `rating` FSRS | Significado |
| --- | --- |
| 1 — Again | No recordé la respuesta |
| 2 — Hard | La recordé con mucha dificultad |
| 3 — Good | La recordé con una vacilación |
| 4 — Easy | La recordé fácilmente |

Esta escala de 1 a 4 es la valoración del repaso, distinta de la puntuación de
coherencia de 1 a 5 del evaluador. FSRS mantiene sus pesos y pasos predeterminados,
con variación aleatoria de intervalos desactivada para obtener fechas reproducibles.
Las fechas se calculan en el servidor en UTC; el cliente no proporciona el estado
ni la fecha de revisión. `next_review` indica cuándo volver a repasar.

Reintentar la misma petición con el mismo `review_id` devuelve exactamente el
resultado guardado y no vuelve a aplicar el repaso. Usar ese ID con otros datos o
enviar una versión desactualizada devuelve 409. En ese caso, consulta la tarjeta
con `GET /flashcards/{card_id}` antes de iniciar una nueva revisión.
Una tarjeta inexistente para el usuario indicado devuelve 404; entradas inválidas
devuelven 422. Un fallo de almacenamiento devuelve 503. La tarjeta y el registro
de revisión se guardan dentro de la misma transacción.

La consulta de pendientes admite `?limit=100`, con valores entre 1 y 200, y devuelve
`user_id`, `as_of` y `cards`. Incluye las tarjetas vencidas y las que vencen exactamente
en `as_of`, ordenadas por fecha; no adelanta las que vencen más tarde ese día.

### Identidad y compatibilidad

La identidad se obtiene del UID de un ID token validado por Firebase Admin. Ya no
es necesario enviar `user_id`. Durante la transición, los cuerpos que lo incluyan
y las rutas antiguas `/due/{user_id}` y `/{user_id}/{card_id}` siguen disponibles,
pero exigen token y devuelven 403 si ese identificador no coincide con el UID.
Las rutas antiguas aparecen como obsoletas en OpenAPI. El acceso a una tarjeta que
no pertenece al usuario devuelve 404. No existe un modo de omitir autenticación por
usar SQLite. Los UID admiten de 1 a 128 caracteres y se codifican con SHA-256 al
construir rutas de Firestore, para admitir también caracteres como `/`.

Los datos previos de SQLite no se migran ni se reasignan automáticamente. Las
tarjetas que se crearon con identificadores de prueba como `caleb` permanecen en
esa base y no se convierten en tarjetas de un UID de Firebase.

## Pruebas

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

Las pruebas usan Lizard/Radon, FSRS y SQLite reales, peticiones locales a la API y
respuestas simuladas de Anthropic. Incluyen recorridos con el SDK real y transporte
simulado para verificar el JSON estructurado. Cubren persistencia, reintentos,
transacciones, control de versiones y concurrencia. No consumen tokens ni usan
credenciales. Sus bases temporales se crean en `.test-data/` y se eliminan al terminar.
Las pruebas criptográficas usan el SDK real y certificados generados sólo para las
pruebas. Las pruebas externas de Firestore de flashcards e historial se omiten
normalmente; su ejecución optativa, con datos temporales y limpieza, se explica en
`FIREBASE_SETUP.md`.

## Próximas integraciones

1. Integración del frontend con inicio de sesión, evaluación e historial - queda pendiente iniciar app web y móvil.
2. Conectar las brechas del historial con la generación de flashcards desde la interfaz.
3. Contrato con el módulo de voz, incluyendo la transcripción.
4. Integración con el sandbox administrado por infraestructura.
5. Dataset etiquetado y validación de la rúbrica y el umbral provisional del juez.


Referencia de la integración del juez:
[Salidas estructuradas de Anthropic](https://platform.claude.com/docs/en/build-with-claude/structured-outputs).

Referencia del programador de repasos:
[Documentación de Py-FSRS](https://github.com/open-spaced-repetition/py-fsrs).
