# Historial de evaluaciones

API 0.4.0. Actualizado el 18 de octubre de 2026.

Cada análisis válido guarda una sesión inmutable del usuario autenticado. Incluye
el código original, lenguaje normalizado, explicación, métricas, respuesta del juez,
brechas de conocimiento, modelo/procedencia y fecha UTC de creación en servidor.
No se guarda el token de autenticación. El código recibido sigue sin ejecutarse.

## Configuración

En el `.env` local del proyecto:

```dotenv
EVALUATIONS_BACKEND=firestore
EVALUATIONS_DB_PATH=data/evaluations.sqlite3
```

El archivo SQLite sólo se usa al seleccionar `EVALUATIONS_BACKEND=sqlite`. La
selección del historial es independiente de `FLASHCARDS_BACKEND`. No hay fallback
entre bases cuando falla un servicio. Firestore reutiliza `FIREBASE_PROJECT_ID`,
`GOOGLE_APPLICATION_CREDENTIALS`, `FIRESTORE_DATABASE_ID` y el cliente existentes.
Reiniciar Uvicorn después de cambiar configuración.

## Endpoints

Todos requieren `Authorization: Bearer <FIREBASE_ID_TOKEN>`.

| Método y ruta | Resultado |
| --- | --- |
| `POST /evaluator/analyze` | Analiza y guarda; devuelve el resultado con `session_id` y `created_at` |
| `GET /evaluator/sessions?limit=20` | Lista resúmenes del usuario, más recientes primero |
| `GET /evaluator/sessions?limit=20&cursor=UUID` | Continúa después de esa sesión propia |
| `GET /evaluator/sessions/{session_id}` | Devuelve la sesión completa, incluida `submission` |

La identidad proviene del token. No se acepta `user_id` ni `created_at` en la
petición de análisis. Los campos desconocidos se rechazan con 422.

## Evaluar y guardar

La petición original sigue funcionando. Se recomienda que el cliente genere un
UUID antes de enviarla y lo reutilice sólo al reintentar esa misma evaluación:

```json
{
  "session_id": "e9396404-6197-4ecd-b057-c809b5b0834d",
  "code": "def sumar(a, b):\n    return a + b",
  "language": "python",
  "explanation": "Recibe dos valores y devuelve su suma."
}
```

Sin `session_id`, el servidor genera uno nuevo en cada petición. La respuesta
conserva `complexity`, `bad_explanation_flag`, `feedback` y `coherence`, y añade
`session_id` y `created_at`. Un 200 confirma que el resultado ya quedó guardado.

También se guardan los análisis que tienen métricas válidas pero coherencia
`insufficient_evidence` o `unavailable`: se conserva su puntuación `null`, sin
transformarla en comprensión correcta. Código inválido o una entrada rechazada no
crean una sesión. Esta integración no genera flashcards automáticamente.

## Reintentos y fallos

- Mismo usuario, ID y contenido: devuelve el primer resultado guardado sin llamar
  otra vez al juez. La comparación usa lenguaje normalizado, código y explicación.
- Mismo usuario e ID con contenido diferente: 409, sin sustituir la sesión original.
- Otro usuario puede usar el mismo UUID para una sesión propia independiente.
- Un fallo al consultar o guardar el historial devuelve 503. Si el guardado se
  completó pero se perdió la respuesta, reintentar con el mismo ID permite recuperarla.
- Dos peticiones simultáneas pueden alcanzar al proveedor antes del primer guardado.
  La transacción garantiza una sola sesión y un solo resultado persistido; no
  garantiza una sola llamada a Claude durante esa carrera.
- Si se quiere volver a evaluar una sesión que quedó como `unavailable`, se debe
  iniciar una evaluación nueva con otro ID. El resultado anterior es inmutable.

No se guardan excepciones, credenciales ni trazas del proveedor en el historial.
Las rutas de datos, tokens y detalles de configuración no aparecen en errores HTTP.

## Consulta paginada

La respuesta tiene esta forma:

```json
{
  "sessions": [
    {
      "session_id": "e9396404-6197-4ecd-b057-c809b5b0834d",
      "created_at": "2026-10-18T18:00:00Z",
      "language": "python",
      "coherence_status": "evaluated",
      "score": 4,
      "bad_explanation_flag": false,
      "knowledge_gaps": [],
      "risk_level": "LOW",
      "max_cyclomatic": 1
    }
  ],
  "next_cursor": null
}
```

`limit` admite de 1 a 100, con 20 por defecto. Si quedan más resultados,
`next_cursor` contiene el ID de la última sesión devuelta. Enviarlo como `cursor`
para continuar; si es `null`, terminó el recorrido. La lista no incluye código ni
explicación; Firestore usa una proyección para no descargarlos en cada página.

El orden es fecha descendente y UUID descendente para desempatar. El cursor se
resuelve siempre dentro del usuario autenticado. Si no existe o pertenece a otro
usuario, devuelve 404. Un UUID mal formado devuelve 422. Una nueva sesión creada
entre páginas no desplaza las sesiones más antiguas del recorrido.

La consulta de detalle añade `user_id` y `submission` al resultado original:

```json
{
  "user_id": "UID_VERIFICADO",
  "submission": {
    "code": "def sumar(a, b):\n    return a + b",
    "language": "python",
    "explanation": "Recibe dos valores y devuelve su suma."
  }
}
```

Ese fragmento muestra los campos adicionales; la respuesta real también contiene
el ID, fecha, métricas, coherencia y feedback. Una sesión inexistente para el usuario
autenticado devuelve 404, aunque exista bajo otra cuenta.

## Prueba desde PowerShell

Obtener `$token` con el procedimiento de `FIREBASE_SETUP.md` e iniciar la API.

```powershell
$headers = @{ Authorization = "Bearer $token" }
$request = @{
    session_id = [guid]::NewGuid().ToString()
    code = 'x = 1 + 2'
    language = 'python'
    explanation = 'Sumo uno y dos y guardo el resultado en x.'
} | ConvertTo-Json

$result = Invoke-RestMethod -Method Post -Uri 'http://localhost:8000/evaluator/analyze' `
    -Headers $headers -ContentType 'application/json; charset=utf-8' `
    -Body ([Text.Encoding]::UTF8.GetBytes($request))

Invoke-RestMethod -Uri 'http://localhost:8000/evaluator/sessions?limit=20' -Headers $headers
Invoke-RestMethod -Uri "http://localhost:8000/evaluator/sessions/$($result.session_id)" -Headers $headers
```

Reenviar `$request` sin cambiarlo recupera el mismo resultado. En Swagger, las rutas
aparecen bajo `evaluator` y usan la autorización existente.

## Almacenamiento y alcance

Verificación final: **125 pruebas aprobadas** desde la carpeta real de la API,
incluidas 21 contra Firestore real. La suite habitual ejecuta 104 pruebas locales
y omite las 21 externas. Se comprobó persistencia, aislamiento, paginación con
empates, reintentos tras pérdida de respuesta y escrituras concurrentes.

Firestore: `usuarios/{sha256(uid)}/sesiones/{session_id}`. Cada documento contiene
`created_at`, el registro completo serializado y su resumen. El índice automático
de fecha y nombre de documento permite el orden de consulta. No se modificaron
reglas, permisos IAM ni cuentas de facturación. SQLite usa su propia tabla y archivo.

La escritura comprueba el ID dentro de una transacción; las sesiones existentes no
se sobrescriben. Las pruebas externas usan exclusivamente la colección temporal
`codebreaker_integration_tests`, con UID aleatorios y limpieza de sus sesiones.

Todavía no se implementan edición/borrado del historial, retención automática,
migración de datos previos, estadísticas longitudinales ni vinculación automática
de tarjetas con sesiones. El frontend aún debe integrar estas consultas.

Referencia: [cursores de Firestore](https://firebase.google.com/docs/firestore/query-data/query-cursors).
