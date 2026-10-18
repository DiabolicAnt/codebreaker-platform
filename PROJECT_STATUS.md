# Revisión y avance del backend

Actualizado el 18 de octubre de 2026. API 0.4.0.

## Base revisada

Se revisaron `main.py`, ambos routers, los servicios y el entorno instalado.
La separación entre rutas y servicios ofrece una base útil para conectar los
módulos del equipo. La evaluación existente era una heurística basada en longitud
de explicación y complejidad; todavía no evaluaba coherencia técnica.

## Problemas corregidos

| Hallazgo | Efecto anterior | Corrección |
| --- | --- | --- |
| `routers/flashcards.py` vacío | `main.py` fallaba al acceder a `flashcards.router` | Router y endpoints de flashcards implementados |
| `.gitignore` con `*` | Git ignoraba todo el código; el repositorio no tenía commits | Exclusiones específicas de entorno, cachés y secretos |
| `venv/pyvenv.cfg` ausente | El ejecutable del entorno virtual no iniciaba | Restauración desde el archivo existente en la raíz |
| Nombre `temp.python` o `temp.csharp` para Lizard | Selección incorrecta del analizador | Extensiones reales `.py`, `.java` y `.cs` |
| Acceso `h[0].total.effort` a Radon | Error ocultado; esfuerzo y mantenibilidad quedaban con valores ficticios | Uso de `h_visit(...).total.effort` y cálculo de MI |
| Captura general de excepciones en métricas | Sintaxis inválida se presentaba como un resultado normal | Validación de sintaxis Python y error explícito |
| Riesgo definido solo por promedio | Una función compleja podía quedar diluida entre funciones simples | Riesgo por máximo; se conserva el promedio |
| Radon con valores por defecto para Java/C# | Métricas aparentes que no se habían calculado | Valores `null` para métricas no aplicables |
| Explicación breve como criterio de comprensión | Afirmaciones sin evidencia suficiente | Juez estructurado y estado explícito de no evaluación |

## Avance implementado

Se integró el juez con Claude mediante el SDK asíncrono de Anthropic, respuestas
estructuradas validadas, una rúbrica de 1 a 5, justificación breve y conceptos de
repaso. El código y la explicación se tratan como datos no confiables dentro del
prompt. No hay ejecución de código ni herramientas disponibles para el modelo.

Se añadieron configuración por entorno, límites de entrada, CORS por orígenes,
dependencias fijadas desde el entorno existente y documentación del contrato.
Las métricas siguen disponibles si no puede completarse la evaluación de coherencia.

Se añadió el motor FSRS con fechas UTC y serialización completa, además de creación
manual, generación desde brechas con Claude, consulta de pendientes, consulta de una
tarjeta y registro de repasos. SQLite ofrece persistencia local de desarrollo mediante
un repositorio separado. Firestore ya tiene un adaptador con las mismas garantías.

Firebase Authentication protege evaluación y flashcards. La identidad proviene del
UID verificado, con revisión de revocación por defecto; `GET /auth/me` devuelve ese UID.
Se rechazan intentos de suplantar usuarios y los secretos no se exponen en errores.
Las consultas nuevas omiten `user_id`; se mantienen las rutas previas como obsoletas,
con verificación explícita de propietario. `/`, `/docs` y `/openapi.json` son públicos.

Las tarjetas son únicas por usuario y concepto. El contenido y progreso existentes
se conservan al repetir una creación. Las revisiones usan un identificador idempotente,
control de versión y una transacción que guarda tanto el estado FSRS como el historial.
Se resolvieron los marcadores de conflicto presentes en el README, conservando su contenido.

## Verificación

Última ejecución desde la carpeta real, con historial incluido: **125 pruebas
aprobadas**, incluidas 21 contra Firestore real (11 de sesiones y 10 de flashcards).
La ejecución habitual usa 104 pruebas locales y omite las 21 externas. Los datos
temporales fueron eliminados. `pip check` sin incompatibilidades; historial efectivo
configurado con `FirestoreEvaluationRepository`. `.env` continúa excluido de Git.

El bloque anterior de Firebase se verificó desde la carpeta real con 87 pruebas,
incluidas 10 de Firestore externo. `pip check` no encontró dependencias incompatibles.

Las 61 pruebas previas se conservaron y adaptaron al acceso autenticado. Cubren
arranque y OpenAPI, análisis en los tres lenguajes,
métricas de Radon, umbrales de complejidad, validación de entradas, estados sin
explicación o sin clave, respuestas del juez, fallos del proveedor y CORS.
Una prueba ejercita el SDK de Anthropic con transporte simulado.

Las pruebas nuevas usan FSRS y SQLite reales y cubren el ciclo aprendizaje/repaso/
reaprendizaje, persistencia al reabrir la base, fechas de vencimiento, separación por
usuario, reintentos, versiones desactualizadas, rollback de transacciones y peticiones
concurrentes. La generación también se comprueba con el SDK y transporte simulado.

Se agregaron pruebas de autorización, configuración, fallos de Firebase y validación
criptográfica con JWT y certificados de prueba. Se verificó conexión real con
Authentication y Firestore del proyecto `code-breaker-27518`. Diez pruebas optativas
contra Firestore real comprobaron concurrencia, reintentos y atomicidad; sus datos
temporales se limpiaron. No se crearon cuentas de usuarios ni se probó un inicio de
sesión real del frontend; el ayudante de correo/contraseña permite esa prueba manual.

No se realizó una evaluación remota con Claude: la integración está verificada
con respuestas simuladas, no su calidad pedagógica ni el acceso real del proveedor.
La rúbrica y el umbral de puntuación baja deben validarse con el dataset etiquetado
del proyecto antes de interpretarlos como un benchmark.

## Trabajo pendiente

El siguiente bloque es conectar el frontend con inicio de sesión, evaluación e
historial, y conectar sus brechas con el repaso desde la interfaz. Los datos antiguos de SQLite no
se migraron; se requiere un mapeo explícito de usuarios si se decide conservarlos
en Firestore. Cloud Run y la vinculación de facturación se pospusieron por decisión
del usuario. Siguen pendientes voz, sandbox, datos para DKT y validación pedagógica
del contenido generado y del juez con casos etiquetados. Ver `FIREBASE_SETUP.md`.

## Historial de evaluaciones

La API 0.4.0 guarda automáticamente cada análisis válido antes de devolver 200.
Las sesiones son inmutables e incluyen código, lenguaje normalizado, explicación,
métricas, coherencia, brechas, proveedor/modelo y fecha UTC. Se añaden `session_id`
y `created_at` a la respuesta previa. Las entradas inválidas no se guardan; los
estados `unavailable` e `insufficient_evidence` conservan sus valores desconocidos.

El cliente puede enviar un UUID para recuperar el resultado tras un reintento. Si
reutiliza el ID con otros datos se devuelve 409. Se comprueba la sesión existente
antes de llamar al juez y una transacción impide sobrescrituras concurrentes. No
se garantiza una sola llamada al proveedor en peticiones simultáneas todavía sin
resultado guardado. Un fallo del historial devuelve 503, sin afirmar que se guardó.

`GET /evaluator/sessions` ofrece resúmenes paginados, con fecha y UUID descendentes;
`GET /evaluator/sessions/{session_id}` obtiene el detalle propio. Los cursores se
resuelven dentro de cada usuario y las consultas no permiten acceso cruzado.
Firestore usa `usuarios/{sha256(uid)}/sesiones/{session_id}`. SQLite ofrece el mismo
contrato para desarrollo mediante un archivo separado. Ver `EVALUATION_HISTORY.md`.
