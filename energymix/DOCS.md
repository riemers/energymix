# Energymix

Energymix plant elk kwartier wat je thuisaccu, zonnepanelen en Zappi moeten doen op basis van de
Tibber-prijzen, je zonprognose en je verbruik. Bij elke beslissing laat hij zien **waarom**.

## Meekijken of aansturen

Energymix stuurt pas iets aan als twee dingen allebei aan staan:

1. **Per onderdeel** in de add-on-configuratie, onder `control`:

   | Onderdeel  | Wat het doet |
   |------------|--------------|
   | `pv`       | Envoy-productie uit bij negatieve prijs (`pv_switch_entity`) |
   | `ess`      | Victron BatteryLife state 9 (van net laden) of 10 (zelfverbruik) |
   | `dvcc`     | Max laadstroom van de accu, met de regelaar (zie hieronder) |
   | `setpoint` | Victron grid-setpoint: negatief = terugleveren |
   | `zappi`    | Zappi-modus Fast / Eco / Eco+ |
   | `feed_in`  | Victron `Hub4/DisableFeedIn` (0 = accu mag ook naar auto/net) |

2. **De hoofdschakelaar** `input_boolean.energymix_aansturen` in HA (of "Aansturen" in het dashboard).
   Daarmee zet je alles in één keer stil.

Zet je "Aansturen" uit of stop je de add-on, dan zet Energymix de Victron-onderdelen die live
stonden eerst terug naar standaard: ESS zelfverbruik, DVCC max, setpoint normaal en alleen critical
loads. Zo blijft de Victron niet hangen in terugleveren, bewaren of laden van het net.

Advies: begin met `pv`, dan `ess` + `dvcc`, dan `zappi` + `feed_in`, en als laatste `setpoint`.

## Helpers in Home Assistant

Bij de eerste start maakt Energymix deze helpers aan. Je kunt ze op je eigen dashboard zetten of in
automations gebruiken. Ze zijn hetzelfde als de schakelaars in het Energymix-dashboard.

| Helper | Betekenis |
|--------|-----------|
| `input_boolean.energymix_aansturen` | Hoofdschakelaar (uit = alleen meekijken) |
| `input_boolean.energymix_auto_snel_laden` | Auto nu Fast tot vol, boven alle planning; gaat vanzelf uit |
| `input_boolean.energymix_terugleveren` | Terugleveren bij grote prijsverschillen |
| `input_boolean.energymix_accu_van_net_laden` | Accu goedkoop van het net laden |
| `input_number.energymix_accu_doel` | Accu laden tot (%) |
| `input_number.energymix_accu_reserve` | Reserve die nooit teruggeleverd wordt (%) |
| `input_datetime.energymix_auto_klaar_om` | Met "vannacht" aan: auto vol vóór deze tijd |
| `input_select.energymix_seizoen` | Automatisch / Zomer / Winter |

En deze sensoren: `sensor.energymix_status` (het verhaal), `sensor.energymix_prijs_nu`,
`sensor.energymix_accu_modus`, `sensor.energymix_zappi_plan`, `sensor.energymix_seizoen`,
`sensor.energymix_besparing`, `sensor.energymix_accu_vol_om`, `sensor.energymix_auto_vol_om` en
`binary_sensor.energymix_terugleveren`.

## Hoe hij beslist

**Auto (gaat altijd voor).** Net als in de Node-RED-flow: de auto die aan de Zappi hangt wordt herkend
aan de kabel en locatie van de Tesla. Wat er mist tot de max-actieradius (`cars[].max_range_km`),
gedeeld door de laadsnelheid (`charge_speed_km_per_hour`, standaard 65 km per uur), is de laadtijd.
Die wordt ingepland als één aaneengesloten blok op het goedkoopste moment binnen de bekende prijzen
(vandaag en, na ±13:00, morgen). Laadt de auto al, dan laadt hij door tenzij een later blok meer dan
1 ct/kWh goedkoper is; zo pendelt de Zappi niet tussen Fast en Eco+.
Staat `vannacht` aan, dan moet de auto vol zijn vóór "Vannacht: auto vol om"
(`input_datetime.energymix_auto_klaar_om`).

**Ochtend-eco.** Is de accu al behoorlijk vol (`eco_battery_soc_min`, standaard 60%) én wordt het
een echte zonnedag (`eco_solar_min`, standaard 35 kWh volgens de prognose voor vandaag), dan laadt
de auto 's ochtends (5–12u) op Eco, met de Victron op all loads zodat de accu meehelpt. Wat dat
oplevert gaat af van de laadtijd; alleen de rest gaat naar de goedkoopste uren (Fast). Later op de
dag laadt de accu weer bij met zon of, als dat loont, met goedkope netstroom. In het winterpatroon
is er geen ochtend-eco. De drempels pas je aan onder Instellingen → Auto.

**Accu.** Energymix simuleert per kwartier het laadniveau, met de zonprognose en het gemiddelde
huisverbruik per uur (dat leert hij zelf uit je metingen). Hij laadt alleen van het net als die
energie later duurdere stroom vervangt, met minstens `arbitrage_min_spread` winst per kWh na
verliezen. Vult de zon de accu toch al, dan laadt hij niet. Bij een negatieve prijs laadt hij altijd.

**Terugleveren.** Alleen als de schakelaar aan staat, het verschil na verliezen minstens
`export_min_spread` per kWh is, de auto niet laadt, en nooit onder de reserve.

**Bewaren.** In een goedkoop uur kan het voordeliger zijn om het huis van het net te laten draaien
en de accu te sparen voor de dure avond (vooral in de winter, als de nacht goedkoop is). Energymix
zet de accu dan op "keep batteries charged" met laadstroom 0: niet laden, niet ontladen.

**Laadstroom-regelaar (per fase).** Als de accu van het net laadt, kijkt een snelle regeling elke
30 s **per fase** hoeveel ruimte er is:

    vrij(fase) = zekering (25 A) - marge (2 A) - (stroom op die fase - eigen deel van de accu)

De krapste fase waar de Victron op laadt (`victron_phases`, bv. `1` of `1,2,3`) bepaalt de
laadstroom. Zet iemand op één fase de oven aan, dan krijgt de accu op die fase minder. Zon die
teruglevert telt mee als ruimte; valt de zon weg, dan regelt de Zappi terug en verlaagt de regelaar
direct de laadstroom. Daarbij:
- stappen van `dvcc_step_a` (standaard 10 A);
- direct omlaag, maar pas na 2 minuten ruimte één stap omhoog;
- minder ruimte dan de minimale laadstroom: 0 A (dan laadt de accu niet);
- regelt de Zappi zichzelf terug (Fast, maar minder dan `zappi_max_w * 0,85`), dan telt het tekort
  per fase als bezet. De auto gaat voor en de accu neemt die ruimte niet in.

Zonder fasesensoren in HA leest Energymix het netvermogen per fase en het accuvermogen rechtstreeks
van de Victron GX via MQTT (`N/<portal>/system/0/Ac/Grid/L1..L3/Power`), en het aantal fases van de
Multi's (`victron_phases: auto`). Je kunt ook eigen sensoren kiezen onder Instellingen → Net (`grid_l1_entity` t/m `grid_l3_entity`, in W,
kW of A, positief = afname). Zonder fasesensoren neemt de regelaar aan dat het verbruik gelijk over de
fases verdeeld is. Dat is minder veilig, dus stel ze in voordat je `dvcc` live zet.

**Seizoenpatroon.** Energymix vergelijkt de gemiddelde prijs van 11-16u met die van 0-6u over de
afgelopen week. Is de middag goedkoper, dan is het een zomerpatroon; is de nacht goedkoper, een
winterpatroon. In het winterpatroon doet hij geen ochtend-eco uit de accu. De planning zelf volgt
altijd de echte prijzen.

## Victron-instellingen voor terugleveren

Voor `setpoint` moet in de GX bij ESS terugleveren toegestaan zijn (*Grid feed-in*, en
*Limit system feed-in* uit of hoog genoeg). Energymix zet bij terugleveren ook `DisableFeedIn` op 0 en
de ESS-state op 10.

## Instellingen

De meeste instellingen pas je aan onder **Instellingen** in het dashboard. Die worden bewaard in
`/data/settings.json` en gaan boven de add-on-configuratie. Tokens, MQTT en de lijst met auto's
staan alleen in de add-on-configuratie.
