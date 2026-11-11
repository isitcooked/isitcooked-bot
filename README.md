# Radar bot: puesta en marcha (≈1-2 h una sola vez)

1. Crea un repositorio **público** en GitHub (Instagram necesita URLs públicas de imagen) y sube todo este contenido.
2. En el repo: Settings > Secrets and variables > Actions > **Secrets**:
   ANTHROPIC_API_KEY, X_API_KEY, X_API_SECRET, X_ACCESS_TOKEN, X_ACCESS_SECRET, IG_USER_ID, IG_ACCESS_TOKEN
   (X: app con permisos Read+Write y tokens de tu cuenta. Instagram: cuenta Business/Creator ligada a una página de Facebook, app de Meta con permiso instagram_content_publish.)
3. Mismo menú, pestaña **Variables**: BRAND (por defecto "Is It Cooked"). DRY_RUN = true (modo prueba, no publica).
4. Settings > Actions > General > Workflow permissions: **Read and write**.
5. Pestaña Actions > radar > Run workflow, elige un job. Revisa en los logs lo que habría publicado.
6. Cuando te guste, cambia DRY_RUN a **false**. Desde ahí corre solo según el calendario del archivo radar.yml.

Posts bloqueados por el validador (números que no están en los datos, palabras prohibidas, falta de aviso) se guardan en la carpeta `held/` y NO se publican.
Aviso: no probado contra las APIs en vivo (DexScreener, RugCheck, X, Meta). Primera ejecución siempre en DRY_RUN.
