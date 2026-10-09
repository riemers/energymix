# Changelog

## 0.2.23
- Accu: laden én bewaren worden samen doorgerekend. 's Nachts goedkoop laden en de accu sparen tot de
  dure avondpiek, met alleen zoveel kWh als tot het eind van de bekende prijzen loont. Eerder probeerde
  de planner één actie tegelijk, en dan leverde nachtladen niets op omdat de accu in de goedkope ochtend
  alweer leegliep. Bewaren blijft alleen gebeuren als de schakelaar "Accu bewaren" aan staat
- Laadt hij niet van het net omdat het te weinig oplevert, dan staat bij de planning hoeveel het per kWh
  zou opleveren en wat de drempel is
## 0.2.22
- Zon in de planningsgrafiek beter zichtbaar: gele lijn met gloed bovenop de prijsbalken, lichte
  vulling, de piek per dag erbij (☀ 4,4 kW) en "zon verwacht" in de legenda

## 0.2.21
- "Accu bewaren" is nu een schakelaar, standaard uit (`input_boolean.energymix_accu_bewaren`)
- Bij de schakelaar staat wat bewaren in de huidige planning zou schelen, en wanneer: aan of uit, hij
  rekent beide door. Ook als `sensor.energymix_bewaren_waarde` (met de blokken als attribuut)

## 0.2.20
- Geen onnodig "accu bewaren" meer: energie die aan het eind van de planning nog in de accu zit werd te
  hoog gewaardeerd (goedkope prijs + laadverlies), waardoor bewaren 's nachts altijd een beetje winst
  leek. Nu: wat die kWh later echt bespaart (na ontlaadverlies)
- Bewaren of van het net laden om later méér terug te leveren (bv. omdat het terugleveren op de reserve
  stopt) moet nu de drempel voor terugleveren halen (`export_min_spread`), niet die voor laden
- De uitleg zegt dan ook eerlijk "meer terugleveren om ..." in plaats van "accu dekt om ..."

## 0.2.19
- Prognose "accu leeg om" als er geen auto laadt: alleen huis en zon, met marge (krap: 20% meer
  verbruik en 30% minder zon; ruim: 15% minder verbruik en 30% meer zon). Na de bekende prognose
  herhaalt hij het laatste dagpatroon, tot 7 dagen vooruit. Te zien onder "Nu" in het dashboard en als
  `sensor.energymix_accu_leeg_om` (met attributen `krap` en `ruim`)

## 0.2.18
- Geleerd huisverbruik is robuuster: metingen terwijl de auto laadt tellen niet mee, negatieve waarden
  worden 0 en de hoogste 10% per uur (pieken) valt weg. Een opgeblazen nachtverbruik liet de planner
  denken dat de accu leeg zou raken, en dan ging hij 's nachts "bewaren"
- Bij bewaren staat erbij wanneer de accu zonder sturing leeg zou zijn, en met welk verbruik en welke zon
- Statistiek: verwacht huisverbruik en zon voor de komende 24 uur, en wanneer de accu zonder sturing leeg is

## 0.2.17
- "Accu bewaren" gaat nu in blokken van minstens een uur, en liever één lang blok dan een paar losse:
  geen losse kwartiertjes meer verspreid over de nacht
- Uitleg bij bewaren laat de echte winst per kWh zien

## 0.2.16
- Accu laadt sneller als er ruimte is: in een goedkoop laad-kwartier mag de regelaar tot DVCC max als
  de fases het toelaten (was: nooit boven de geplande stroom)
- Omhoog regelen met de helft van het verschil i.p.v. 10 A per 2 minuten; omlaag blijft direct
- Planner rekent de zonprognose mee in de ruimte per fase (zon levert terug op dezelfde fases)

## 0.2.15
- Laadplan vergelijkt het goedkoopste blok 's nachts (22-07) met overdag (07-19), binnen de bekende
  prijzen, en laat zien welke gekozen is. Na ±13:00 telt morgen mee: hang je 's avonds een auto aan en
  is morgenmiddag goedkoper, dan wacht hij tot morgen
- Nieuwe keuze "Laadmoment" (`input_select.energymix_laadmoment`): Automatisch (goedkoopst),
  's Nachts of Overdag
- "Vol vóór" geldt alleen met de schakelaar "vannacht"; dat staat er nu bij

## 0.2.14
- Prijsbalken in de Tibber-kleuren per kwartier (zeer goedkoop → zeer duur), ook in de tooltip en bij "Prijs nu"
- Minigrafiek rechtsboven vervangen door "Vandaag": verbruik, zon, auto, accu (netto +/−) en net in kWh

## 0.2.13
- Fix: kleine laadacties op 20 A (bv. om 16:00 net na het laden van de auto). De planning gebruikte
  het "geleerde" laadvermogen, dat gemeten is terwijl de zon laadde (~3 kW) en dus te laag is; nu het
  echte vermogen (DVCC max, begrensd per fase)
- Geen mini-acties meer: laden onder de minimale laadstroom of terugleveren onder 0,5 kWh per kwartier
  vervalt en het plan wordt opnieuw doorgerekend

## 0.2.12
- Auto's leren: max actieradius = actieradius / accu% x laadlimiet (Tesla), gemiddeld over metingen en
  bewaard; ook "Complete" van de Zappi telt als meting. Vervangt de vaste max-km in de planning
- Laadsnelheid (km/u) gemeten uit de Tesla-laadsnelheid of uit de stijging van de actieradius
- Tijdens Fast laden gebruikt de planning de "tijd tot vol" van de auto zelf, zodat het laadblok
  korter wordt en in de goedkoopste uren valt
- Tesla-entities worden automatisch gevonden (battery_level, charge_limit, time_to_full_charge,
  charge_rate); laadplan en Instellingen tonen geleerde waarden en wat de auto zegt

## 0.2.11
- Nieuwe schakelaar "Auto nu snel laden" (`input_boolean.energymix_auto_snel_laden`, standaard uit):
  Zappi meteen op Fast tot de auto vol is, boven alle planning. Gaat vanzelf weer uit als de auto vol
  is of de stekker eruit gaat (aanzetten vóór het insteken mag). Handig voor HomeKit

## 0.2.10
- Veiligheid: bij uitzetten van "Aansturen" en bij stoppen/herstarten van de add-on worden de
  Victron-instellingen die live stonden teruggezet naar standaard (ESS zelfverbruik, DVCC max,
  setpoint normaal, critical loads). Zo blijft de Victron nooit hangen in terugleveren,
  bewaren of laden van het net

## 0.2.9
- Fix: Zappi pendelde tussen Fast en Eco+ (losse goedkope kwartieren, zoals 9 minuten in het
  lopende kwartier). De auto laadt nu in één aaneengesloten goedkoopste blok, zoals in Node-RED
- Laadt de auto al, dan laadt hij door, tenzij een later blok meer dan 1 ct/kWh goedkoper is

## 0.2.8
- Fix: laadt de auto, dan gaat de zon eerst naar de auto en pas de rest naar huis en accu.
  De verwachte accu-lijn stijgt dus niet meer tijdens het laden van de auto

## 0.2.7
- Ochtend-eco terug als vaste keuze: accu > 60% en ≥ 35 kWh zon verwacht → auto 's ochtends op Eco
  met de Victron op all loads; wat dat oplevert gaat af van de Fast-laadtijd in de goedkoopste uren
- Tijdens eco wordt de accu nooit bewaard of van het net geladen
- Eco-drempels instelbaar in Instellingen → Auto; laadplan laat zien of eco vandaag doorgaat en waarom

## 0.2.6
- Auto laadt weer zoals in Node-RED: ontbrekende km tot de max-actieradius / laadsnelheid
  (65 km/u) = laadtijd, in de goedkoopste bekende uren. Geen minimum-km meer
  (helper `input_number.energymix_auto_minimum` wordt niet meer gebruikt; mag weg)
- "Klaar om" geldt alleen nog als `vannacht` aan staat
- Zon telt weer mee als ruimte op een fase; de Zappi-terugregeling vangt een wolk op
- Laadplan toont hoeveel km er mist en hoe lang dat laden duurt

## 0.2.5
- Veiligheid: teruglevering (zon) telt niet meer als extra ruimte op een fase; maximaal altijd
  zekering - marge (bv. 23 A), ook als de panelen veel leveren
- Planning: laadsessies van de auto als balken ("Fast 12:00–14:30") en accu-acties
  (laden / bewaren / terug), plus een lijst met van–tot, modus, kWh, gemiddelde prijs en
  actieradius voor en na
- Kortere tooltip: prijs, accu %, auto km en één reden

## 0.2.4
- Kies zelf welke accumonitor de SoC levert (standaard de actieve monitor van de GX, bv. de Lynx);
  Instellingen tonen alle accu's die de GX kent met naam, SoC en vermogen
- Zonnepanelen: de Envoy is leidend (zelf gevonden als het veld leeg is); de PV-omvormer van de
  Victron alleen als reserve wanneer de Envoy niet beschikbaar is

## 0.2.3
- Accuniveau komt nu van de Victron GX zelf (MQTT); de HA-sensor is alleen terugval.
  Wijken ze meer dan 5% af, dan staat dat in het logboek
- Instellingen: waarschuwing als een accu-, net- of fasesensor al 12 uur niet is bijgewerkt

## 0.2.2
- Live waarden rechtstreeks van de Victron GX via MQTT: netvermogen per fase, accuvermogen, SoC.
  Gebruikt als de HA-entity leeg is (geen extra sensoren nodig)
- Victron-fases automatisch uit de Multi's (`victron_phases: auto`)
- Instellingen: suggesties voor bekende entities (Envoy, myenergi) met knop "gebruik",
  en per leeg veld wat er in plaats daarvan gebruikt wordt

## 0.2.1
- Rekenen per fase (3x25A): de krapste fase waar de Victron op laadt bepaalt de laadstroom van de
  accu; fasesensoren instelbaar (W, kW of A), met fasebalken in het dashboard
- Geen ruimte op een fase: laadstroom 0 in plaats van het minimum
- Nieuwe actie "bewaren": in goedkope uren huis van het net en accu sparen voor de dure uren

## 0.2.0
- Nieuwe planner: simuleert het accuniveau per kwartier (zon, huis, auto) en laadt/levert alleen
  als het na verliezen echt iets oplevert; reden per slot ("bespaart om 19:00 …")
- Auto: minimum-actieradius vóór "klaar om" in de goedkoopste slots, daarboven alleen goedkoop;
  ochtend-eco alleen als de zonprognose het gat weer vult
- Terugleveren bij grote prijsverschillen (Victron grid-setpoint), met reserve, nooit als de auto laadt
- Laadstroom-regelaar: ruimte op de aansluiting, stappen, rustig omhoog, Zappi-terugregeling gaat voor
- Seizoenpatroon (zomer: middag goedkoop / winter: nacht goedkoop), automatisch of handmatig
- HA-helpers (hoofdschakelaar, terugleveren, accu doel/reserve, auto minimum/klaar om, seizoen)
  en sensoren (status, prijs, plannen, besparing)
- Statistiek: laagste accustand per dag, geladen/ontladen, gemeten laadvermogen, tijd tot doel
- Dashboard opnieuw ontworpen: live energiestroom, verhaal, bijsturen, planning met accu-verwachting,
  statistiek en instellingen met entity-kiezer

## 0.1.3
- Logboek: geen regel meer per dashboard-refresh

## 0.1.2
- Fix: verbinding met Home Assistant faalde (`auth_invalid`) omdat s6 de SUPERVISOR_TOKEN wiste

## 0.1.1
- Icoon en logo voor de Add-on Store, favicon in het dashboard

## 0.1.0
- Eerste versie: planner met de regels uit de Node-RED "Tibber Strategie" flow (v4.1)
- Shadow mode, per onderdeel live te zetten
- React-dashboard met tijdlijn en "waarom" per beslissing
