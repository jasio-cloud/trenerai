# TRENER AI 💪 — pilnowanie makro + zakupy

Bot Discord (24/7) planuje posiłki pod Twoje makro, codziennie rano wypisuje wszystkie posiłki,
robi listę zakupów z orientacyjną ceną i dobiera dania tak, żeby produkty się nie marnowały.

## Cele
2300 kcal · 185 g białka · 70 g tłuszczu · 230 g węgli. Bez ryżu, ryb, owsianki, kasz. Proste przepisy.

## Komendy na Discordzie
| Komenda | Działanie |
|---|---|
| `!dzis` (`!menu`) | wszystkie dzisiejsze posiłki + suma makro |
| `!przepis obiad` | przepis krok po kroku |
| `!zmien obiad` | losuje inny posiłek na dziś |
| `!zakupy` | lista zakupów na cały plan + cena |
| `!plan` | posiłki na najbliższe dni |
| `!nowyplan` | generuje nowy plan od dziś |
| `!waga 81.5` | zapis wagi |
| `!progres` | ostatnie wpisy wagi |
| `!pomoc` | lista komend |

Posiłki: `sniadanie`, `przedtreningowy` (2. posiłek), `obiad`, `kolacja`.

## Jak to działa
- Plan tworzony jest na `plan_days` dni naprzód (domyślnie 7). Algorytm dobiera posiłki tak,
  by powtarzały się te same produkty → kupujesz np. 1,2 kg kurczaka i schodzi przez tydzień.
- Codziennie rano (lub na starcie zmiany) bot wypisuje pełny zestaw posiłków na dzień.
- `!zakupy` sumuje produkty z całego planu i podaje orientacyjną cenę (cennik: `data/produkty.json`).

## Pliki
| Plik | Co to |
|---|---|
| `bot.py` | bot Discord (główny, na serwer) |
| `core.py` | logika: planer, zakupy, harmonogram, przepisy |
| `coach.py` | CLI do testów |
| `config.json` | webhook, token, makra, daty zmian, `plan_days` |
| `data/meals.json` | 48 posiłków (przepisy + produkty) |
| `data/produkty.json` | cennik produktów |

## Co edytować
- Zmienić cenę produktu → `data/produkty.json`.
- Dodać posiłek → `data/meals.json` (pamiętaj o `produkty` i `kroki`).
- Dłuższy/krótszy plan → `plan_days` w `config.json`.

➡️ Postawienie bota na serwerze: `DEPLOY.md`.
