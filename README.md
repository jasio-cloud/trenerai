# Trener AI

Osobisty system planowania dnia, diety i treningu pod grafik **24/48 na ochronie**.
Działa w chmurze — **Twój komputer nie musi być włączony**, żeby przychodziły pingi.

## Co robi

- **Zna Twój grafik.** Cykl trwa dokładnie 3 dni: zmiana 24 h → dzień po zmianie → dzień wolny.
  System sam wie, który dziś jest, i układa pod to cały dzień od pobudki do snu.
- **Gotujesz raz na 3 dni.** Zawsze w dniu po zmianie, po odespaniu — jeden garnek, 3 pojemniki.
  Dzień zmiany jesz wyłącznie z boxów, więc nic nie trzeba odgrzewać ani gotować w pracy.
- **Zakupy raz na 3 dni**, z listą, która sama się skraca — bo system pamięta, co zostało w lodówce.
- **Pilnuje kalorii i białka** na redukcji. Makra liczone ze składników, nie z oka.
- **Plan treningowy** pod dom (Marbo Kelton): 2 sesje na cykl — krótsza po zmianie, główna w dniu wolnym.
- **Pinguje na telefon** o każdym punkcie planu: pobudka, posiłki, zakupy, gotowanie, trening, sen.

Bez kurczaka, ryżu, kasz i ryb. Białko stoi na wołowinie, mielonym, jajach i nabiale.

---

## Najpierw zobacz, jak działa (bez żadnej konfiguracji)

Otwórz terminal w tym katalogu i odpal:

```bash
py trener.py dzis
```

Zobaczysz cały dzisiejszy dzień od pobudki do snu, z posiłkami i makrami. Dalej:
`py trener.py plan`, `py trener.py zakupy`, `py trener.py gotowanie`, `py trener.py trening`.
Nic nie psujesz — to tylko czytanie. Dopiero poniższa konfiguracja sprawia,
że pingi zaczynają chodzić same, bez włączonego komputera.

## Uruchomienie (raz, ok. 15 minut)

### 1. Apka ntfy na iPhone

Zainstaluj **ntfy** z App Store (darmowa, bez konta). Wymyśl długi, losowy temat, np.
`trener-krystian-7f3k9x2p`. Temat działa jak hasło — kto go zna, może Ci wysyłać powiadomienia,
więc nie ma być zgadywalny. W apce: **+ → Subscribe to topic** → wpisz swój temat.

### 2. Repozytorium na GitHubie

Wrzuć ten katalog do repozytorium i ustaw je jako **publiczne**.

> Dlaczego publiczne: GitHub Actions dla repo publicznych są za darmo bez limitu minut.
> Repo prywatne ma 2000 minut miesięcznie, a ping co 10 minut zjada więcej.
> W repo nie ma sekretów — temat ntfy siedzi w sekretach GitHuba, nie w plikach.
> Jedyne dane osobiste, jakie tam trafią, to Twoja waga i plan posiłków. Jeśli wolisz,
> żeby waga nie była publiczna, dopisz `state/historia.json` do `.gitignore`.

```bash
git add -A
git commit -m "Trener AI v2"
git branch -M main
git remote add origin https://github.com/TWOJ-LOGIN/trener.git
git push -u origin main
```

### 3. Sekret z tematem ntfy

W repo: **Settings → Secrets and variables → Actions → New repository secret**
- Name: `NTFY_TOPIC`
- Secret: Twój temat, np. `trener-krystian-7f3k9x2p`

### 4. Panel jako apka na telefonie

W repo: **Settings → Pages → Source: Deploy from a branch → Branch: `main`, folder: `/docs`**.
Po chwili panel jest pod `https://TWOJ-LOGIN.github.io/trener/`.

Wpisz ten adres do `config.json` w polu `panel_url` i zrób commit — wtedy kliknięcie
w powiadomienie na telefonie otworzy panel od razu na dzisiejszym planie.

Na iPhonie: otwórz ten adres w **Safari → Udostępnij → Dodaj do ekranu początkowego**.
Od tej chwili wygląda i odpala się jak zwykła apka, z własną ikoną i bez paska przeglądarki.

### 5. Sprawdzenie

W repo: **Actions → „Trener AI — pingi na telefon" → Run workflow**, zaznacz `test` → **Run**.
Na telefon powinno przyjść powiadomienie „Trener AI działa". Jeśli przyszło — koniec konfiguracji.

### 6. Kroki z iPhone'a (opcjonalne, 5 minut)

iPhone i tak liczy kroki w apce Zdrowie. Skrót raz dziennie odczyta tę liczbę i wyśle do systemu,
a system odeśle konkretną podpowiedź — inną w dniu wolnym (cel 10 000), inną na 24-godzinnej
służbie (7 000), inną w dniu odsypiania (4 000). Po 21:30 przestaje wysyłać na spacer,
bo sen na redukcji jest wart więcej niż dwa tysiące kroków.

**Token dostępu.** GitHub → **Settings → Developer settings → Personal access tokens →
Fine-grained tokens → Generate new token**. Wybierz tylko repozytorium `trener`,
uprawnienie **Contents: Read and write**. Skopiuj token — pokaże się raz.

**Skrót.** Apka **Skróty → Automatyzacja → + → Pora dnia → 20:00 → Codziennie →
Uruchom natychmiast** (żeby nie pytał o potwierdzenie). Dodaj trzy akcje:

1. **Znajdź próbki zdrowia** — Typ: `Kroki`, filtr: `Data rozpoczęcia` jest `dzisiaj`
2. **Oblicz statystyki** — `Suma` z wyniku poprzedniej akcji
3. **Pobierz zawartość URL**
   - URL: `https://api.github.com/repos/TWOJ-LOGIN/trener/dispatches`
   - Metoda: `POST`
   - Nagłówki: `Authorization` = `Bearer TWOJ_TOKEN`, `Accept` = `application/vnd.github+json`
   - Treść żądania: `JSON`
     - `event_type` (Tekst) = `kroki`
     - `client_payload` (Słownik) → w środku `kroki` (Liczba) = wynik z akcji „Oblicz statystyki"

Test: uruchom skrót ręcznie. W ciągu minuty powinno przyjść powiadomienie z liczbą kroków
i podpowiedzią. Możesz też sprawdzić bez telefonu: **Actions → „Trener AI — kroki z iPhone'a"
→ Run workflow** i wpisz dowolną liczbę.

> Token siedzi na Twoim telefonie w Skrócie. Dlatego ma być **fine-grained** i ograniczony
> do tego jednego repo — nawet gdyby wyciekł, nie daje dostępu do niczego innego.

**Wersja bez tokena**, jeśli nie chcesz się w to bawić: te same trzy akcje, ale zamiast
„Pobierz zawartość URL" dajesz **Jeżeli** wynik jest mniejszy niż 10000 → **Pokaż
powiadomienie**. Działa w całości na telefonie, ale nie zna typu dnia i nie trafia do panelu.

---

## Codzienne używanie

Normalnie nie musisz robić nic — pingi przychodzą same, a panel sam się odświeża co 10 minut.

Z komputera (opcjonalnie, do podglądu i poprawek):

```bash
py trener.py dzis        # plan dnia od pobudki do snu
py trener.py plan        # cały 3-dniowy cykl z posiłkami i makrami
py trener.py zakupy      # lista zakupów (bez tego, co masz w lodówce)
py trener.py gotowanie   # przepis na dziś, krok po kroku
py trener.py trening     # dzisiejszy trening z ciężarami i wskazówkami
py trener.py lodowka     # co masz w domu i do kiedy jest dobre
py trener.py waga 81.4   # zapis wagi + tempo redukcji
py trener.py kroki 6420  # ręczny zapis kroków (normalnie robi to Skrót z iPhone'a)
py trener.py nowyplan    # przelosuj posiłki, jeśli plan Ci nie pasuje
py trener.py kupione     # ręczne zaksięgowanie zakupów (cron robi to sam o 15:30)
```

Po zmianie czegokolwiek lokalnie zrób `git push` — inaczej chmura o tym nie wie.

---

## Jak to jest zbudowane

| Plik | Za co odpowiada |
|---|---|
| `trener.py` | Cała logika: grafik, planer makro, lodówka, zakupy, pingi, eksport |
| `config.json` | Ty: waga, cel, makra, godziny zakupów i gotowania, kotwica grafiku |
| `data/produkty.json` | Cennik: co ile kosztuje i w jakich opakowaniach się to kupuje |
| `data/makro.json` | Makro na 1 jednostkę produktu — **jedyne** źródło prawdy o kaloriach |
| `data/bases.json` | Dania gotowane hurtem na 3 dni, z przepisem krok po kroku |
| `data/quick.json` | Posiłki do 12 minut; `box: true` = da się zjeść na zimno na zmianie |
| `data/workouts.json` | Treningi domowe: główne i krótkie po zmianie |
| `data/dni.json` | Szablony dnia A–Z dla trzech typów dnia + nawyki |
| `data/kroki.json` | Cele kroków na każdy typ dnia i podpowiedzi, gdy brakuje |
| `docs/` | Panel WWW (GitHub Pages) — to jest ta „apka" na telefonie |
| `state/` | Lodówka, aktualny plan, historia wagi, wysłane pingi |
| `legacy/` | Poprzednia wersja na Discorda, zostawiona na wszelki wypadek |

### Zmiana danych

Nie lubisz jakiegoś dania — usuń je z `data/quick.json` albo `data/bases.json`.
Chcesz dodać swoje — dopisz wpis w tym samym formacie; makra policzą się same ze składników,
pod warunkiem że każdy użyty produkt istnieje w `produkty.json` **i** w `makro.json`.
Zmieniły się ceny — poprawiasz `produkty.json` i cała lista zakupów przelicza się sama.

### Kotwica grafiku

W `config.json` → `grafik.kotwica` stoi data pewnego dnia zmiany (`2026-06-21`).
Cała reszta liczy się z niej: `(dzisiaj − kotwica) mod 3` daje typ dnia.
Jeśli kiedyś zmienisz się z kimś służbą i grafik się przesunie, poprawiasz tę jedną datę.

---

## Gdy coś nie działa

**Pingi przestały przychodzić.** GitHub wyłącza harmonogramy w repo, w którym nic się nie dzieje
przez 60 dni. Wejdź w zakładkę **Actions** i włącz workflow z powrotem, albo zrób dowolny commit.

**Ping przyszedł z opóźnieniem.** Cron GitHuba potrafi się spóźnić kilkanaście minut przy dużym
ruchu — to normalne i nie da się tego przyspieszyć. System ma z tego powodu 25-minutowe okno,
a przed dublami chroni lista wysłanych pingów w `state/`.

**Lista zakupów pokazuje rzeczy, których nie masz.** Lodówka rozjechała się z rzeczywistością.
Skasuj `state/fridge.json`, zrób `py trener.py kupione --force` po następnych zakupach i wypchnij zmiany.

**Plan Ci nie pasuje.** `py trener.py nowyplan` losuje cały cykl od nowa (baza + wszystkie posiłki).
