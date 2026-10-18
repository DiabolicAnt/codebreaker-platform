# Firebase local: Authentication y Firestore

Actualizado el 18 de octubre de 2026. API 0.4.0.

Proyecto confirmado: `code-breaker-27518`. Firestore Standard: `(default)`, ubicación
multirregional `nam5`. Authentication tiene habilitados correo/contraseña y Google.
La facturación y el despliegue en Cloud Run se pospusieron por decisión del equipo.
Se comprobó conexión real con Authentication y Firestore sin vincular facturación.

## Configuración

Dentro de la carpeta de la API, instalar las dependencias con su entorno virtual:

```powershell
.\venv\Scripts\python.exe -m pip install -r requirements.txt
```

Copiar `.env.example` a `.env` sólo si `.env` no existe. Si ya existe, editarlo sin
sobrescribir las claves del juez. Para conectar Firestore:

```dotenv
FIREBASE_PROJECT_ID=code-breaker-27518
GOOGLE_APPLICATION_CREDENTIALS=C:/Users/Caleb/.config/codebreaker/ARCHIVO_ADMINSDK.json
FIREBASE_CHECK_REVOKED=true
FIREBASE_TIMEOUT_SECONDS=10
FIREBASE_USE_EMULATORS=false
FLASHCARDS_BACKEND=firestore
FIRESTORE_DATABASE_ID=(default)
FIRESTORE_TIMEOUT_SECONDS=10
EVALUATIONS_BACKEND=firestore
EVALUATIONS_DB_PATH=data/evaluations.sqlite3
```

Sustituir únicamente el nombre del archivo por la ruta real al JSON. El JSON se
mantiene fuera del repositorio y no se publica ni se entrega al frontend. La
credencial se valida contra el proyecto configurado. En Google Cloud se usarán
Application Default Credentials de la cuenta de servicio asociada al despliegue.

Las variables del proceso tienen prioridad sobre `.env`. Reiniciar Uvicorn después
de cambiar configuración. `FLASHCARDS_BACKEND=sqlite` conserva SQLite para desarrollo,
pero exige la misma autenticación. No hay fallback a SQLite si Firestore falla.
La región `nam5` no se pone como ID de base ni como región de Cloud Run.

## Probar con un usuario real

1. Firebase Console → Authentication → Users → Add user. Crear una cuenta de prueba
   con correo y contraseña. También puede usarse una cuenta que ya exista.
2. Obtener un **Firebase ID token** iniciando sesión. El frontend lo consigue con
   `await user.getIdToken()` después de iniciar sesión con correo o Google.
   Un access token de Google o un custom token de Admin no reemplazan al ID token.
3. Sin frontend, ejecutar el ayudante interactivo:

   ```powershell
   $token = .\venv\Scripts\python.exe .\scripts\firebase_login.py
   ```

   Solicita la Web API key del proyecto, correo y contraseña; la contraseña se
   oculta y no se guarda. Para localizar `apiKey`, abrir Configuración del proyecto
   → General → Tus apps → aplicación web → Configuración del SDK. Si aún no hay
   aplicación web registrada, coordinar su registro con la responsable del frontend.
   Usar la Web API key de ese proyecto, nunca la clave privada del JSON Admin.
   El ayudante usa el proveedor correo/contraseña; Google se prueba con el SDK cliente.
4. Iniciar la API en otra terminal:

   ```powershell
   .\venv\Scripts\python.exe -m uvicorn main:app --reload
   ```

5. Abrir <http://localhost:8000/docs>, pulsar **Authorize** y pegar únicamente el
   ID token. El esquema HTTP Bearer agrega el prefijo `Bearer`. No pegarlo en chats.

También puede probarse con PowerShell, en la terminal donde existe `$token`:

```powershell
if ($LASTEXITCODE -ne 0 -or -not $token) { throw 'El inicio de sesión no produjo un token' }
$headers = @{ Authorization = "Bearer $token" }
Invoke-RestMethod -Uri 'http://localhost:8000/auth/me' -Headers $headers

$body = @{
    cards = @(@{
        concept = 'Bucles'
        question = '¿Para qué sirve un bucle?'
        answer = 'Para repetir una operación según una condición o una secuencia.'
    })
} | ConvertTo-Json -Depth 5

$batch = Invoke-RestMethod -Method Post -Uri 'http://localhost:8000/flashcards/' `
    -Headers $headers -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
$card = $batch.cards[0]

Invoke-RestMethod -Uri 'http://localhost:8000/flashcards/due' -Headers $headers

$review = @{
    card_id = $card.card_id
    review_id = [guid]::NewGuid().ToString()
    expected_version = $card.version
    rating = 3
    review_duration_ms = 1500
} | ConvertTo-Json

Invoke-RestMethod -Method Post -Uri 'http://localhost:8000/flashcards/review' `
    -Headers $headers -ContentType 'application/json' -Body $review
```

Reenviar `$review` sin cambiar nada devuelve el resultado anterior. Para otro repaso,
obtener la versión actual y crear un `review_id` nuevo. FSRS acepta sólo 1, 2, 3 o 4.
Cuando el ID token expire, volver a iniciar sesión u obtener uno actualizado.

## Contratos y errores

Rutas protegidas: `/auth/me`, todas las de `/evaluator` y todas las de `/flashcards`.
`GET /`, `/docs` y `/openapi.json` continúan públicos.

Ya no es necesario enviar `user_id` en creación, generación o repaso. Se inyecta el
UID verificado. Por compatibilidad se acepta si coincide con ese UID. Las consultas
nuevas son `GET /flashcards/due` y `GET /flashcards/{card_id}`. Las rutas previas con
`user_id` siguen disponibles como obsoletas y no permiten consultar otro usuario.

| Código | Significado |
| --- | --- |
| 401 | Falta token, es inválido, expiró, pertenece a otro proyecto o la sesión fue revocada/usuario deshabilitado |
| 403 | El identificador enviado intenta acceder a otro usuario |
| 404 | La tarjeta no existe para el usuario autenticado |
| 409 | Versión desactualizada, ID de repaso reutilizado con otros datos o fecha inválida |
| 422 | Datos de entrada inválidos; por ejemplo `rating: 9` |
| 503 | Firebase, Firestore o el proveedor correspondiente no están disponibles/configurados |

La revisión de revocación exige una consulta a Authentication. Un fallo del servicio
no se interpreta como sesión válida. La aplicación no guarda ni registra tokens.

## Estructura de Firestore

```text
usuarios/{sha256(uid)}/
  flashcards/{card_id}       contenido, UID, estado FSRS serializado, versión y fechas
  conceptos/{sha256(clave)}  índice de unicidad: card_id
  repasos/{review_id}        petición original, respuesta y log FSRS
  sesiones/{session_id}      evaluación inmutable, entrada original y resumen
```

La clave del concepto normaliza Unicode, espacios y mayúsculas. Los documentos de
usuario funcionan como contenedores de subcolecciones; no se crea aún un perfil de
usuario en el documento padre. Los IDs codificados evitan interpretar `/` en UID o
conceptos como segmentos de ruta. Los UUID y las fechas UTC se generan en servidor.

Creación: transacción con todas las lecturas antes de escribir; un concepto existente
conserva contenido y progreso. Repaso: transacción que guarda estado e historial
juntos, con control de versión e idempotencia por usuario y `review_id`. Las consultas
de pendientes usan el índice automático de `due_at` y orden por ID de documento.

El SDK Admin accede mediante permisos IAM; las reglas de clientes de Firestore no
reemplazan la autorización implementada en FastAPI. Esta integración no abre reglas
ni concede acceso directo del frontend a Firestore.

Los datos anteriores de SQLite permanecen en su archivo; no se migran ni se reasignan
a UID de Firebase automáticamente. Las evaluaciones nuevas se guardan y consultan
según [EVALUATION_HISTORY.md](EVALUATION_HISTORY.md).

## Pruebas

Resultado vigente con historial: **125 pruebas aprobadas**, incluidas 21 externas
de Firestore. La suite habitual ejecuta 104 pruebas locales y omite esas 21 externas.

Suite sin llamadas externas ni credenciales:

```powershell
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

Se omiten por defecto las pruebas externas de Firestore. Para ejecutarlas, con `.env`
configurado, habilitarlas explícitamente y retirar la variable al finalizar:

```powershell
$env:FIRESTORE_INTEGRATION_TESTS = '1'
try {
    .\venv\Scripts\python.exe -m unittest tests.test_firestore_integration -v
    .\venv\Scripts\python.exe -m unittest tests.test_evaluation_firestore_integration -v
} finally {
    Remove-Item Env:FIRESTORE_INTEGRATION_TESTS -ErrorAction SilentlyContinue
}
```

Esas pruebas escriben sólo en `codebreaker_integration_tests`, con UID aleatorios,
y eliminan sus documentos al terminar. No modifican tarjetas de usuarios existentes.
Si el proceso termina abruptamente, podrían quedar datos de prueba en esa colección.
Las pruebas cubren unicidad y consultas, persistencia, concurrencia, reintentos,
versiones, acceso cruzado y rollback si falla el historial.

## Fuentes técnicas

- [Firebase Admin para Python](https://firebase.google.com/docs/admin/setup).
- [Verificación de ID tokens](https://firebase.google.com/docs/auth/admin/verify-id-tokens).
- [Inicio de sesión por REST](https://firebase.google.com/docs/reference/rest/auth#section-sign-in-email-password).
- [Transacciones de Firestore](https://firebase.google.com/docs/firestore/manage-data/transactions).
- [Ubicaciones de Firestore](https://firebase.google.com/docs/firestore/locations).
