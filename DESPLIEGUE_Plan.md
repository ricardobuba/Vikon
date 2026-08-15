# Plan de despliegue — de la LAN a un servicio

> Paso 2 del camino hacia app de pago en Google Play. Estado: **propuesta**.
> Objetivo inmediato: familia y amigos usándolo desde cualquier red, sin que
> nada de lo construido haya que tirarlo después.

---

## 1. Qué cambia

Hoy el motor, Postgres y la sincronización viven en el PC de casa. Un servicio
de suscripción no puede depender de que ese PC esté encendido, así que el
despliegue no es una mejora: es el cimiento.

Lo bueno es que **no hay que rehacer la app**. La API ya es JSON, la auth ya
aísla por atleta, y el OAuth de Strava ya es por usuario. Lo que falta es
infraestructura y tres cambios de código que la escala obliga.

---

## 2. La restricción que manda: Strava

La primera medición asustaba: **~3.644 peticiones** para el backfill de un
usuario, contra un límite de ~2.000/día **por aplicación**. Un alta nueva
agotaba la cuota diaria entera.

Pero al mirar de dónde salían, la cosa cambia por completo:

| Qué se pide | Peticiones | Tamaño |
|---|---|---|
| **Metadatos** de TODO el historial (1.822 act.) | **~10** | 6 MB |
| **Streams**, una petición por actividad | ~1.822 | 204 MB |

Strava devuelve los resúmenes **en páginas de 200**, así que el historial
entero de metadatos cuesta diez peticiones. Los streams son el 99,7% del coste
y el 97% del espacio.

**No hay que recortar el historial: hay que recortar los streams.** Medido:

| | Actividades | Peticiones | Streams |
|---|---|---|---|
| Historial completo (2017-2026) | 1.822 | ~1.832 | 204 MB |
| **Metadatos completos + streams de 12 meses** | 1.822 | **~239** | **22 MB** |

**15x menos peticiones**, y cabe de sobra en la cuota de un día.

Lo importante es que esto **no sacrifica análisis**: el histórico de CTL/ATL/TSB,
los umbrales personalizados y el percentil de forma salen de los METADATOS, que
se conservan enteros. Eso respeta la decisión de "usa todos los datos", tomada
precisamente porque un año flojo no debe borrar la capacidad de fondo.

El resto de medidas siguen en pie:

- **Backfill en cola con cubo de fichas** que respeta 200/15 min.
- **Prioridad por utilidad**: metadatos primero (el plan de hoy ya funciona con
  ellos), streams recientes después.
- **Progreso honesto en la UI**, sin fingir que ya está.
- **Pedir ampliación de límite** a Strava cuando haya usuarios reales.

---

## 2b. La pieza que lo resuelve todo: guardar la MMP, no el stream

El motor de CP **no consume el stream crudo**: consume su curva de potencia
máxima media (MMP). Medido:

| | |
|---|---|
| Stream crudo de una actividad | ~110 KB |
| Su MMP (10 duraciones) | **80 bytes** |
| MMP de las 1.051 actividades con potencia | **0,08 MB** |

Y el precalentado, perfilado:

```
smoothed_cp_states  2,19 s en frío   (7,8 s con el disco realmente frío)
   ├── cargar streams de BD + limpiar + calcular MMP ... ~2,2 s   ← TODO está aquí
   └── filtro de Kalman ............................... despreciable
```

**El coste entero es cargar 204 MB para volver a derivar unos números que ya
calculamos ayer.** Si la MMP se persiste al ingerir, el precalentado pasa a leer
0,08 MB y el filtro es instantáneo.

De ahí sale el modelo de retención:

| Dato | Se guarda | Para qué |
|---|---|---|
| **Metadatos** de actividad | siempre | TSS, CTL/ATL/TSB, umbrales, cumplimiento |
| **MMP** derivada | siempre (80 B) | CP/W'/FTP, curva de potencia, coherencia |
| **Stream crudo** | últimos 12 meses | zonas e intervalos (clasificar la sesión) |

Con eso: **~9 MB por usuario** (6 metadatos + 2,4 de streams comprimidos + 0,08
de MMP) y precalentado en milisegundos.

Nota sobre tus datos: tú ya tienes los 204 MB descargados. No se tiran sin más
— primero se calcula la MMP de **todo** el historial (2017-2026), y solo después
se sueltan los streams antiguos. Así conservas la trayectoria de CP completa,
que un usuario nuevo no podrá tener.

### Antes que nada: los términos

El acuerdo de API de Strava tiene restricciones sobre **uso comercial** y sobre
cobrar por acceso a sus datos. No puedo afirmarte el detalle —cambian y mi
conocimiento tiene fecha de corte—, pero **esto se lee antes de escribir una
línea de este plan**. Si bloquea el modelo de suscripción, es infinitamente más
barato saberlo hoy.

Plan B si bloquea: ingesta por **archivo .FIT** subido por el usuario, o
Garmin/Intervals.icu como fuente. La arquitectura ya separa `adapters/`, así que
el cambio es acotado — pero conviene saberlo antes, no después.

---

## 3. Almacenamiento: comprimir lo que quede

Aun con retención de 12 meses, los streams que se guardan siguen siendo el
grueso. Medido sobre la BD real:

| | Tamaño |
|---|---|
| BD completa (1 usuario) | 219 MB |
| Tabla `stream` | 204 MB |
| Streams en JSON + gzip | 49 MB (4,2x) |
| Streams en **binario + zlib** | **22 MB (9,3x)** |

Los vatios son enteros pequeños: guardarlos como JSON cuesta ~9x de más.
Aplicado a los 22 MB de los últimos 12 meses, quedan **~2,4 MB por usuario**.

Decisión: `stream.data` pasa de JSONB a `bytea` comprimido, con la
serialización encapsulada en el repositorio para que nada más se entere.

---

## 4. Tres cosas del código que la escala rompe

Salen de leer el código actual, no de suponer:

**(a) El precalentado de caché es O(usuarios).** `_warm_cache()` recorre todos
los atletas con actividades, a ~2-8 s cada uno. Con 50 usuarios son minutos de
CPU en cada arranque, y el problema crece con las altas.

Con la MMP persistida (§2b) el trabajo desaparece casi entero, pero aun así
conviene que **no sea al arranque**: pasa a perezoso, la primera vez que ese
atleta pide algo. Así el servidor levanta al instante y nadie paga por usuarios
que hoy no van a entrar.

**(b) La caché de CP vive en el proceso.** `_SMOOTH_CACHE` es un dict en
memoria. Con una sola instancia es correcto; el día que haya dos, cada una
tendrá la suya. No es un problema ahora — sí es una razón para **arrancar con
una sola instancia** y anotarlo.

**(c) Hay un bucle de fondo permanente.** El scheduler de sync y ese caché
exigen un **proceso vivo**. Eso descarta hosts puramente serverless por
funciones: hace falta una VM pequeña que no se duerma.

---

## 4b. Presupuesto de rendimiento

Sin cifras, "va rápido" es una opinión. Estos son los topes; si algún cambio los
supera, es un fallo, no un matiz:

| Métrica | Tope | Hoy |
|---|---|---|
| `/api/state` con caché caliente | **< 200 ms** | 86 ms ✓ |
| Primera pantalla tras abrir la app | **< 1 s** | 0,6 s ✓ |
| Arranque del servidor (cualquier nº de usuarios) | **< 1 s** | ~2 s × usuarios ✗ |
| Primera petición de un usuario tras reiniciar | **< 300 ms** | 2,2 s ✗ |

Los dos últimos son los que arregla la Fase 1. Y ojo al orden: **el precalentado
perezoso (1.5) sin la MMP persistida (1.1) EMPEORA la cuarta fila** — pasaría a
cobrarle los 2,2 s al primero que entre tras cada despliegue, que hoy no ocurre
porque se precalienta de fondo al arrancar.

Tres cosas de producción que también castigan la carga y no dependen del código:

- **Máquina dormida.** Si el host apaga la instancia por inactividad, el primer
  acceso paga el arranque completo. Debe quedar siempre despierta.
- **Región.** Servir desde EE. UU. añade cientos de ms por petición desde
  España. Madrid o Frankfurt — y de paso lo pide el RGPD.
- **El cumplimiento crece.** Hoy cuesta 1,1 ms porque `plan_log` casi está
  vacío; con 14 días registrados clasificará 14 sesiones (cargando sus streams)
  dentro de `/api/state`. Acotado, pero es lo siguiente que se notará: cuando
  pase de 50 ms, se calcula fuera del camino de la petición.

---

## 5. Arquitectura objetivo

```
Móvil / navegador  →  HTTPS (dominio propio)
                        │
                   Fly.io (1 instancia, región EU)
                    ├── FastAPI (la app de hoy)
                    ├── scheduler de sync (todos los perfiles)
                    └── cola de backfill con límite de Strava
                        │
                   Postgres gestionado (Neon, región EU)
```

**Decisiones y por qué:**

- **Región UE** (Madrid/Frankfurt) — no es un capricho: son datos de
  entrenamiento con frecuencia cardíaca, y el RGPD te va a pedir cuentas de
  dónde viven.
- **Fly.io** por el proceso siempre vivo y por poder fijar región. Railway o
  Render valen igual; lo que no vale es serverless por funciones.
- **Neon** por capa gratuita generosa y ramas de BD (útil para probar
  migraciones sin tocar producción).
- **Una sola instancia** al principio, por (b).
- **Dominio propio** — HTTPS real desbloquea de paso la PWA completa: service
  worker (que hoy por la LAN no llega a registrarse), cookie `Secure`, y la
  puerta a notificaciones push.

---

## 6. Fases

### Fase 0 — Verificar términos de Strava *(tuyo, bloqueante)*

Condiciona todo lo demás. No empieces por otra cosa.

### Fase 1 — Preparar el código *(mío)*

Sin tocar infraestructura todavía, todo verificable en local y con una métrica
de éxito por paso. En este orden, porque cada uno apoya al siguiente:

**1.1 — Persistir la MMP.** Tabla `activity_mmp` (activity_id, duración →
vatios). Se calcula al ingerir y se rellena hacia atrás para lo ya existente.
`load_power_activities` deja de leer streams y pasa a leer MMP.
→ *Éxito: `smoothed_cp_states` baja de ~2,2 s a <100 ms, y el CP estimado no
cambia (mismo número, mismo intervalo).* Esa segunda parte es la que importa:
si cambia, la refactorización está mal.

**1.2 — Retención de streams.** Política de 12 meses: al ingerir no se piden
streams más antiguos; los existentes se sueltan **después** de tener su MMP.
Lo que sigue necesitando stream crudo (clasificar zonas e intervalos) solo mira
días recientes, así que no se rompe.
→ *Éxito: la BD baja de 219 MB a ~30 MB sin que cambie ni el CP ni el plan.*

**1.3 — Comprimir los streams que quedan.** JSONB → `bytea` con zlib.
→ *Éxito: ~9x menos, y los tests de clasificación siguen pasando.*

**1.4 — Cola de backfill con cubo de fichas.** Respeta 200/15 min, prioriza
metadatos y luego streams recientes, y expone progreso.
→ *Éxito: un alta nueva consume ~240 peticiones y deja plan útil en minutos.*

**1.5 — Precalentado perezoso** + `healthcheck` + configuración por variables
de entorno.
→ *Éxito: el servidor levanta en <1 s con N usuarios.*

Los pasos 1.1 y 1.2 son los que responden a lo que pediste: ingerir solo el
último año **sin perder análisis**, y un precalentado rápido de verdad.

### Fase 2 — Endurecer *(mío)*

Deja de ser opcional en cuanto haya una URL pública:

1. ✅ Cookie de sesión con `Secure` — conmutable con `COOKIE_SECURE`. Queda en
   `false` mientras se sirva por HTTP en la LAN (si no, el navegador la
   descarta); se activa el día que haya HTTPS.
2. ✅ El pestillo `AUTH_ENABLED=false` ya **falla cerrado**: con más de una
   cuenta creada devuelve 403 en vez de servir el primer atleta. Sigue siendo
   un rescate válido para un despliegue de una sola persona.
3. ✅ La documentación de la API (`/api/docs`, `/openapi.json`) se apaga con
   `API_DOCS=false`.
4. Límite de intentos en `/api/login`.
5. Registro por invitación: sin esto, una URL pública es una URL donde
   cualquiera se crea una cuenta en tu servidor.
6. Cifrar en reposo los tokens de Strava.
7. Correo como identidad + recuperación de contraseña.

### Fase 3 — Infraestructura *(tuyo lo que toca credenciales, mío el resto)*

1. Cuenta en Neon → BD en región UE → cadena de conexión (**la manejas tú**).
2. Cuenta en Fly.io → app en región UE.
3. Migrar los datos: `pg_dump` local → `pg_restore` remoto (219 MB, o ~40 MB ya
   comprimidos).
4. Secretos como variables de entorno del host, nunca en el repositorio.

### Fase 4 — Dominio y Strava

1. Dominio + DNS apuntando a Fly.
2. Certificado (automático).
3. **Cambiar el Authorization Callback Domain** en tu app de Strava al dominio
   nuevo. El `redirect_uri` ya sale del host de la petición, así que el código
   no cambia.

### Fase 5 — Invitar

Familia y amigos entran por URL, se instalan la PWA, y cada uno conecta su
Strava. Aquí es donde de verdad se prueba el multi-perfil.

---

## 7. Reparto

**Tuyo:** términos de Strava · cuentas de Neon/Fly y sus credenciales · compra
del dominio · cambiar los ajustes de tu app de Strava · decidir a quién invitas.

**Mío:** compresión y migración · cola de backfill con límite · precalentado
perezoso · endurecimiento completo · correo y recuperación · configuración de
despliegue · healthcheck · verificación de que todo sigue funcionando.

---

## 8. Coste

Con ~9 MB por usuario (§2b), una capa gratuita de 500 MB da para **unos 50
usuarios** antes de pagar nada de BD.

| Partida | Arranque | Con ~20 usuarios |
|---|---|---|
| Postgres (Neon) | 0 € | 0 € |
| Servidor (Fly.io) | ~5 €/mes | ~5-7 €/mes |
| Dominio | ~10 €/año | ~10 €/año |
| LLM | ~0 € (capa gratuita) | **a medir** |

Los ~292 tokens de la ficha por turno hacen que el coste de LLM sea pequeño,
pero es el único que crece con el uso: hay que instrumentarlo por usuario antes
de fijar el precio de la suscripción.

---

## 9. Lo que NO se hace todavía

- **El paywall.** Va al final: hasta que no haya gente usándolo no sabrás por
  qué pagarían ni cuánto.
- **Varias instancias / Redis.** Innecesario con esta escala, y añade partes
  móviles.
- **App nativa o Play Store.** El canal de pruebas internas llegará cuando el
  servicio esté estable; la PWA cubre el hueco entretanto.
