# cerebro-auth (`auth_*`)

A diferencia de memory/docs/flows, `cerebro-auth` no es una fuente de
conocimiento del usuario — administra el **acceso al propio ecosistema**:
quién existe (`users`), cómo se agrupan (`groups`), qué puede ver cada grupo
(`group_scopes`) y qué credenciales existen (`api_tokens`). Usá estas tools
**solo cuando el usuario pida explícitamente** crear/gestionar un usuario, un
grupo o un token — nunca como parte del protocolo normal de inicio de
conversación (regla cero de `SKILL.md`), y nunca por iniciativa propia: es
superficie de seguridad, no de recuperación de contexto.

Todas requieren que el token con el que estás llamando sea `access_level:
admin` — si no lo es, la tool devuelve `error` y no hay nada que reintentar
con argumentos distintos, es un rechazo del servidor, no un problema de la
llamada.

## Herramientas

| Herramienta | Úsala para |
|---|---|
| `auth_create_user` | Crear una identidad nueva (persona o servicio). No otorga acceso por sí sola — eso requiere sumarla a un grupo. |
| `auth_list_users` | Ver usuarios existentes y su `access_level`, antes de `auth_add_group_member` o `auth_create_token(user=...)`. |
| `auth_create_group` | Crear un grupo nuevo — nace sin permisos, es solo un balde con nombre. |
| `auth_set_group_scopes` | Definir qué módulos/contextos puede ver un grupo. REEMPLAZA los scopes actuales del grupo, no es aditivo. |
| `auth_add_group_member` | Sumar un usuario existente a un grupo, para que herede sus scopes. |
| `auth_create_token` | Emitir una credencial real. Ver "Dos ejes independientes" abajo — es la tool más delicada de las ocho. |
| `auth_list_tokens` | Auditar qué tokens existen (metadata, nunca el valor secreto) antes de revocar algo obsoleto. |
| `auth_revoke_token` | Revocar un token — inmediato e irreversible, quien lo usaba pierde acceso ya mismo. |

## Dos ejes independientes en `auth_create_token` / `auth_set_group_scopes`

1. **Nivel** (`access_level` vs. `user`, solo en `auth_create_token`) — exactamente
   uno de los dos, nunca ambos: pasá `access_level` directo para un token de
   servicio sin usuario asociado, o pasá `user` para que el token herede el
   nivel de ese usuario. Pasar los dos a la vez es un error.
2. **Alcance** (`allowed_modules`/`module_scopes`, en ambas tools) — dos capas:
   `allowed_modules` es el candado grueso (qué módulos toca en absoluto);
   `module_scopes` es el candado fino dentro de cada módulo permitido (ej.
   `{"memory": {"contexts": ["proyecto-x"]}}`). Un módulo fuera de
   `allowed_modules` es inalcanzable sin importar lo que diga `module_scopes`.

Para un token atado a un usuario `owner`, `allowed_modules`/`module_scopes` se
pueden omitir del todo para heredar completo lo que sus grupos ya otorgan — es
el caso común de "este token simplemente ES ese usuario". Si se pasan
explícitos en ese caso, solo pueden **angostar** lo heredado, nunca
ampliarlo — el servidor rechaza un intento de ensanchar en vez de recortarlo
en silencio.

## El valor del token se muestra UNA sola vez

`auth_create_token` devuelve el secreto en texto plano solo en esa respuesta.
Nadie — ni un admin — puede volver a recuperarlo después. Si el token es para
otra persona o servicio, entregáselo como parte de terminar la tarea, no
asumas que vos o quien sea lo va a poder consultar más tarde. Si se perdió, la
única salida es `auth_revoke_token` + crear uno nuevo.

## Errores y honestidad

Mismo criterio que el resto de cerebro (ver `SKILL.md`): un `error` se
reporta tal cual, sin reintentar a ciegas — sobre todo un rechazo por falta de
`access_level: admin`, que no se soluciona cambiando argumentos. Antes de
revocar un token, confirmá con el usuario si hay dudas de que pueda romper
algo en uso, salvo que ya haya pedido específicamente revocar ese token.
