# CLAUDE.md

Werkafspraken voor dit repo. Lees eerst `README.md` voor wat de app doet en hoe hij draait.

## Werkwijze

- Werk op een branch, open een PR naar `main`. Een merge naar `main` deployt direct naar de NAS.
- Draai `pytest -q` vóór elke push; de tests gebruiken SQLite en mocken Spotify en OpenAI.
- Houd PR's klein en beschrijf in de PR wat er na de merge nog handmatig moet (env-variabelen
  in Portainer, opnieuw koppelen, enzovoort).
- Schrijf code, logregels en UI-teksten in het Nederlands, zoals de rest van de app.

## Vaste regels in de code

- **Eén plek die roteert:** `scheduler._execute_rotation`. Ook handmatige rotaties lopen daar
  doorheen, zodat elke rotatie in de audit trail en het alarm zit.
- **Spotify-client altijd via `spotify_auth.get_spotify_client()`.** Het token staat in de
  database, nooit in een bestand. In `/callback` altijd `check_cache=False` (anders blokkeert een
  ingetrokken token het herkoppelen).
- **Schema alleen via Alembic.** Geen `create_all`. Nieuwe tabel of kolom = nieuwe revisie in
  `app/alembic/versions` plus het model.
- **Instellingen via de omgeving** (`config.py`), geen hardcoded modelnamen of drempels.
- **Alles behalve `/health` en `/auth/*` zit achter de login.** Nieuwe routes dus ook.
- **Fouten melden:** een mislukte rotatie geeft status `fout` of raist; `alerts.notify_failure`
  stuurt één mail per oorzaak. Nooit stil falen.

## Niet doen

- Geen secrets in code of in de repo (de repo is publiek). Secrets staan in Portainer.
- Geen tweede worker voor gunicorn: de scheduler zou dan dubbel draaien.
- Geen bestands-fallback terugbrengen; `db/migrate_data.py` is de enige lezer van V2-bestanden.
