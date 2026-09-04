# -*- coding: utf-8 -*-
"""
Trener AI v2 — planowanie dnia, diety i treningu pod grafik 24/48 na ochronie.

Założenia, które ten plik realizuje:
  * cykl trwa dokładnie 3 dni: ZMIANA (24h) -> PO ZMIANIE -> WOLNE,
  * zakupy i gotowanie odbywają się RAZ na cykl, w dniu po zmianie, po odespaniu,
  * jedno gotowanie = jedna "baza" na 3 obiady, reszta posiłków to składanki do 12 minut,
  * lodówka jest stanem trwałym, więc nie kupujemy drugi raz tego samego,
  * makra liczone są ze składników (data/makro.json) — to jedyne źródło prawdy,
  * pingi lecą na telefon przez ntfy.sh, odpalane cronem GitHub Actions (komputer może być wyłączony).

CLI:
  py trener.py dzis | plan | nowyplan | zakupy | kupione | gotowanie | trening
  py trener.py lodowka | waga 81.4 | kroki 6420 [--ping] | budzik 23:33
  py trener.py eksport | tick | auto | test
"""
import os, sys, json, math, random, datetime, urllib.request, urllib.error

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
STATE = os.path.join(BASE, "state")
DOCS = os.path.join(BASE, "docs")
for _d in (STATE, DOCS):
    os.makedirs(_d, exist_ok=True)


# ---------------------------------------------------------------- wczytywanie

def load(path, default=None):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        if default is None:
            raise
        return default


def save(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _load_dotenv():
    p = os.path.join(BASE, ".env")
    if not os.path.exists(p):
        return
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()

CFG = load(os.path.join(BASE, "config.json"))
PROD = load(os.path.join(DATA, "produkty.json"))
MAKRO = load(os.path.join(DATA, "makro.json"))
BAZY = load(os.path.join(DATA, "bases.json"))["bazy"]
QUICK = load(os.path.join(DATA, "quick.json"))["posilki"]
DNI = load(os.path.join(DATA, "dni.json"))
TRENINGI = load(os.path.join(DATA, "workouts.json"))
KROKI = load(os.path.join(DATA, "kroki.json"))
SUPLE = load(os.path.join(DATA, "suple.json"))
import dziennik

BAZA_BY_ID = {b["id"]: b for b in BAZY}
QUICK_BY_ID = {q["id"]: q for q in QUICK}
# Cel kaloryczny = wartosc bazowa z config.json plus korekta wyliczona z trendu wagi.
# Dzieki temu config zostaje punktem odniesienia, a system i tak dostraja sie do tego,
# co realnie pokazuje waga (patrz dziennik.przelicz_kalorie).
CEL = dict(CFG["makra"])
CEL["kcal"] += dziennik.korekta_kcal()
SLOTY = ("sniadanie", "drugi", "obiad", "kolacja")

PLAN_PATH = os.path.join(STATE, "plan.json")
PODMIANY_PATH = os.path.join(STATE, "podmiany.json")
FRIDGE_PATH = os.path.join(STATE, "fridge.json")
HIST_PATH = os.path.join(STATE, "historia.json")

# ile dni realnie wytrzyma produkt od dnia zakupu
TRWALOSC = {"swieze": 4, "chlodnia": 12, "trwale": 200}


# -------------------------------------------------------------------- czas

def teraz():
    """Lokalny czas w Polsce — także gdy skrypt leci na runnerze GitHuba w UTC."""
    try:
        from zoneinfo import ZoneInfo
        return datetime.datetime.now(ZoneInfo(CFG.get("strefa", "Europe/Warsaw")))
    except Exception:
        return datetime.datetime.now()


def dzis():
    return teraz().date()


def parse_date(s):
    return datetime.date.fromisoformat(s)


# ------------------------------------------------------------------ grafik

def typ_dnia(d=None):
    """0 = zmiana 24h, 1 = dzień po zmianie, 2 = dzień wolny."""
    d = d or dzis()
    kotwica = parse_date(CFG["grafik"]["kotwica"])
    return (d - kotwica).days % CFG["grafik"]["cykl_dni"]


DZIEN_GOTOWANIA = 1  # typ dnia, w którym robisz zakupy i gotujesz


def start_cyklu(d=None):
    """Data ostatniego dnia gotowania — czyli początek okna, które to gotowanie obsługuje.

    Okno celowo NIE zaczyna się w dniu zmiany. Gotujesz po zmianie, więc jedzenie
    z tego garnka jesz: dziś wieczorem, jutro (wolne) i pojutrze (następna zmiana).
    Gdyby okno startowało w dniu zmiany, lista zakupów obejmowałaby dzień, który
    już minął, i kupowałbyś jedzenie, które zjadłeś dwa dni wcześniej.
    """
    d = d or dzis()
    cofnij = (typ_dnia(d) - DZIEN_GOTOWANIA) % CFG["grafik"]["cykl_dni"]
    return d - datetime.timedelta(days=cofnij)


def nr_cyklu(d=None):
    """Kolejny numer gotowania, liczony od pierwszego dnia gotowania po kotwicy."""
    kotwica = parse_date(CFG["grafik"]["kotwica"]) + datetime.timedelta(days=DZIEN_GOTOWANIA)
    return (start_cyklu(d) - kotwica).days // CFG["grafik"]["cykl_dni"]


def opis_typu(t):
    return DNI["typy"][str(t)]


# ------------------------------------------------------------------- makra

def makra_produktow(produkty, dzielnik=1.0):
    t = {"kcal": 0.0, "bialko": 0.0, "tluszcz": 0.0, "wegle": 0.0}
    for klucz, ilosc in produkty:
        m = MAKRO.get(klucz)
        if not m:
            continue
        q = ilosc / dzielnik
        t["kcal"] += m[0] * q
        t["bialko"] += m[1] * q
        t["tluszcz"] += m[2] * q
        t["wegle"] += m[3] * q
    return {k: int(round(v)) for k, v in t.items()}


def makra_posilku(pid):
    """Działa i dla szybkiego posiłku, i dla porcji bazy."""
    if pid in QUICK_BY_ID:
        return makra_produktow(QUICK_BY_ID[pid]["produkty"])
    b = BAZA_BY_ID[pid]
    return makra_produktow(b["produkty"], dzielnik=b["porcje"])


def produkty_posilku(pid):
    if pid in QUICK_BY_ID:
        return [(k, q) for k, q in QUICK_BY_ID[pid]["produkty"]]
    b = BAZA_BY_ID[pid]
    return [(k, q / b["porcje"]) for k, q in b["produkty"]]


def nazwa_posilku(pid):
    if pid in QUICK_BY_ID:
        return QUICK_BY_ID[pid]["nazwa"]
    return BAZA_BY_ID[pid]["nazwa"]


def makra_dnia(dzien):
    t = {"kcal": 0, "bialko": 0, "tluszcz": 0, "wegle": 0}
    for slot in SLOTY:
        m = makra_posilku(dzien[slot])
        for k in t:
            t[k] += m[k]
    return t


# ------------------------------------------------------------------ planer

def _pule(box_only):
    """Kandydaci na każdy slot; na dniu zmiany tylko to, co da się zjeść na zimno z boxa."""
    out = {}
    for slot in ("sniadanie", "drugi", "kolacja"):
        out[slot] = [q["id"] for q in QUICK
                     if slot in q["sloty"] and (q["box"] if box_only else True)]
    return out


def _wybierz_baze(historia):
    """Baza, której dawno nie było — żeby nie jeść bolognese trzeci cykl z rzędu."""
    ostatnie = historia.get("bazy", [])
    def klucz(b):
        return ostatnie.index(b["id"]) if b["id"] in ostatnie else -1
    kandydaci = sorted(BAZY, key=klucz)[:4]
    return random.choice(kandydaci)


def _blad_dnia(m):
    return (abs(m["kcal"] - CEL["kcal"]) * 1.0
            + abs(m["bialko"] - CEL["bialko"]) * 6.0
            + abs(m["tluszcz"] - CEL["tluszcz"]) * 3.0
            + abs(m["wegle"] - CEL["wegle"]) * 1.0)


def generuj_plan(d=None, force=False):
    """Układa cały 3-dniowy cykl: jedna baza na obiady + składanki na resztę."""
    d = d or dzis()
    start = start_cyklu(d)
    stary = load(PLAN_PATH, {})
    if not force and stary.get("od") == start.isoformat():
        return stary

    historia = load(HIST_PATH, {"bazy": [], "waga": [], "treningi": []})
    random.seed(nr_cyklu(d) * 7919)
    baza = _wybierz_baze(historia)
    makra_bazy = makra_posilku(baza["id"])

    daty = [start + datetime.timedelta(days=i) for i in range(3)]
    pule = [_pule(box_only=(typ_dnia(dd) == 0)) for dd in daty]

    najlepszy, najlepszy_wynik = None, float("inf")
    for _ in range(20000):
        kandydat = []
        for i in range(3):
            kandydat.append({
                "sniadanie": random.choice(pule[i]["sniadanie"]),
                "drugi": random.choice(pule[i]["drugi"]),
                "kolacja": random.choice(pule[i]["kolacja"]),
            })
        wynik = 0.0
        uzyte = []
        klucze_produktow = []
        for i, dzien in enumerate(kandydat):
            m = dict(makra_bazy)
            for slot in ("sniadanie", "drugi", "kolacja"):
                mm = makra_posilku(dzien[slot])
                for k in m:
                    m[k] += mm[k]
                uzyte.append(dzien[slot])
                klucze_produktow += [k for k, _ in QUICK_BY_ID[dzien[slot]]["produkty"]]
            wynik += _blad_dnia(m)
        # kara za powtarzanie tego samego posiłku w cyklu
        wynik += 450 * (len(uzyte) - len(set(uzyte)))
        # premia za wspólne produkty — mniej pozycji na liście i mniej marnowania
        wynik -= 8 * (len(klucze_produktow) - len(set(klucze_produktow)))
        if wynik < najlepszy_wynik:
            najlepszy_wynik, najlepszy = wynik, kandydat

    dni = []
    for i, dd in enumerate(daty):
        dzien = dict(najlepszy[i])
        dzien["obiad"] = baza["id"]
        dzien["data"] = dd.isoformat()
        dzien["typ"] = typ_dnia(dd)
        dzien["makra"] = makra_dnia(dzien)
        dni.append(dzien)

    plan = {
        "cykl": nr_cyklu(d),
        "od": start.isoformat(),
        "do": daty[-1].isoformat(),
        "baza": baza["id"],
        "dni": dni,
        "wygenerowano": teraz().isoformat(timespec="seconds"),
    }
    save(PLAN_PATH, plan)

    historia["bazy"] = ([baza["id"]] + historia.get("bazy", []))[:6]
    save(HIST_PATH, historia)
    return plan


def podmiany(d=None):
    """Reczne zmiany posilkow na dany dzien - nadpisuja wylosowany plan."""
    d = d or dzis()
    return load(PODMIANY_PATH, {}).get(d.isoformat(), {})


def _zastosuj_podmiany(dzien, d):
    """Plan jest propozycja, nie wyrokiem. Jesli cos podmieniles, to ma zostac."""
    zmiany = podmiany(d)
    if not zmiany:
        return dzien
    dzien = dict(dzien)
    for slot, pid in zmiany.items():
        if slot in SLOTY:
            dzien[slot] = pid
    dzien["makra"] = makra_dnia(dzien)
    dzien["podmienione"] = list(zmiany)
    return dzien


def plan_dnia(d=None):
    d = d or dzis()
    plan = generuj_plan(d)
    for dzien in plan["dni"]:
        if dzien["data"] == d.isoformat():
            return plan, _zastosuj_podmiany(dzien, d)
    plan = generuj_plan(d, force=True)
    for dzien in plan["dni"]:
        if dzien["data"] == d.isoformat():
            return plan, _zastosuj_podmiany(dzien, d)
    return plan, plan["dni"][0]


def _zapisz_podmiane(slot, pid, d=None):
    d = d or dzis()
    w = load(PODMIANY_PATH, {})
    w.setdefault(d.isoformat(), {})[slot] = pid
    # trzymamy tylko biezacy tydzien, zeby plik nie puchl w nieskonczonosc
    granica = (d - datetime.timedelta(days=7)).isoformat()
    w = {k: v for k, v in w.items() if k >= granica}
    save(PODMIANY_PATH, w)
    dziennik.dodaj_zdarzenie("podmiana", d, slot=slot, na=pid)


def zamien_posilek(slot, d=None):
    """Podmienia jeden posilek na inny o zblizonych makrach.

    Nie losujemy czegokolwiek: szukamy dania najblizszego kalorycznie i bialkowo temu,
    ktore wypada, zeby podmiana nie rozwalila calego dnia. W dniu zmiany bierzemy
    wylacznie to, co da sie zjesc na zimno z boxa.
    """
    d = d or dzis()
    _, dzien = plan_dnia(d)
    obecny = dzien[slot]
    cel = makra_posilku(obecny)
    box_only = typ_dnia(d) == 0

    if slot == "obiad":
        kandydaci = [q["id"] for q in QUICK if q.get("bez_gotowania") and (q["box"] if box_only else True)]
        kandydaci += [b["id"] for b in BAZY if b["id"] != obecny]
    else:
        kandydaci = [q["id"] for q in QUICK
                     if slot in q.get("sloty", []) and (q["box"] if box_only else True)]
    uzyte = {dzien[s] for s in SLOTY}
    kandydaci = [k for k in kandydaci if k != obecny and k not in uzyte]
    if not kandydaci:
        return None

    def blad(pid):
        m = makra_posilku(pid)
        return abs(m["kcal"] - cel["kcal"]) + 8 * abs(m["bialko"] - cel["bialko"])

    kandydaci.sort(key=blad)
    wybrany = kandydaci[0]
    _zapisz_podmiane(slot, wybrany, d)
    return {"z": nazwa_posilku(obecny), "na": nazwa_posilku(wybrany), "id": wybrany,
            "makra_stare": cel, "makra_nowe": makra_posilku(wybrany)}


def bez_gotowania(d=None):
    """Dzis nie gotujesz - podmieniamy obiad na gotowca o tych samych makrach.

    Sedno: nie chodzi o to, zeby zjesc cokolwiek, tylko zeby dzien nadal sie zgadzal.
    Dlatego wybieramy pozycje najblizsza kaloriom i bialku porcji, ktora mialbys ugotowac.
    """
    d = d or dzis()
    _, dzien = plan_dnia(d)
    cel = makra_posilku(dzien["obiad"])
    box_only = typ_dnia(d) == 0
    kandydaci = [q["id"] for q in QUICK if q.get("bez_gotowania") and (q["box"] if box_only else True)]
    if not kandydaci:
        return None
    kandydaci.sort(key=lambda pid: (abs(makra_posilku(pid)["kcal"] - cel["kcal"])
                                    + 8 * abs(makra_posilku(pid)["bialko"] - cel["bialko"])))
    wybrany = kandydaci[0]
    _zapisz_podmiane("obiad", wybrany, d)
    m = makra_posilku(wybrany)
    return {"na": nazwa_posilku(wybrany), "id": wybrany, "makra": m, "zamiast": cel,
            "roznica_kcal": m["kcal"] - cel["kcal"], "roznica_b": m["bialko"] - cel["bialko"],
            "czas": QUICK_BY_ID[wybrany]["czas_min"],
            "produkty": [(PROD[k]["nazwa"], q, PROD[k]["jedn"]) for k, q in QUICK_BY_ID[wybrany]["produkty"]]}


# ----------------------------------------------------------------- lodówka

def wczytaj_lodowke():
    fr = load(FRIDGE_PATH, {"stan": {}, "log": []})
    dzisiaj = dzis().isoformat()
    przeterminowane = [k for k, v in fr["stan"].items() if v.get("do", "9999") < dzisiaj]
    for k in przeterminowane:
        fr["log"].append({"data": dzisiaj, "co": "wyrzucone", "produkt": k,
                          "ilosc": fr["stan"][k]["ilosc"]})
        del fr["stan"][k]
    if przeterminowane:
        save(FRIDGE_PATH, fr)
    return fr


def w_lodowce(klucz):
    return wczytaj_lodowke()["stan"].get(klucz, {}).get("ilosc", 0)


def dodaj_do_lodowki(klucz, ilosc, data=None):
    fr = wczytaj_lodowke()
    data = data or dzis()
    dni = TRWALOSC.get(PROD[klucz]["trw"], 7)
    do = (data + datetime.timedelta(days=dni)).isoformat()
    poz = fr["stan"].setdefault(klucz, {"ilosc": 0, "do": do})
    poz["ilosc"] = round(poz["ilosc"] + ilosc, 2)
    poz["do"] = min(poz["do"], do) if poz.get("do") else do
    save(FRIDGE_PATH, fr)


def zdejmij_z_lodowki(klucz, ilosc):
    fr = wczytaj_lodowke()
    if klucz in fr["stan"]:
        fr["stan"][klucz]["ilosc"] = round(fr["stan"][klucz]["ilosc"] - ilosc, 2)
        if fr["stan"][klucz]["ilosc"] <= 0.001:
            del fr["stan"][klucz]
        save(FRIDGE_PATH, fr)


# ------------------------------------------------------------------ zakupy

def potrzebne_na_cykl(plan):
    """Sumuje wszystkie składniki z 3 dni planu."""
    suma = {}
    for dzien in plan["dni"]:
        for slot in SLOTY:
            for k, q in produkty_posilku(dzien[slot]):
                suma[k] = suma.get(k, 0) + q
    return {k: round(v, 2) for k, v in sorted(suma.items())}


def _sciezka_zakupow(plan):
    return os.path.join(STATE, "zakupy-%s.json" % plan["od"])


def lista_zakupow(plan=None):
    """Kupujemy tylko brakującą różnicę, zaokrągloną w górę do całych opakowań.

    Po zaksięgowaniu cyklu lista jest ZAMROŻONA (czytana z migawki), bo inaczej
    po zakupach przeliczyłaby się na nowo od już opróżnionej lodówki i pokazała
    drugą listę na to samo. Migawka to jest ta lista, z którą faktycznie idziesz do sklepu.
    """
    plan = plan or generuj_plan()
    migawka = load(_sciezka_zakupow(plan), None) if os.path.exists(_sciezka_zakupow(plan)) else None
    if migawka:
        return migawka
    potrzeba = potrzebne_na_cykl(plan)
    stan = wczytaj_lodowke()["stan"]
    kup, mam, koszt = [], [], 0.0
    for klucz, ile in potrzeba.items():
        p = PROD[klucz]
        w_domu = stan.get(klucz, {}).get("ilosc", 0)
        brakuje = max(0.0, ile - w_domu)
        if brakuje <= 0.001:
            mam.append({"klucz": klucz, "nazwa": p["nazwa"], "potrzeba": ile,
                        "w_domu": w_domu, "jedn": p["jedn"]})
            continue
        opakowan = int(math.ceil(brakuje / p["opak"] - 1e-9))
        cena = round(opakowan * p["cena"], 2)
        koszt += cena
        kup.append({
            "klucz": klucz, "nazwa": p["nazwa"], "kat": p["kat"], "trw": p["trw"],
            "opakowan": opakowan, "opak": p["opak"], "jedn": p["jedn"],
            "brakuje": round(brakuje, 1), "dokupisz": opakowan * p["opak"],
            "cena": cena,
        })
    kolejnosc = {"mieso": 0, "nabial": 1, "warzywa": 2, "pieczywo": 3, "spizarnia": 4}
    kup.sort(key=lambda x: (kolejnosc.get(x["kat"], 9), x["nazwa"]))
    return {"kup": kup, "mam": mam, "koszt": round(koszt, 2), "zrobione": False}


def zaksieguj_cykl(force=False):
    """Rozliczenie całego cyklu jedną operacją, wykonywane w dniu zakupów.

    Dokłada do lodówki kupione OPAKOWANIA i od razu zdejmuje wszystko, co ten
    cykl zje. To, co zostaje (końcówki opakowań), przechodzi na następny cykl —
    i dlatego następna lista zakupów sama się skraca. Liczone raz na cykl.
    """
    plan = generuj_plan()
    znacznik = "cykl:" + plan["od"]
    fr = wczytaj_lodowke()
    if not force and any(w.get("co") == znacznik for w in fr["log"]):
        return None
    if force and os.path.exists(_sciezka_zakupow(plan)):
        os.remove(_sciezka_zakupow(plan))
    z = lista_zakupow(plan)
    z["zrobione"] = True
    save(_sciezka_zakupow(plan), z)   # zamrażamy listę, z którą idziesz do sklepu
    for poz in z["kup"]:
        dodaj_do_lodowki(poz["klucz"], poz["dokupisz"])
    for klucz, ile in potrzebne_na_cykl(plan).items():
        zdejmij_z_lodowki(klucz, ile)
    fr = wczytaj_lodowke()
    fr["log"].append({"data": dzis().isoformat(), "co": znacznik,
                      "pozycji": len(z["kup"]), "koszt": z["koszt"]})
    save(FRIDGE_PATH, fr)
    return z


def auto_rozlicz():
    """Wywoływane przez crona: w dniu gotowania, po zakupach, samo księguje cykl.

    Dzięki temu lodówka jest aktualna nawet gdy komputer w ogóle nie jest włączany.
    Jeśli w sklepie coś poszło inaczej, poprawiasz ręcznie: py trener.py kupione --force
    """
    n = teraz()
    if typ_dnia(n.date()) != DZIEN_GOTOWANIA:
        return None
    godzina_zakupow = CFG["gotowanie"]["godzina_zakupow"]
    if n.strftime("%H:%M") < godzina_zakupow:
        return None

    # KOLEJNOSC MA ZNACZENIE. Najpierw korygujemy kalorie wedlug tego, co pokazala
    # waga, potem przeliczamy plan na nowy cel, a dopiero na koncu ksiegujemy zakupy.
    # Odwrotnie kupilbys jedzenie pod nieaktualne zapotrzebowanie.
    k = dziennik.przelicz_kalorie(CFG["makra"]["kcal"], zastosuj=True)
    if k.get("zmiana"):
        CEL["kcal"] = k["kcal_po"]
        generuj_plan(force=True)
        tresc = k["ocena"] + "\n" + ("Nowy cel: %d kcal dziennie." % k["kcal_po"])
        wyslij_ping("⚖️ Korekta kalorii", tresc, priorytet=4)
    return zaksieguj_cykl()


# ------------------------------------------------------------------- kroki

def ocena_krokow(kroki, d=None, godzina=None):
    """Ile kroków, ile trzeba i co realnie z tym zrobić o tej porze dnia.

    Cel zależy od typu dnia. Po godzinie granicznej system przestaje wysyłać
    na spacer — o 22:00 nadrabianie kroków kosztuje sen, a sen na redukcji
    jest wart więcej niż dwa tysiące kroków.
    """
    d = d or dzis()
    kroki = int(kroki)
    t = str(typ_dnia(d))
    cel = KROKI["cele"][t]["cel"]
    brakuje = max(0, cel - kroki)
    godzina = godzina or teraz().strftime("%H:%M")
    pozno = godzina >= KROKI["godzina_pozno"]

    if kroki >= cel * KROKI["progi"]["blisko"]:
        status = "ok" if kroki >= cel else "blisko"
        sugestia = random.choice(KROKI["pochwaly"])
    else:
        status = "malo"
        if pozno:
            sugestia = random.choice(KROKI["sugestie_pozno"])
        else:
            sugestia = next(x["tekst"] for x in KROKI["sugestie"][t] if brakuje <= x["do"])

    return {"kroki": kroki, "cel": cel, "brakuje": brakuje, "status": status,
            "sugestia": sugestia, "komentarz": KROKI["cele"][t]["komentarz"],
            "procent": min(100, int(round(kroki / cel * 100)))}


def zapisz_kroki(ile, d=None, ping=False):
    """Przyjmuje liczbę kroków z iPhone'a (Skrót -> GitHub) i odsyła podpowiedź."""
    d = d or dzis()
    h = load(HIST_PATH, {"bazy": [], "waga": [], "treningi": [], "kroki": []})
    h.setdefault("kroki", [])
    h["kroki"] = [k for k in h["kroki"] if k["data"] != d.isoformat()]
    o = ocena_krokow(ile, d)
    h["kroki"].append({"data": d.isoformat(), "kroki": o["kroki"],
                       "cel": o["cel"], "status": o["status"]})
    h["kroki"].sort(key=lambda k: k["data"])
    h["kroki"] = h["kroki"][-60:]
    save(HIST_PATH, h)

    if ping:
        ikona = {"ok": "✅", "blisko": "🟢", "malo": "🚶"}[o["status"]]
        tytul = "%s Kroki: %s / %s" % (ikona, f"{o['kroki']:,}".replace(",", " "),
                                       f"{o['cel']:,}".replace(",", " "))
        wyslij_ping(tytul, o["sugestia"], priorytet=3 if o["status"] == "malo" else 2)
    return o


def kroki_dzis(d=None):
    d = d or dzis()
    h = load(HIST_PATH, {})
    for k in reversed(h.get("kroki", [])):
        if k["data"] == d.isoformat():
            return ocena_krokow(k["kroki"], d)
    return None


# ----------------------------------------------------------------- trening

def trening_dnia(d=None):
    d = d or dzis()
    t = typ_dnia(d)
    c = nr_cyklu(d)
    if t == 0:
        return {"rodzaj": "zmiana", "trening": TRENINGI["zmiana"]}
    if t == 1:
        return {"rodzaj": "krotki", "trening": TRENINGI["krotkie"][c % len(TRENINGI["krotkie"])]}
    return {"rodzaj": "glowny", "trening": TRENINGI["glowne"][c % len(TRENINGI["glowne"])]}


# ------------------------------------------------------------------ budzik

def _na_minuty(hhmm):
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def budzik_dla(czas_zdarzenia, d=None):
    """Na którą godzinę ma być budzik i ile to snu, licząc od chwili położenia się.

    Reguła: jeśli kładziesz się PRZED dzisiejszą pobudką (czyli po nocnej zmianie,
    rano), budzik jest jeszcze na dziś. Jeśli kładziesz się wieczorem — budzik
    jest na pobudkę dnia następnego, która w cyklu 24/48 za każdym razem jest inna.
    """
    d = d or dzis()
    dzis_pobudka = DNI["typy"][str(typ_dnia(d))]["pobudka"]
    if _na_minuty(czas_zdarzenia) < _na_minuty(dzis_pobudka):
        godzina, dzien_docelowy, jutro_li = dzis_pobudka, d, False
    else:
        jutro = d + datetime.timedelta(days=1)
        godzina, dzien_docelowy, jutro_li = DNI["typy"][str(typ_dnia(jutro))]["pobudka"], jutro, True
    minut = (_na_minuty(godzina) - _na_minuty(czas_zdarzenia)) % (24 * 60)
    return {"godzina": godzina, "minut": minut, "jutro": jutro_li,
            "ile": "%d h %02d min" % (minut // 60, minut % 60),
            "typ_docelowy": DNI["typy"][str(typ_dnia(dzien_docelowy))]["nazwa"]}


# ----------------------------------------------------------- kalkulator snu

def kalkulator_snu(o_ktorej=None, d=None):
    """Na którą ustawić budzik, gdy kładziesz się o nietypowej porze.

    Sen chodzi cyklami po ok. 90 minut i budzenie w środku cyklu daje uczucie
    rozbicia nawet po długim spaniu. Liczymy więc wielokrotności cyklu od chwili
    ZAŚNIĘCIA, nie położenia się — stąd doliczany czas zasypiania.

    Najważniejsze: kalkulator zna grafik. Jeśli budzisz się w dzień zmiany,
    05:30 jest nienegocjowalne i wtedy nie proponujemy sześciu cykli, tylko
    mówimy wprost, ile snu z tego wyjdzie i czy nie kłaść się od razu.
    """
    d = d or dzis()
    cfg = CFG["sen"]
    n = teraz()
    if o_ktorej:
        hh, mm = o_ktorej.split(":")
        polozenie = int(hh) * 60 + int(mm)
    else:
        polozenie = n.hour * 60 + n.minute

    # po dobie służby zasypia się od razu, w normalny wieczór trwa to dłużej
    zmeczony = typ_dnia(d) == 1
    zasypianie = cfg["zasypianie_zmeczony_min"] if zmeczony else cfg["zasypianie_min"]
    zasniecie = polozenie + zasypianie

    # dzień, na który wypada pobudka: jeśli kładziesz się nad ranem, to jeszcze dziś
    doba = 24 * 60
    def dzien_pobudki(minuta):
        return d if minuta < doba else d + datetime.timedelta(days=1)

    opcje = []
    for cykli in range(3, 7):
        m = zasniecie + cykli * cfg["cykl_min"]
        dp = dzien_pobudki(m)
        opcje.append({
            "cykli": cykli,
            "godzina": "%02d:%02d" % ((m % doba) // 60, m % 60),
            "snu_min": m - polozenie,
            "snu": "%d h %02d min" % ((m - polozenie) // 60, (m - polozenie) % 60),
            "jutro": dp != d,
            "typ_dnia": typ_dnia(dp),
        })

    # twarde ograniczenie: jeśli budzisz się w dzień zmiany, pobudka jest ustalona
    dzien_docelowy = dzien_pobudki(zasniecie + 4 * cfg["cykl_min"])
    typ_doc = typ_dnia(dzien_docelowy)
    pobudka_plan = DNI["typy"][str(typ_doc)]["pobudka"]
    sztywna = typ_doc == 0

    # na sluzbie nocna przerwa to drzemki z planu, a nie sen - nie doradzamy tu cykli
    na_sluzbie = typ_dnia(d) == 0 and (polozenie >= _na_minuty("21:00") or polozenie < _na_minuty("06:00"))

    wynik = {"na_sluzbie": na_sluzbie, "polozenie": "%02d:%02d" % (polozenie // 60 % 24, polozenie % 60),
             "zasypianie": zasypianie, "opcje": opcje,
             "typ_docelowy": DNI["typy"][str(typ_doc)]["nazwa"],
             "pobudka_planowa": pobudka_plan, "sztywna": sztywna}

    if na_sluzbie:
        wynik["zalecenie"] = "-"
        wynik["snu_zalecane"] = "-"
        wynik["cykli_zalecane"] = 0
        wynik["uwagi"] = []
        wynik["ocena"] = ("Jesteś na służbie. Tu nie planujemy snu cyklami, tylko trzymamy się czterech "
                          "drzemek z planu: 23:00, 00:40, 02:20 i 04:00, każda po 1 h 20. "
                          "Kalkulator przyda się dopiero w domu.")
        return wynik

    # Sztywna pobudka ma sens tylko, gdy naprawde kladziesz sie teraz. Gdy do niej
    # zostalo wiecej niz 11 h, wpisana godzina nie jest pora snu (np. popoludnie)
    # i podawanie "14 h snu" byloby bzdura - wtedy wracamy do liczenia cyklami.
    if sztywna:
        pm = _na_minuty(pobudka_plan)
        if pm < polozenie % doba:
            pm += doba
        snu = pm - polozenie % doba
        if snu > 11 * 60:
            sztywna = False
            wynik["sztywna"] = False
            wynik["za_daleko"] = "%d h %02d min" % (snu // 60, snu % 60)

    if sztywna:
        pelnych = max(0, (snu - zasypianie) // cfg["cykl_min"])
        wynik["zalecenie"] = pobudka_plan
        wynik["snu_zalecane"] = "%d h %02d min" % (snu // 60, snu % 60)
        wynik["cykli_zalecane"] = int(pelnych)
        if pelnych >= 5:
            wynik["ocena"] = "Spokojnie zdążysz się wyspać."
        elif pelnych == 4:
            wynik["ocena"] = "Cztery pełne cykle — akceptowalnie jak na dzień przed zmianą."
        elif pelnych == 3:
            wynik["ocena"] = "Trzy cykle. Da się przeżyć, ale na służbie będzie ciężko — kładź się teraz, bez zwłoki."
        else:
            wynik["ocena"] = ("Mniej niż trzy cykle. Dziś już nie nadrobisz — prześpij, ile się da, "
                              "i licz na drzemki na służbie.")
    else:
        cel = [o for o in opcje if o["cykli"] in cfg["cykle_dobre"]]
        wybor = cel[-1] if cel else opcje[-1]
        wynik["zalecenie"] = wybor["godzina"]
        wynik["snu_zalecane"] = wybor["snu"]
        wynik["cykli_zalecane"] = wybor["cykli"]
        wynik["ocena"] = "Bez sztywnej pobudki — bierz sześć cykli, jeśli możesz, pięć jeśli nie."

    # Sam cykl snu to za mało. Późna pobudka potrafi rozwalić plan dnia albo noc
    # przed zmianą, i to jest ważniejsze niż trafienie w równą wielokrotność.
    uwagi = []
    if wynik.get("za_daleko"):
        uwagi.append("Do najbliższej sztywnej pobudki (%s) zostało %s, czyli więcej niż noc. "
                     "Zakładam, że nie kładziesz się właśnie teraz — poniżej masz zwykłe cykle."
                     % (pobudka_plan, wynik["za_daleko"]))
    # Porownanie z planowa pobudka ma sens tylko dla realnej porannej pobudki.
    # Przy roznicy ponad 8 h porownujemy pory z roznych czesci doby i wychodza bzdury.
    if wynik["zalecenie"] != "-" and not wynik.get("za_daleko"):
        dzien_b = dzien_pobudki(zasniecie + wynik["cykli_zalecane"] * cfg["cykl_min"])
        typ_b = typ_dnia(dzien_b)
        plan_pobudka = DNI["typy"][str(typ_b)]["pobudka"]
        spoznienie = _na_minuty(wynik["zalecenie"]) - _na_minuty(plan_pobudka)
        if 60 < spoznienie <= 8 * 60:
            trening = next((e["czas"] for e in DNI["plan"][str(typ_b)] if "TRENING" in e["tytul"]), None)
            u = "Plan tego dnia zakłada pobudkę %s" % plan_pobudka
            if trening:
                u += " i trening o %s" % trening
            u += ", więc budząc się o %s przesuwasz wszystko o %d h %02d min." % (
                wynik["zalecenie"], spoznienie // 60, spoznienie % 60)
            uwagi.append(u)
            if typ_dnia(dzien_b + datetime.timedelta(days=1)) == 0:
                uwagi.append("Nazajutrz ZMIANA z pobudką %s. Po tak późnym wstaniu nie zaśniesz o 22:00 — "
                             "licz się z krótką nocą przed służbą." % DNI["typy"]["0"]["pobudka"])
    wynik["uwagi"] = uwagi
    return wynik


# ------------------------------------------------------------------ agenda

def agenda(d=None):
    """Plan dnia A-Z. Doklejamy nocne punkty ze zmiany, która trwa jeszcze nad ranem."""
    d = d or dzis()
    t = typ_dnia(d)
    zdarzenia = [dict(e) for e in DNI["plan"][str(t)] if not e.get("nastepny_dzien")]
    if t == 1:
        zdarzenia += [dict(e) for e in DNI["plan"]["0"] if e.get("nastepny_dzien")]
    _, dzien = plan_dnia(d)
    for e in zdarzenia:
        if e.get("akcja") == "budzik":
            e["budzik"] = budzik_dla(e["czas"], d)
        if e.get("slot"):
            pid = dzien[e["slot"]]
            m = makra_posilku(pid)
            e["posilek"] = nazwa_posilku(pid)
            e["posilek_id"] = pid
            e["makra"] = m
    zdarzenia.sort(key=lambda e: e["czas"])
    return zdarzenia


# --------------------------------------------------------------- pingi ntfy

def wyslij_ping(tytul, tresc, tagi=None, priorytet=3):
    topic = os.environ.get(CFG["ntfy"]["topic_env"], "").strip()
    if not topic:
        print("[ntfy] brak NTFY_TOPIC — pomijam wysyłkę")
        return False
    payload = {
        "topic": topic,
        "title": tytul,
        "message": tresc,
        "priority": priorytet,
        "tags": tagi or [],
    }
    if CFG.get("panel_url"):
        payload["click"] = CFG["panel_url"]
    req = urllib.request.Request(
        CFG["ntfy"]["server"],
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status < 300
    except urllib.error.URLError as e:
        print("[ntfy] błąd wysyłki:", e)
        return False


def tresc_pinga(e, d):
    czesci = []
    if e.get("posilek"):
        m = e["makra"]
        czesci.append("%s  (%d kcal, %d g B)" % (e["posilek"], m["kcal"], m["bialko"]))
    if e.get("opis"):
        czesci.append(e["opis"])
    if e.get("akcja") == "gotowanie":
        b = BAZA_BY_ID[generuj_plan(d)["baza"]]
        czesci.insert(0, "Dziś gotujesz: %s — %d min, %s." % (b["nazwa"], b["czas_min"], b["naczynia"]))
    if e.get("akcja") == "podsumowanie":
        czesci.insert(0, tekst_podsumowania(podsumowanie_cyklu(d)))
    if e.get("akcja") == "suple":
        czesci.append("Suple na teraz: " + ", ".join("%s (%s)" % (x["nazwa"], x["ile"])
                                                     for x in SUPLE["lista"]))
    if e.get("akcja") == "zakupy":
        z = lista_zakupow()
        czesci.insert(0, "%d pozycji, ok. %.2f zł. %d rzeczy już masz w lodówce."
                      % (len(z["kup"]), z["koszt"], len(z["mam"])))
    if e.get("akcja") == "budzik":
        b = budzik_dla(e["czas"], d)
        tekst = "Ustaw budzik na %s — to %s snu." % (b["godzina"], b["ile"])
        if b["jutro"]:
            tekst += " Jutro: %s." % b["typ_docelowy"]
        czesci.insert(0, tekst)
    if e.get("akcja") in ("trening_krotki", "trening_glowny"):
        t = trening_dnia(d)["trening"]
        czesci.insert(0, "%s — %d min." % (t["nazwa"], t["czas_min"]))
    return "\n".join(czesci)


def tick(okno_min=25, sucho=False):
    """Odpalane cronem co 10 min. Okno 25 min z zapasem, bo cron GitHuba potrafi się spóźnić;
    przed dublami chroni plik state/wyslane-DATA.json, a nie wąskie okno."""
    n = teraz()
    d = n.date()
    wyslane_path = os.path.join(STATE, "wyslane-%s.json" % d.isoformat())
    wyslane = load(wyslane_path, [])
    poszlo = []
    for e in agenda(d):
        if not e.get("ping"):
            continue
        klucz = e["czas"] + "|" + e["tytul"]
        if klucz in wyslane:
            continue
        hh, mm = e["czas"].split(":")
        moment = n.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
        minelo = (n - moment).total_seconds() / 60.0
        if 0 <= minelo <= okno_min:
            tytul = "%s %s" % (e.get("ikona", "•"), e["tytul"])
            tresc = tresc_pinga(e, d)
            if sucho:
                print("[SUCHY BIEG]", tytul, "|", tresc.replace("\n", " / "))
                poszlo.append(klucz)
            elif wyslij_ping(tytul, tresc, tagi=[], priorytet=4 if e.get("akcja") else 3):
                wyslane.append(klucz)
                poszlo.append(klucz)
    if poszlo and not sucho:
        save(wyslane_path, wyslane)
    return poszlo


def podsumowanie_cyklu(d=None):
    """Co realnie wyszlo z ostatnich trzech dni - liczby, nie wrazenia."""
    d = d or dzis()
    start = start_cyklu(d)
    koniec = start + datetime.timedelta(days=2)
    plan = generuj_plan(d)
    t = dziennik.trend_wagi()
    h = load(HIST_PATH, {})
    kroki = [k for k in h.get("kroki", []) if start.isoformat() <= k["data"] <= koniec.isoformat()]
    srednie_kroki = int(sum(k["kroki"] for k in kroki) / len(kroki)) if kroki else None
    zrobione = sum(1 for v in dziennik.odhaczenia(d).values() if v)
    return {
        "od": start.isoformat(), "do": koniec.isoformat(),
        "baza": BAZA_BY_ID[plan["baza"]]["nazwa"],
        "treningi": dziennik.treningi_zrobione(start, koniec),
        "treningi_plan": 2,
        "kroki_srednio": srednie_kroki,
        "waga": t,
        "kalorie": dziennik.przelicz_kalorie(CFG["makra"]["kcal"]),
        "odhaczone_dzis": zrobione,
    }


def tekst_podsumowania(p):
    w = []
    w.append("Cykl %s → %s. Gotowałeś: %s." % (p["od"], p["do"], p["baza"]))
    w.append("Treningi: %d z %d." % (p["treningi"], p["treningi_plan"]))
    if p["kroki_srednio"]:
        w.append("Kroki średnio: %s dziennie." % f"{p['kroki_srednio']:,}".replace(",", " "))
    if p["waga"]:
        w.append("Waga: %.1f kg, tempo %+.2f kg/tydz." % (p["waga"]["waga_teraz"], p["waga"]["kg_tydzien"]))
    w.append(p["kalorie"]["ocena"])
    return "\n".join(w)


# ------------------------------------------------------------------ eksport

def eksport():
    """Zrzuca wszystko, czego potrzebuje panel WWW, do jednego pliku."""
    d = dzis()
    plan = generuj_plan(d)
    baza = BAZA_BY_ID[plan["baza"]]
    dane = {
        "wygenerowano": teraz().isoformat(timespec="seconds"),
        "dzis": d.isoformat(),
        "typ_dzis": typ_dnia(d),
        "typy": DNI["typy"],
        "cel": CEL,
        "user": CFG["user"],
        "plan": plan,
        "baza": baza,
        "agenda": agenda(d),
        "zakupy": lista_zakupow(plan),
        "lodowka": wczytaj_lodowke()["stan"],
        "trening": trening_dnia(d),
        "nawyki": DNI["nawyki"],
        "posilki": {p["id"]: p for p in QUICK},
        "produkty": {k: v for k, v in PROD.items() if not k.startswith("_")},
        "panel_wersja": CFG.get("panel_wersja", 1),
        "suple": SUPLE,
        "odhaczone": dziennik.odhaczenia(d),
        "podmiany": podmiany(d),
        "ciezary": dziennik.ciezary(),
        "kalorie": dziennik.przelicz_kalorie(CFG["makra"]["kcal"]),
        "cel_bazowy": CFG["makra"]["kcal"],
        "sen": CFG["sen"],
        # skrot potrzebny kalkulatorowi snu w panelu: pobudka i godzina treningu per typ dnia
        "pobudki": {t: {"nazwa": DNI["typy"][t]["nazwa"], "pobudka": DNI["typy"][t]["pobudka"],
                        "trening": next((e["czas"] for e in DNI["plan"][t] if "TRENING" in e["tytul"]), None)}
                    for t in ("0", "1", "2")},
        "historia": load(HIST_PATH, {"bazy": [], "waga": [], "treningi": [], "kroki": []}),
        "kroki": kroki_dzis(d),
        "kroki_cel": KROKI["cele"][str(typ_dnia(d))],
    }
    for dzien in dane["plan"]["dni"]:
        dzien["nazwy"] = {s: nazwa_posilku(dzien[s]) for s in SLOTY}

    # Cron leci co 10 minut. Gdyby zapisywał plik zawsze, sam znacznik czasu
    # generowałby commit przy każdym uruchomieniu — 144 dziennie i repo nie do
    # czytania. Zapisujemy tylko, gdy zmieniła się TREŚĆ.
    sciezka = os.path.join(DOCS, "dane.json")
    stare = load(sciezka, None) if os.path.exists(sciezka) else None
    if stare is not None:
        a = dict(stare); b = dict(dane)
        a.pop("wygenerowano", None); b.pop("wygenerowano", None)
        if json.dumps(a, sort_keys=True, ensure_ascii=False) == json.dumps(b, sort_keys=True, ensure_ascii=False):
            return stare
    save(sciezka, dane)
    return dane


# ---------------------------------------------------------------- wypisanie

def _kreska(t=""):
    print("\n" + t)
    print("─" * 58)


def pokaz_dzis(d=None):
    d = d or dzis()
    t = typ_dnia(d)
    info = opis_typu(t)
    _kreska("%s  %s — %s" % (info["emoji"], d.strftime("%A %d.%m"), info["nazwa"]))
    print(info["opis"])
    _, dzien = plan_dnia(d)
    m = dzien["makra"]
    print("\nMakra dnia: %d kcal  •  B %d g  •  T %d g  •  W %d g   (cel: %d / %d / %d / %d)"
          % (m["kcal"], m["bialko"], m["tluszcz"], m["wegle"],
             CEL["kcal"], CEL["bialko"], CEL["tluszcz"], CEL["wegle"]))
    _kreska("PLAN DNIA")
    for e in agenda(d):
        linia = "%s  %s %s" % (e["czas"], e.get("ikona", "•"), e["tytul"])
        if e.get("posilek"):
            linia += " → %s (%d kcal)" % (e["posilek"], e["makra"]["kcal"])
        print(linia)
        if e.get("opis"):
            print("        " + e["opis"])


def pokaz_plan():
    plan = generuj_plan()
    b = BAZA_BY_ID[plan["baza"]]
    _kreska("CYKL %d:  %s → %s" % (plan["cykl"], plan["od"], plan["do"]))
    print("Gotujesz raz: %s (%d porcje, %d min, %s)" % (b["nazwa"], b["porcje"], b["czas_min"], b["naczynia"]))
    for dzien in plan["dni"]:
        info = opis_typu(dzien["typ"])
        m = dzien["makra"]
        print("\n%s %s — %s   [%d kcal, B %d]" % (info["emoji"], dzien["data"], info["nazwa"], m["kcal"], m["bialko"]))
        for slot in SLOTY:
            print("   %-11s %s" % (slot + ":", nazwa_posilku(dzien[slot])))


def pokaz_zakupy():
    z = lista_zakupow()
    _kreska("ZAKUPY NA 3 DNI — ok. %.2f zł" % z["koszt"])
    ost = None
    for poz in z["kup"]:
        if poz["kat"] != ost:
            ost = poz["kat"]
            print("\n  " + {"mieso": "MIĘSO", "nabial": "NABIAŁ I JAJA", "warzywa": "WARZYWA I OWOCE",
                            "pieczywo": "PIECZYWO I MAKARONY", "spizarnia": "SPIŻARNIA"}.get(ost, ost.upper()))
        print("   □ %-34s %d × %g %s   %5.2f zł"
              % (poz["nazwa"], poz["opakowan"], poz["opak"], poz["jedn"], poz["cena"]))
    if z["mam"]:
        _kreska("JUŻ MASZ — NIE KUPUJ")
        for poz in z["mam"]:
            print("   ✓ %-34s w domu %g %s" % (poz["nazwa"], poz["w_domu"], poz["jedn"]))


def pokaz_gotowanie():
    plan = generuj_plan()
    b = BAZA_BY_ID[plan["baza"]]
    _kreska("GOTOWANIE: %s" % b["nazwa"])
    print("%d porcje • %d min łącznie, w tym %d min realnej roboty • %s"
          % (b["porcje"], b["czas_min"], b["czas_pracy"], b["naczynia"]))
    m = makra_posilku(b["id"])
    print("Jedna porcja: %d kcal, B %d g, T %d g, W %d g" % (m["kcal"], m["bialko"], m["tluszcz"], m["wegle"]))
    print("\nSKŁADNIKI NA CAŁY GARNEK")
    for k, q in b["produkty"]:
        print("   • %-32s %g %s" % (PROD[k]["nazwa"], q, PROD[k]["jedn"]))
    print("\nKROK PO KROKU")
    for i, krok in enumerate(b["kroki"], 1):
        print("   %d. %s" % (i, krok))
    print("\nPrzechowywanie: %s" % b["przechowywanie"])
    print("Odgrzewanie:    %s" % b["odgrzewanie"])


def pokaz_trening(d=None):
    t = trening_dnia(d)
    if t["rodzaj"] == "zmiana":
        w = t["trening"]
        _kreska(w["nazwa"])
        print(w["zasada"])
        print("\nMikro-rozruch co 2-3 h:")
        for x in w["mikro"]:
            print("   • " + x)
        return
    w = t["trening"]
    _kreska("%s — %d min" % (w["nazwa"], w["czas_min"]))
    for r in w["rozgrzewka"]:
        print("   ▸ " + r)
    print()
    for c in w["cwiczenia"]:
        print("   %-42s %s × %s   (przerwa %s)" % (c["nazwa"], c["serie"], c["powt"], c["przerwa"]))
        print("        " + c["wskazowka"])
    print("\n" + w["finisz"])


def pokaz_lodowke():
    fr = wczytaj_lodowke()
    _kreska("LODÓWKA I SPIŻARNIA")
    if not fr["stan"]:
        print("   (pusto — po pierwszych zakupach odpal: py trener.py kupione)")
        return
    for k, v in sorted(fr["stan"].items(), key=lambda x: PROD[x[0]]["kat"]):
        print("   %-34s %8g %-8s  do %s" % (PROD[k]["nazwa"], v["ilosc"], PROD[k]["jedn"], v.get("do", "?")))


def zapisz_wage(kg):
    h = load(HIST_PATH, {"bazy": [], "waga": [], "treningi": []})
    h["waga"] = [w for w in h.get("waga", []) if w["data"] != dzis().isoformat()]
    h["waga"].append({"data": dzis().isoformat(), "kg": float(kg)})
    h["waga"].sort(key=lambda w: w["data"])
    save(HIST_PATH, h)
    wagi = h["waga"]
    print("Zapisane: %.1f kg" % float(kg))
    if len(wagi) >= 2:
        delta = wagi[-1]["kg"] - wagi[0]["kg"]
        dni = (parse_date(wagi[-1]["data"]) - parse_date(wagi[0]["data"])).days or 1
        print("Od %s: %+.1f kg w %d dni (%+.2f kg/tydzień). Cel redukcji: -0,4 do -0,7 kg/tydzień."
              % (wagi[0]["data"], delta, dni, delta / dni * 7))


# --------------------------------------------------------------------- CLI

def main():
    cmd = (sys.argv[1] if len(sys.argv) > 1 else "dzis").lower()
    arg = sys.argv[2] if len(sys.argv) > 2 else None
    if cmd == "dzis":
        pokaz_dzis()
    elif cmd == "plan":
        pokaz_plan()
    elif cmd == "nowyplan":
        generuj_plan(force=True)
        pokaz_plan()
    elif cmd == "zakupy":
        pokaz_zakupy()
    elif cmd == "kupione":
        z = zaksieguj_cykl(force=(arg == "--force"))
        if z is None:
            print("Ten cykl był już rozliczony. Aby policzyć od nowa: py trener.py kupione --force")
        else:
            print("Zaksięgowano zakupy (%d pozycji, %.2f zł) i zużycie na 3 dni." % (len(z["kup"]), z["koszt"]))
        pokaz_lodowke()
    elif cmd == "gotowanie":
        pokaz_gotowanie()
    elif cmd == "trening":
        pokaz_trening()
    elif cmd == "lodowka":
        pokaz_lodowke()
    elif cmd == "auto":
        z = auto_rozlicz()
        print("Zaksięgowano cykl (%.2f zł)." % z["koszt"] if z else "Nic do zaksięgowania.")
    elif cmd == "zamien":
        slot = (arg or "").lower()
        if slot not in SLOTY:
            print("Użycie: py trener.py zamien sniadanie|drugi|obiad|kolacja")
        else:
            z = zamien_posilek(slot)
            if not z:
                print("Brak sensownego zamiennika na ten slot.")
            else:
                print(slot.upper())
                print("  było: %s (%d kcal, B %d)"
                      % (z["z"], z["makra_stare"]["kcal"], z["makra_stare"]["bialko"]))
                print("  jest: %s (%d kcal, B %d)"
                      % (z["na"], z["makra_nowe"]["kcal"], z["makra_nowe"]["bialko"]))
    elif cmd == "niegotuje":
        g = bez_gotowania()
        if not g:
            print("Brak gotowca pasującego na dziś.")
        else:
            _kreska("DZIŚ BEZ GOTOWANIA")
            print("Zamiast obiadu: %s  (%d min roboty)" % (g["na"], g["czas"]))
            print("Makra: %d kcal, B %d  →  planowane było %d kcal, B %d  (różnica %+d kcal, %+d g białka)"
                  % (g["makra"]["kcal"], g["makra"]["bialko"], g["zamiast"]["kcal"],
                     g["zamiast"]["bialko"], g["roznica_kcal"], g["roznica_b"]))
            print()
            print("DOKUP:")
            for nazwa, ile, jedn in g["produkty"]:
                print("   □ %-34s %g %s" % (nazwa, ile, jedn))
    elif cmd == "serie":
        if len(sys.argv) < 5:
            print('Użycie: py trener.py serie "Wyciskanie sztangi leżąc" 60 8 8 7')
        else:
            cw, ciezar, powt = sys.argv[2], sys.argv[3], sys.argv[4:]
            # To samo cwiczenie wystepuje i w sesji glownej, i w krotkiej, ale zakres
            # powtorzen do progresji bierzemy z GLOWNEJ - to ona wyznacza postep,
            # krotka jest podtrzymujaca. Stad przerwanie na pierwszym trafieniu.
            zakres = None
            for grupa in ("glowne", "krotkie"):
                for t_ in TRENINGI[grupa]:
                    for c in t_["cwiczenia"]:
                        if c["nazwa"].lower() == cw.lower():
                            zakres = c["powt"]
                            break
                    if zakres:
                        break
                if zakres:
                    break
            w = dziennik.zapisz_serie(cw, ciezar, powt, zakres)
            print("%s: %s kg × %s" % (cw, ciezar, ", ".join(powt)))
            print(w["komentarz"])
    elif cmd == "zrobione":
        if not arg:
            print("Użycie: py trener.py zrobione \"13:30|Obiad\"")
        else:
            dziennik.odhacz(arg)
            print("Odhaczone: %s" % arg)
    elif cmd == "podsumowanie":
        p = podsumowanie_cyklu()
        _kreska("PODSUMOWANIE CYKLU")
        print(tekst_podsumowania(p))
    elif cmd == "kalorie":
        w = dziennik.przelicz_kalorie(CFG["makra"]["kcal"], zastosuj=("--zastosuj" in sys.argv))
        _kreska("KALORIE")
        print("Teraz: %d kcal (baza %d %+d)" % (w["kcal_teraz"], CFG["makra"]["kcal"], w["korekta_teraz"]))
        print(w["ocena"])
        if w.get("zmiana"):
            print("Po zmianie: %d kcal. Dodaj --zastosuj, żeby zapisać." % w["kcal_po"])
    elif cmd == "kroki":
        if not arg:
            o = kroki_dzis()
            if o:
                print("Dziś: %d / %d kroków (%d%%). %s" % (o["kroki"], o["cel"], o["procent"], o["sugestia"]))
            else:
                print("Brak zapisu kroków na dziś. Użycie: py trener.py kroki 6420")
        else:
            o = zapisz_kroki(arg, ping=("--ping" in sys.argv))
            print("%d / %d kroków (%d%%)" % (o["kroki"], o["cel"], o["procent"]))
            print(o["komentarz"])
            print("→ " + o["sugestia"])
    elif cmd == "budzik":
        w = kalkulator_snu(arg)
        _kreska("KŁADZIESZ SIĘ O %s" % w["polozenie"])
        print("Zasypianie liczone na %d min. Pobudka wypada w dzień: %s." % (w["zasypianie"], w["typ_docelowy"]))
        if w.get("na_sluzbie"):
            print()
            print(w["ocena"])
        elif w["sztywna"]:
            print()
            print("Jutro ZMIANA — pobudka %s jest sztywna, nie ma co wybierać." % w["pobudka_planowa"])
            print("Budzik: %s  →  %s snu, %d pełnych cykli." % (w["zalecenie"], w["snu_zalecane"], w["cykli_zalecane"]))
            print(w["ocena"])
        else:
            print()
            for o in w["opcje"]:
                znak = "◀ TO" if o["godzina"] == w["zalecenie"] else "  "
                print("   %s  %d cykle  →  %s snu   %s" % (o["godzina"], o["cykli"], o["snu"], znak))
            print()
            print(w["ocena"])
        for u in w.get("uwagi", []):
            print()
            print("UWAGA: " + u)
        print()
        print("Cykl to średnio 90 min, indywidualnie 80–110. Traktuj to jako przybliżenie.")
    elif cmd == "waga":
        if not arg:
            print("Użycie: py trener.py waga 81.4")
        else:
            zapisz_wage(arg.replace(",", "."))
    elif cmd == "eksport":
        d = eksport()
        print("Zapisano docs/dane.json (%d dni planu, %d pozycji zakupów)."
              % (len(d["plan"]["dni"]), len(d["zakupy"]["kup"])))
    elif cmd == "tick":
        poszlo = tick(sucho=(arg == "sucho"))
        print("Wysłano: %s" % (", ".join(poszlo) if poszlo else "nic (nie ma nic na teraz)"))
    elif cmd == "wdrozenie":
        # Odpalane po każdym pushu. Potwierdza, że cala droga
        # GitHub -> ntfy -> telefon jest drozna, i mowi, co przyjdzie nastepne.
        n = teraz()
        nast = [e for e in agenda() if e.get("ping") and e["czas"] > n.strftime("%H:%M")]
        info = opis_typu(typ_dnia())
        if nast:
            tresc = "Dzis: %s. Nastepny ping o %s - %s." % (info["nazwa"], nast[0]["czas"], nast[0]["tytul"])
        else:
            tresc = "Dzis: %s. Na dzis to juz wszystko, kolejne pingi jutro." % info["nazwa"]
        wyslij_ping("✅ Trener AI zaktualizowany", tresc, priorytet=3)
        print(tresc)
    elif cmd == "test":
        ok = wyslij_ping("🔔 Trener AI działa",
                         "Jeśli to widzisz na telefonie, powiadomienia są ustawione poprawnie.",
                         priorytet=4)
        print("Wysłane." if ok else "Nie wysłano — sprawdź NTFY_TOPIC.")
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
