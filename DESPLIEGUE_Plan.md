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

Medido sobre datos reales, no estimado:

| | |
|---|---|
| Actividades del backfill inicial (1 usuario) | **1.822** |
| Peticiones que eso cuesta (actividad + streams) | **~3.644** |
| Límite de Strava **por aplicación**, no por usuario | ~200/15 min · **~2.000/día** |

**Un solo usuario nuevo se come la cuota diaria entera de toda la app.** Con
diez altas en una semana, el servicio se atasca y nadie sincroniza.

Esto deja de ser un detalle de implementación y pasa a ser la pieza central del
diseño. Consecuencias, todas obligatorias:

- **El backfill deja de ser síncrono** y pasa a una **cola con cubo de fichas**
  que respeta 200/15 min. Un usuario nuevo se importa a lo largo de horas o días.
- **Prioridad por utilidad**: primero las actividades recientes (CTL/ATL y el
  plan de hoy dependen de ellas), después hacia atrás. El usuario tiene un plan
  útil en minutos aunque su historial tarde días.
- **Los streams, solo donde hacen falta**: últimos ~180 días para la curva de
  potencia, más los tests maximales marcados. El resto, metadatos.
- **Progreso honesto en la UI**: "importando tu historial: 340/1.822". Nada de
  fingir que ya está.
- **Pedir ampliación de límite** a Strava en cuanto haya usuarios reales.

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

## 3. Almacenamiento: comprimir los streams

Medido sobre la BD real:

| | Tamaño |
|---|---|
| BD completa (1 usuario) | **219 MB** |
| De los cuales, tabla `stream` | **204 MB** |
| Streams en JSON + gzip | 49 MB (4,2x) |
| Streams en **binario + zlib** | **22 MB (9,3x)** |

Los vatios son enteros pequeños: guardarlos como JSON es pagar ~9x de más.
Comprimir baja el coste de "2 usuarios por capa gratuita" a "~20", y abarata
la migración inicial.

Decisión: `stream.data` pasa de JSONB a `bytea` comprimido, con la
serialización encapsulada en el repositorio para que nada más se entere.

---

## 4. Tres cosas del código que la escala rompe

Salen de leer el código actual, no de suponer:

**(a) El precalentado de caché es O(usuarios).** `_warm_cache()` recorre todos
los atletas con actividades y cada uno cuesta ~8 s. Con 50 usuarios son ~7
minutos de CPU en cada arranque. Debe pasar a **perezoso** (al primer uso de ese
atleta) o a un trabajo programado, no al arranque.

**(b) La caché de CP vive en el proceso.** `_SMOOTH_CACHE` es un dict en
memoria. Con una sola instancia es correcto; el día que haya dos, cada una
tendrá la suya. No es un problema ahora — sí es una razón para **arrancar con
una sola instancia** y anotarlo.

**(c) Hay un bucle de fondo permanente.** El scheduler de sync y ese caché
exigen un **proceso vivo**. Eso descarta hosts puramente serverless por
funciones: hace falta una VM pequeña que no se duerma.

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

Sin tocar infraestructura todavía, y todo verificable en local:

1. Compresión de streams + migración idempotente de los existentes.
2. Cola de backfill con cubo de fichas y prioridad por recencia.
3. Precalentado perezoso en vez de al arranque.
4. Configuración por variables de entorno (hoy hay cosas que asumen `.env`).
5. `healthcheck` para que el host sepa si la app está viva.

### Fase 2 — Endurecer *(mío)*

Deja de ser opcional en cuanto haya una URL pública:

1. Cookie de sesión con `Secure` cuando el origen sea HTTPS.
2. **Límite de intentos en `/api/login`** — hoy no hay ninguno.
3. **Registro por invitación** — sin esto, una URL pública es una URL donde
   cualquiera se crea una cuenta en tu servidor.
4. **Matar el pestillo `AUTH_ENABLED=false`** en producción: salta el login
   entero y haría que todos cayeran en el primer atleta.
5. Correo como identidad + recuperación de contraseña.

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

| Partida | Arranque | Con ~20 usuarios |
|---|---|---|
| Postgres (Neon) | 0 € | 0 € (gracias a comprimir) |
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
