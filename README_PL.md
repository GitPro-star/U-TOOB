# U-TOOB 5.0 — pierwsza wersja sieciowa

U-TOOB 5.0 przenosi projekt z pojedynczego komputera do aplikacji internetowej. W paczce znajduje się aplikacja Flask, szablony HTML, stylowanie, obsługa bazy PostgreSQL, plik konfiguracji Render i integracja z magazynem plików kompatybilnym z S3 (Cloudflare R2).

## Co zawiera wersja 5.0

- rejestrację i logowanie użytkowników;
- hasła zapisane w postaci skrótów PBKDF2, nie w zwykłym tekście;
- kanały użytkowników i edycję nazwy wyświetlanej oraz opisu;
- przesyłanie filmów, tytuły, opisy i odtwarzacz;
- wyszukiwanie, sortowanie po dacie i popularności;
- komentarze i polubienia;
- subskrypcje kanałów i kanał subskrypcji;
- SQLite do testowania lokalnego oraz PostgreSQL przez `DATABASE_URL` na hostingu;
- Cloudflare R2 do trwałego przechowywania filmów w sieci;
- ochronę formularzy CSRF i ciasteczka sesyjne HttpOnly/SameSite.

To pierwsza wersja MVP, nie klon całego YouTube. Nie ma jeszcze moderacji, zgłaszania treści, odzyskiwania hasła, panelu administratora, transkodowania ani transmisji na żywo. Przed otwarciem serwisu dla publiczności należy dodać limity logowania, moderację i kopie zapasowe.

## Lokalny test

Wymagany Python 3.10+.

```bash
python -m venv .venv
```

Windows:

```bat
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

Otwórz `http://127.0.0.1:5000`. W trybie lokalnym baza danych trafia do `instance/utoob.db`, a filmy do `instance/uploads/`. Tej wersji nie uruchamia się przez interpreter Pythona wbudowany w Inkscape — użyj zwykłego Pythona. (Na Render zależności instaluje automatycznie proces build.)

## Wdrożenie przez GitHub + Render

1. Rozpakuj folder `U-TOOB_5.0`.
2. Utwórz nowe repozytorium GitHub, np. `utoob-5`, i wgraj **zawartość** folderu (plik `render.yaml` musi być w katalogu głównym repozytorium).
3. W Render wybierz **New → Blueprint**, połącz konto GitHub i wskaż repozytorium `utoob-5`.
4. Render odczyta `render.yaml`, utworzy web service i bazę PostgreSQL. Ustaw zmienne Cloudflare R2 opisane niżej.
5. Po ukończeniu wdrożenia otwórz adres `https://...onrender.com`, utwórz konto i opublikuj film testowy.

W ustawieniach usługi polecenia są już zawarte w `render.yaml`:
- Build: `pip install -r requirements.txt`
- Start: `gunicorn app:app`
- Health check: `/healthz`

### Ważne o bazie Render

Plik `render.yaml` tworzy darmową bazę PostgreSQL do **testów**. Według aktualnej dokumentacji Render darmowe bazy wygasają po 30 dniach; po wygaśnięciu istnieje 14-dniowy okres na podniesienie planu, a później dane są usuwane. Przed prawdziwym użyciem podnieś bazę do płatnego planu albo skonfiguruj trwałą zewnętrzną bazę PostgreSQL. Nie przechowuj ważnych danych wyłącznie w darmowej bazie testowej.

Darmowy web service Render ma system plików efemeryczny — pliki zapisywane w systemie lokalnym mogą zniknąć po restarcie lub wdrożeniu. Dlatego wdrożona konfiguracja ustawia `STORAGE_BACKEND=r2` i wymaga osobnego magazynu plików. Dokumentacja: https://render.com/docs/free oraz https://render.com/docs/disks.

## Konfiguracja Cloudflare R2 dla filmów

1. W Cloudflare przejdź do R2 Object Storage i utwórz bucket, np. `utoob-videos`.
2. Utwórz R2 API token / klucze S3 z prawami do odczytu i zapisu obiektów w tym bucketcie.
3. Skopiuj **S3 endpoint** swojego konta (format `https://<ACCOUNT_ID>.r2.cloudflarestorage.com`), nazwę bucketu, Access Key ID i Secret Access Key.
4. W Render → usługa `utoob-5` → Environment dodaj wartości:
   - `R2_ENDPOINT_URL` — endpoint z Cloudflare;
   - `R2_ACCESS_KEY_ID` — identyfikator klucza;
   - `R2_SECRET_ACCESS_KEY` — sekret klucza;
   - `R2_BUCKET_NAME` — nazwa bucketu;
   - `R2_PUBLIC_BASE_URL` — opcjonalna baza URL dla publicznego odczytu; można zostawić pustą, a aplikacja generuje czasowe linki do odczytu.
5. W żadnym wypadku nie wklejaj tych sekretów do GitHub ani do publicznego kodu. Wpisuj je wyłącznie w Environment w Render.

Cloudflare R2 jest zgodny z API S3. Jeśli chcesz używać automatycznych miniatur z klatek filmów, skonfiguruj też regułę CORS bucketu dopuszczającą origin swojej domeny Render i metodę `GET`. Bez CORS samo odtwarzanie zwykle działa, ale przeglądarka może zablokować generowanie miniatur z klatek cross-origin.

## Co znajduje się w projekcie

```text
U-TOOB_5.0/
├── app.py
├── requirements.txt
├── render.yaml
├── README_PL.md
├── .gitignore
├── templates/
│   ├── base.html
│   ├── index.html
│   ├── auth.html
│   ├── account.html
│   ├── channel.html
│   ├── upload.html
│   └── watch.html
├── static/
│   ├── style.css
│   └── app.js
└── uploads/
```

## Ochrona danych

- Nie używaj hasła, którego używasz na innych kontach.
- `SECRET_KEY` musi zostać ustawiony jako sekret w Render; nie umieszczaj go w repozytorium.
- Zabezpiecz kopie zapasowe bazy i magazynu plików.
- Przed publikacją dla nieznanych użytkowników dodaj moderację, limity uploadów i ochronę przed nadużyciami.
