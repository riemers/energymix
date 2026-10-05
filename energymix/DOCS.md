# Energymix

Energymix bepaalt elk kwartier wat je thuisaccu, zonnepanelen en Zappi moeten doen op basis van
de Tibber-prijzen, en laat bij elke beslissing zien **waarom**. Het vervangt de "Tibber Strategie"
Node-RED-flow met één planner in plaats van losse regels die elkaar overschrijven.

## Shadow mode (standaard)

Zolang alles onder `control` op `false` staat, stuurt de add-on **niets** aan. Hij rekent, toont
het plan in het dashboard en logt elke beslissing. Laat hem zo een paar dagen naast Node-RED
draaien en vergelijk.

Daarna neem je het per onderdeel over: zet bijvoorbeeld `control.pv: true` en schakel dezelfde
nodes in Node-RED uit. Volgorde-advies: `pv` → `ess` + `dvcc` → `zappi` + `feed_in`.

| Onderdeel | Wat het doet |
|-----------|--------------|
| `pv`      | Envoy-productie uit bij negatieve prijs (`pv_switch_entity`) |
| `ess`     | Victron BatteryLife state 9 (accu laden) of 10 (zelfverbruik) via MQTT |
| `dvcc`    | DVCC max laadstroom; in een negatief blok zo verdeeld dat de accu aan het eind vol is |
| `zappi`   | Zappi-modus Fast / Eco / Eco+ (`zappi_mode_entity`) |
| `feed_in` | Victron `Hub4/DisableFeedIn` (0 = alle loads, accu mag de auto laden) |

## Belangrijkste opties

- `tibber_token`: je Tibber API-token (developer.tibber.com).
- `price_resolution`: `HOURLY` of `QUARTER_HOURLY`. De planner werkt met echte tijdstempels,
  dus beide werken, ook op dagen met zomer-/wintertijdwissel.
- `mqtt_host`, `victron_portal_id`, `victron_vebus_instance`: je Venus GX / Cerbo.
- Entities: zelfde als in de Node-RED-config. Laat `zappi_*` leeg als je geen Zappi hebt,
  en `pv_switch_entity` leeg als je geen PV wilt curtailen.
- `cars`: per auto `name`, `max_range_km`, `cable_entity`, `location_entity`, `range_entity`.
- `input_select.carcharger` met opties `auto`, `fast`, `slow`, `ecoa` blijft je bediening.

## Beslisregels

Accu (prioriteit hoog → laag):
1. Negatieve prijs → laden, laadstroom verdeeld over het negatieve blok.
2. Goedkoop (< `cheap_price`) → laden, tenzij er later vandaag nog een negatieve prijs komt
   of de zonverwachting genoeg is om de accu vol te krijgen (en de auto niet laadt).
3. Lage SoC en weinig zon → laden in de goedkoopste `lowest_price_ess_minutes` van de dag.
4. Anders zelfverbruik.

Zappi in `auto`:
1. Prijs ≤ `force_fast_price` → Fast.
2. Goedkoopste aaneengesloten venster voor de benodigde laadtijd (uit actieradius) → Fast.
   Met `vannacht` aan moet het venster klaar zijn vóór `vannacht_ready_hour`.
3. Ochtend (5–12u), genoeg zon, accu > 60% en geen Fast meer gepland → Eco vanuit de accu.
4. Anders Eco+.
