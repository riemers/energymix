# Changelog

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
