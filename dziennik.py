# -*- coding: utf-8 -*-
"""
Dziennik: co się faktycznie wydarzyło i co z tego wynika.

Cały system do tej pory zakładał, że plan jest wykonywany. Ten moduł zamyka pętlę:
zapisuje odhaczone punkty, przepracowane ciężary i wagę, a potem WYCIĄGA Z TEGO WNIOSKI —
podnosi ciężary, gdy seria wyszła, i koryguje kalorie, gdy waga stoi w miejscu.

Nie importuje trener.py (żeby nie robić importu w kółko) — dostaje dane w argumentach
albo czyta własne pliki.
"""
import os, json, io, datetime, re

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
STATE = os.path.join(BASE, "state")
os.makedirs(STATE, exist_ok=True)

ZDARZENIA_PATH = os.path.join(STATE, "zdarzenia.json")
CIEZARY_PATH = os.path.join(STATE, "ciezary.json")
HIST_PATH = os.path.join(STATE, "historia.json")


def _load(p, dom):
    try:
        with io.open(p, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return dom


def _save(p, o):
    with io.open(p, "w", encoding="utf-8") as f:
        json.dump(o, f, ensure_ascii=False, indent=2)


# ------------------------------------------------------------ log zdarzeń

def zdarzenia():
    return _load(ZDARZENIA_PATH, [])


def dodaj_zdarzenie(typ, data=None, **dane):
    """Dopisuje fakt. Log jest tylko dopisywany — nic się nie nadpisuje wstecz,
    dzięki czemu panel i cron mogą pisać do niego niezależnie."""
    z = zdarzenia()
    wpis = {"data": (data or datetime.date.today()).isoformat(), "typ": typ}
    wpis.update(dane)
    z.append(wpis)
    _save(ZDARZENIA_PATH, z[-500:])
    return wpis


def odhacz(punkt, zrobione=True, data=None):
    return dodaj_zdarzenie("punkt", data, punkt=punkt, zrobione=bool(zrobione))


def odhaczenia(data):
    """Ostatni stan każdego punktu danego dnia: {'13:30|Obiad': True}."""
    ds = data.isoformat() if hasattr(data, "isoformat") else str(data)
    out = {}
    for z in zdarzenia():
        if z["typ"] == "punkt" and z["data"] == ds:
            out[z["punkt"]] = z["zrobione"]
    return out


# ------------------------------------------------- dziennik treningowy

DOLNE_PARTIE = ("przysiad", "martwy", "wykrok", "wspi", "rumu")


def _dolna_partia(nazwa):
    n = nazwa.lower()
    return any(k in n for k in DOLNE_PARTIE)


def _zakres(powt):
    """'6-8' -> (6, 8). 'max', '45-60 s', '10 na nogę' -> None (nie progresujemy z tego)."""
    m = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", str(powt))
    return (int(m.group(1)), int(m.group(2))) if m else None


def ciezary():
    return _load(CIEZARY_PATH, {})


def propozycja(cwiczenie):
    """Ile brać dziś: ostatni ciężar plus ewentualna podwyżka wyliczona po poprzedniej sesji."""
    c = ciezary().get(cwiczenie)
    if not c:
        return None
    return {"ciezar": c.get("nastepny", c["ciezar"]), "poprzedni": c["ciezar"],
            "powt": c.get("powt", []), "data": c.get("data"),
            "podwyzka": c.get("nastepny", c["ciezar"]) - c["ciezar"]}


def zapisz_serie(cwiczenie, ciezar, powtorzenia, powt_cel=None, data=None, loguj=True):
    """Zapisuje przerobioną serię i od razu decyduje, co robić następnym razem.

    Progresja liniowa: jeśli KAŻDA seria trafiła w górny koniec zakresu, ciężar rośnie
    (góra +2,5 kg, dół +5 kg — dolne partie są silniejsze i mniejszy skok jest niemierzalny).
    Jeśli dwa razy z rzędu nie wyrobiłeś dolnego końca zakresu, schodzimy o 10%: na redukcji
    to normalne i lepiej cofnąć się o krok niż miesiącami mielić ciężar, którego nie udźwigniesz.
    """
    ciezar = float(ciezar)
    powtorzenia = [int(p) for p in powtorzenia]
    wszystkie = ciezary()
    stare = wszystkie.get(cwiczenie, {})
    zakres = _zakres(powt_cel) if powt_cel else None

    krok = 5.0 if _dolna_partia(cwiczenie) else 2.5
    nastepny, komentarz, nieudane = ciezar, "Zapisane.", stare.get("nieudane", 0)

    if zakres:
        dol, gora = zakres
        if powtorzenia and min(powtorzenia) >= gora:
            nastepny = ciezar + krok
            nieudane = 0
            komentarz = ("Wszystkie serie na górnym końcu zakresu — następnym razem %.1f kg."
                         % nastepny)
        elif powtorzenia and min(powtorzenia) < dol:
            nieudane += 1
            if nieudane >= 2:
                nastepny = round(ciezar * 0.9 / 2.5) * 2.5
                nieudane = 0
                komentarz = ("Drugi raz poniżej zakresu — schodzimy do %.1f kg i budujemy od nowa. "
                             "Na deficycie to normalne, nie cofnięcie się w rozwoju." % nastepny)
            else:
                komentarz = ("Poniżej zakresu, ale zostajemy przy %.1f kg. Jak powtórzy się "
                             "następnym razem, zejdziemy z ciężaru." % ciezar)
        else:
            komentarz = "W zakresie — zostajemy przy %.1f kg do czasu trafienia w górny koniec." % ciezar

    wszystkie[cwiczenie] = {"ciezar": ciezar, "powt": powtorzenia, "nastepny": nastepny,
                            "nieudane": nieudane,
                            "data": (data or datetime.date.today()).isoformat()}
    _save(CIEZARY_PATH, wszystkie)
    # loguj=False gdy zdarzenie juz jest w logu (przyszlo z panelu) - inaczej
    # kazda seria zapisana z telefonu dublowalaby sie w historii
    if loguj:
        dodaj_zdarzenie("seria", data, cwiczenie=cwiczenie, ciezar=ciezar, powt=powtorzenia)
    return {"nastepny": nastepny, "komentarz": komentarz, "krok": krok}


def treningi_zrobione(od, do):
    """Ile sesji faktycznie odbytych w przedziale dat (liczone po dniach, nie po seriach)."""
    dni = {z["data"] for z in zdarzenia()
           if z["typ"] == "seria" and od.isoformat() <= z["data"] <= do.isoformat()}
    return len(dni)


# ------------------------------------------- korekta kalorii z trendu wagi

def trend_wagi(dni=21):
    """Kilogramy na tydzień, liczone regresją z ostatnich tygodni.

    Pojedynczy pomiar nic nie znaczy — waga potrafi skoczyć o kilogram po słonym posiłku
    albo po nocce. Dlatego patrzymy na kierunek, a nie na ostatnią liczbę.
    """
    h = _load(HIST_PATH, {})
    wagi = sorted(h.get("waga", []), key=lambda w: w["data"])
    if len(wagi) < 3:
        return None
    dzis = datetime.date.today()
    ost = [w for w in wagi
           if (dzis - datetime.date.fromisoformat(w["data"])).days <= dni]
    if len(ost) < 3:
        ost = wagi[-3:]
    x0 = datetime.date.fromisoformat(ost[0]["data"])
    xs = [(datetime.date.fromisoformat(w["data"]) - x0).days for w in ost]
    ys = [float(w["kg"]) for w in ost]
    n = len(xs)
    sx, sy = sum(xs), sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    mian = n * sxx - sx * sx
    if mian == 0:
        return None
    nachylenie = (n * sxy - sx * sy) / mian          # kg na dzień
    return {"kg_tydzien": round(nachylenie * 7, 2), "pomiarow": n,
            "od": ost[0]["data"], "do": ost[-1]["data"],
            "waga_teraz": ys[-1]}


def korekta_kcal():
    """O ile skorygować kalorie względem wartości bazowej z config.json."""
    return int(_load(os.path.join(STATE, "kcal_korekta.json"), {"kcal": 0})["kcal"])


def przelicz_kalorie(cel_bazowy, zastosuj=False):
    """Porównuje realne tempo chudnięcia z zamierzonym i proponuje korektę.

    Widełki -0,4 do -0,7 kg/tydz. to kompromis: wolniej znaczy, że deficytu praktycznie
    nie ma, szybciej — że tracisz razem z tłuszczem mięśnie, co przy Twoim celu jest
    stratą, a nie sukcesem.
    """
    t = trend_wagi()
    korekta = korekta_kcal()
    wynik = {"trend": t, "korekta_teraz": korekta,
             "kcal_teraz": cel_bazowy + korekta, "zmiana": 0}
    if not t:
        wynik["ocena"] = ("Za mało pomiarów, żeby cokolwiek liczyć. Potrzebuję trzech ważeń "
                          "w ciągu trzech tygodni — ważysz się raz na cykl, więc to około tygodnia.")
        return wynik

    tempo = t["kg_tydzien"]
    if tempo > -0.3:
        zmiana = -150
        ocena = ("Chudniesz w tempie %.2f kg/tydz., czyli praktycznie stoisz. Ścinam %d kcal."
                 % (tempo, abs(zmiana)))
    elif tempo < -0.8:
        zmiana = 150
        ocena = ("Lecisz %.2f kg/tydz. — za szybko. Przy takim tempie oddajesz mięśnie razem "
                 "z tłuszczem, więc dokładam %d kcal." % (tempo, zmiana))
    else:
        zmiana = 0
        ocena = "Tempo %.2f kg/tydz. mieści się w widełkach. Nic nie ruszam." % tempo

    nowe = max(1800, cel_bazowy + korekta + zmiana)
    zmiana = nowe - (cel_bazowy + korekta)
    if nowe == 1800 and zmiana == 0:
        ocena += " Niżej nie zejdę — 1800 kcal to podłoga, poniżej której nie da się zjeść białka."
    wynik.update({"zmiana": zmiana, "kcal_po": nowe, "ocena": ocena})

    if zastosuj and zmiana:
        _save(os.path.join(STATE, "kcal_korekta.json"),
              {"kcal": korekta + zmiana, "data": datetime.date.today().isoformat(),
               "powod": ocena})
        dodaj_zdarzenie("kalorie", kcal=nowe, zmiana=zmiana, tempo=tempo)
    return wynik


# --------------------------------------------- przetwarzanie zdarzen z panelu

WSKAZNIK_PATH = os.path.join(STATE, "przetworzone.json")


def nieprzetworzone():
    """Zdarzenia, ktore doszly z telefonu i czekaja na zastosowanie."""
    i = _load(WSKAZNIK_PATH, {"do": 0})["do"]
    return i, zdarzenia()[i:]


def oznacz_przetworzone(ile):
    i = _load(WSKAZNIK_PATH, {"do": 0})["do"]
    _save(WSKAZNIK_PATH, {"do": i + ile,
                          "kiedy": datetime.datetime.now().isoformat(timespec="seconds")})


def zapisz_wage(kg, data=None):
    h = _load(HIST_PATH, {})
    ds = (data or datetime.date.today()).isoformat()
    h.setdefault("waga", [])
    h["waga"] = [w for w in h["waga"] if w["data"] != ds]
    h["waga"].append({"data": ds, "kg": float(kg)})
    h["waga"].sort(key=lambda w: w["data"])
    _save(HIST_PATH, h)
