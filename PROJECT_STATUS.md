# Revisión y avance del backend

## Base revisada

Se revisaron `main.py`, ambos routers, los servicios y el entorno instalado.
La separación entre rutas y servicios ofrece una base útil para conectar los
módulos del equipo. La evaluación existente era una heurística basada en longitud
de explicación y complejidad; todavía no evaluaba coherencia técnica.

## Problemas corregidos

| Hallazgo | Efecto anterior | Corrección |
| --- | --- | --- |
| `routers/flashcards.py` vacío | `main.py` fallaba al acceder a `flashcards.router` | Router declarado; endpoints pendientes |
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

## Verificación

23 pruebas automatizadas cubren arranque y OpenAPI, análisis en los tres lenguajes,
métricas de Radon, umbrales de complejidad, validación de entradas, estados sin
explicación o sin clave, respuestas del juez, fallos del proveedor y CORS.
Una prueba ejercita el SDK de Anthropic con transporte simulado.

No se realizó una evaluación remota con Claude: la integración está verificada
con respuestas simuladas, no su calidad pedagógica ni el acceso real del proveedor.
La rúbrica y el umbral de puntuación baja deben validarse con el dataset etiquetado
del proyecto antes de interpretarlos como un benchmark.

## Trabajo pendiente

`services/fsrs_engine.py` sigue pendiente, al igual que endpoints de flashcards,
persistencia, autenticación, voz, sandbox y datos para DKT. El próximo bloque de
backend es FSRS con pruebas de programación de repasos y contrato de estado por
usuario; después, su persistencia e integración con Firebase.
