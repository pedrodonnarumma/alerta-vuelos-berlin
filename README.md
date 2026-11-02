# Alerta de ofertas: EZE / SCL → Berlín (abril–mayo 2027, solo ida)

Bot que corre todos los días en GitHub Actions, consulta Google Flights vía SerpApi
y te avisa por Telegram cuando aparece un precio significativamente bajo.

## Cómo decide si es oferta (`config.yaml`)
Avisa si se cumple **cualquiera** de estas:
1. Precio ≤ `precio_maximo` (por defecto USD 600, ≈ −15 % sobre la base de 709).
2. Precio ≥ 15 % por debajo de la mediana que el propio bot fue observando para ese origen
   (se activa con 15+ observaciones).
3. Google marca el precio como `low` **y** está por debajo de su rango típico.

No repite el aviso para el mismo origen+fecha salvo que baje USD 20 más.

## Presupuesto de búsquedas
2 orígenes × 61 días = 122 combinaciones. Con 8 búsquedas por día (≈240/mes, dentro de las
250 gratis de SerpApi) recorre toda la ventana cada ~16 días, con fechas salteadas en cada corrida.
Si sumás MDZ, bajá `busquedas_por_corrida` o pasá a un plan pago.

## Puesta en marcha (≈10 min)
1. **SerpApi**: creá una cuenta en serpapi.com y copiá tu API key.
2. **Bot de Telegram**:
   - Hablale a `@BotFather` → `/newbot` → guardá el token.
   - Mandale cualquier mensaje a tu bot y abrí
     `https://api.telegram.org/bot<TOKEN>/getUpdates` → copiá `message.chat.id`.
3. **Repo**: subí esta carpeta a un repo de GitHub (puede ser privado).
4. **Secrets** (Settings → Secrets and variables → Actions):
   `SERPAPI_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`.
5. **Settings → Actions → General → Workflow permissions** → *Read and write*
   (para que pueda commitear el historial).
6. Pestaña **Actions** → "Alerta vuelos Berlín" → **Run workflow** para probarlo.

Para probar localmente:
```bash
pip install -r requirements.txt
SERPAPI_KEY=... python alerta.py
pytest -q   # tests con respuestas simuladas
```

## Limitación sobre la valija despachada
Ni Google Flights ni SerpApi garantizan que el precio de vuelos internacionales incluya
la valija despachada: depende de la familia tarifaria (LATAM "Light", Iberia "Basic", etc.).
Por eso cada alerta te recuerda verificarlo. Si alguna aerolínea te molesta por eso, sumala a
`aerolineas_excluidas`.

## Notas
- En repos públicos, GitHub desactiva los workflows programados tras 60 días sin actividad;
  el commit diario del historial debería evitarlo, pero si llega un mail de aviso, reactivalo desde Actions.
- `data/historial.json` sirve también para graficar cómo evolucionan los precios.
