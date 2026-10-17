# Wissellijst V3

AI-gestuurde rotatie van Spotify-playlists: elke lijst krijgt op een vast ritme een nieuw blok
nummers, het oudste blok gaat eruit, en je ziet vooraf wat erin komt. Draait als Docker-stack
op een Synology NAS, uitgerold via GitHub Actions.

## Wat het doet

| Type | Hoe | Voorbeeld |
| --- | --- | --- |
| **Categorie** | GPT stelt per vrije categorie een nummer voor; Spotify en de regels valideren (bestaat het, juiste decennium, niet in historie, max per artiest). Mislukt een categorie, dan vraagt de app GPT opnieuw met de reden erbij. | "80s heeft in de Top 2000 gestaan", "cabarock zoals Ernst van der Pasch" |
| **Discovery** | Scant bronlijsten, filtert op recente releases, laat GPT scoren op een smaakprofiel uit je luistergedrag, selecteert de beste. | "Shiny new singles" uit 8 nieuwe-muziek-lijsten |

Per wissellijst: een **wachtrij** (het volgende blok, per nummer te vervangen), een **historie**
(alles wat ooit in de lijst zat, wordt nooit herhaald), een **rotatieschema** (elk uur, 4x per dag,
dagelijks, wekelijks) en een **audit trail** van elke rotatie.

## Architectuur

Eén Flask-proces (gunicorn, 1 worker) met UI, API en APScheduler. PostgreSQL voor alle data,
inclusief het Spotify-token. Externe diensten: Spotify Web API, OpenAI, optioneel SMTP.

```
app/
  web.py          routes (UI + API), startup: migraties en scheduler
  auth.py         wachtwoord-login (APP_PASSWORD), sessie-cookie
  spotify_auth.py OAuth met het token in de database, state-check
  scheduler.py    APScheduler + _execute_rotation (de ene plek die roteert en vastlegt)
  automation.py   rotatie: oudste blok eruit, wachtrij erin, nieuw blok klaar
  suggest.py      categorie-lijsten: GPT-suggesties + validatie
  discovery.py    discovery-lijsten: scannen, scoren, selecteren
  alerts.py       alarm-mail bij mislukte rotaties, status voor de UI
  config.py       instellingen (env) en de data-laag
  db/             SQLAlchemy-modellen, sessies, migraties (Alembic), V2-import
  templates/      index.html (de hele UI), login.html
tests/            pytest, draait op SQLite zonder Spotify of OpenAI
```

## Lokaal draaien

```bash
cp .env.example .env            # vul SPOTIFY_*, OPENAI_API_KEY, APP_PASSWORD in
docker compose -f docker-compose.dev.yml up --build
# UI op http://localhost:9090 ; koppel Spotify via /login
```

Zonder Docker: `pip install -r requirements-dev.txt`, `DATABASE_URL=sqlite:///dev.db python app/web.py`.

Tests: `pytest -q` (geen externe diensten nodig).

## Instellingen (omgeving)

| Variabele | Verplicht | Betekenis |
| --- | --- | --- |
| `DATABASE_URL` | ja | PostgreSQL-URL (de compose-stack zet hem) |
| `SPOTIFY_CLIENT_ID`, `SPOTIFY_CLIENT_SECRET`, `SPOTIFY_REDIRECT_URI` | ja | Spotify-app; redirect = `https://<host>/callback` |
| `OPENAI_API_KEY` | ja | OpenAI |
| `APP_PASSWORD` | sterk aanbevolen | zonder dit staat de app open (gele balk in de UI) |
| `SECRET_KEY` | nee | sessie-sleutel; anders afgeleid van `APP_PASSWORD` |
| `ALERT_MAIL` | nee | adres voor alarm-mails; anders het eerste wissellijst-mailadres |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS`, `MAIL_FROM` | nee | mail na rotatie en alarm |
| `TZ` / `SCHEDULER_TZ` | nee | tijdzone van de rotatie-tijden (default Europe/Amsterdam) |
| `OPENAI_MODEL`, `OPENAI_SCORE_MODEL` | nee | gpt-4o / gpt-4o-mini |
| `INSTAGRAM_AI_AFWERKING`, `INSTAGRAM_AFWERKING_QUALITY`, `INSTAGRAM_AFWERKING_POGINGEN`, `OPENAI_IMAGE_INPUT_FIDELITY`, `OPENAI_CONTROLE_MODEL` | nee | AI-afwerking van de plaatjes met tekstcontrole: 1 / high / 2 / high / gpt-4o (0 = alleen het strakke plaatje) |
| `OPENAI_IMAGE_MODEL`, `OPENAI_IMAGE_QUALITY`, `INSTAGRAM_AI_ILLUSTRATIE` | nee | Instagram-plaatjes: gpt-image-1 / medium / 1 (0 = zelf getekende achtergrond, geen beeldkosten) |
| `SUGGESTIES_PER_CATEGORIE`, `MAX_REASKS`, `BLOK_ACCEPT_RATIO`, `DISCOVERY_MAX_MAANDEN`, `DISCOVERY_EXPIRE_DAGEN`, `POPULARITEIT_VAKKEN`, `RADIO_SCORE_MIN`, `RADIO_KANDIDATEN_PER_CATEGORIE` | nee | gedrag van de generatie, zie `.env.example` |

**Waar de secrets staan:** in de Portainer-stack (Stacks, wissellijst, Editor, Environment variables).
De deploy-workflow stuurt die ongewijzigd door; GitHub-secrets zijn alleen nodig voor Tailscale en
Portainer (`TS_OAUTH_CLIENT_ID`, `TS_OAUTH_SECRET`, `PORTAINER_URL`, `PORTAINER_API_TOKEN`,
`PORTAINER_STACK_ID`). Roteer je een Spotify- of OpenAI-key, dan dus in Portainer, en daarna
"Update the stack".

## Deploy

Push naar `main` → tests → Docker-image naar ghcr.io → GitHub Actions joint het tailnet →
Portainer haalt het image op en herstart de stack. Duurt ongeveer vier minuten; `/health` meldt
de versie. Pull requests draaien alleen de tests.

## Database

Het schema wordt door Alembic beheerd (`app/alembic/versions`). Bij de start draait de app
`upgrade head`; een database uit de tijd vóór Alembic wordt eenmalig op revisie 001 gestempeld.
Nieuwe migratie: een bestand in `versions/` met `down_revision` op de vorige, en het model in
`db/models.py` bijwerken.

Bij een eerste start met een lege database en V2-bestanden in `/app/data` (wissellijsten.json,
historie_*.txt) worden die eenmalig geïmporteerd; een oud `.cache`-bestand met het Spotify-token
wordt overgenomen in de tabel `spotify_token`.

## Bekende grenzen

- Eén gebruiker, één Spotify-account. De app is niet bedoeld als dienst voor anderen (Spotify's
  API-beleid voor nieuwe apps maakt dat ook lastig).
- De scheduler draait in het app-proces: een herstart van de container is een herstart van de
  scheduler. Rotaties die in dat venster vallen worden tot 5 minuten later ingehaald.
