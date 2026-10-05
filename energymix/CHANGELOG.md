# Changelog

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
