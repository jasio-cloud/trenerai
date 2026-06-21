# -*- coding: utf-8 -*-
"""
Wspólna logika Trenera AI (wersja: pilnowanie makro + zakupy).
- Planuje posiłki na kilka dni naprzód tak, by produkty się nie marnowały (te same składniki wracają).
- Liczy listę zakupów z orientacyjną ceną.
- Codziennie rano wypisuje wszystkie posiłki dnia.
Importowane przez bot.py (Discord 24/7) oraz coach.py (CLI/test).
"""
import os, json, random, datetime, urllib.request, math

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
STATE = os.path.join(BASE, "state")
PROGRESS = os.path.join(BASE, "progress")
for _d in (STATE, PROGRESS):
    os.makedirs(_d, exist_ok=True)

def _load_dotenv():
    """Wczytuje sekrety z pliku .env (token, webhook), jeśli istnieje. Bez zewnętrznych bibliotek."""
    p = os.path.join(BASE, ".env")
    if not os.path.exists(p):
        return
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
_load_dotenv()

SLOTS = ("sniadanie", "przedtreningowy", "obiad", "kolacja")
SLOT_LABELS = {
    "sniadanie": "ŚNIADANIE",
    "przedtreningowy": "DRUGI POSIŁEK",
    "obiad": "OBIAD",
    "kolacja": "KOLACJA",
}
SLOT_ALIASES = {
    "sniadanie": "sniadanie", "śniadanie": "sniadanie", "sniad": "sniadanie", "1": "sniadanie",
    "przedtreningowy": "przedtreningowy", "drugi": "przedtreningowy", "przekaska": "przedtreningowy", "2": "przedtreningowy",
    "obiad": "obiad", "o": "obiad", "lunch": "obiad", "3": "obiad",
    "kolacja": "kolacja", "kol": "kolacja", "k": "kolacja", "4": "kolacja",
}
KAT_ORDER = [
    ("mieso", "🥩 Mięso i wędliny"),
    ("nabial", "🥛 Nabiał i jaja"),
    ("pieczywo", "🍞 Pieczywo i makarony"),
    ("warzywa", "🥦 Warzywa i owoce"),
    ("spizarnia", "🛒 Spiżarnia"),
]

def load_json(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def save_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)

CFG = load_json(os.path.join(BASE, "config.json"))
MEALS = load_json(os.path.join(DATA, "meals.json"))
PROD = load_json(os.path.join(DATA, "produkty.json"))
MAKRO = load_json(os.path.join(DATA, "makro.json"))
MEAL_BY_ID = {m["id"]: m for slot in SLOTS for m in MEALS[slot]}

# sekrety: najpierw .env / zmienne środowiskowe, potem config.json (fallback)
WEBHOOK = os.environ.get("DISCORD_WEBHOOK_URL") or CFG.get("webhook_url", "")

def meal_macros(meal):
    """Liczy makro posiłku ze składników (produkty.json + makro.json) – jedno źródło prawdy."""
    t = {"kcal": 0.0, "p": 0.0, "f": 0.0, "c": 0.0}
    for k, q in meal["produkty"]:
        m = MAKRO.get(k)
        if not m:
            continue
        t["kcal"] += m[0] * q
        t["p"] += m[1] * q
        t["f"] += m[2] * q
        t["c"] += m[3] * q
    return {k: int(round(v)) for k, v in t.items()}

PLAN_PATH = os.path.join(STATE, "plan.json")

def resolve_slot(name):
    if not name:
        return None
    return SLOT_ALIASES.get(name.strip().lower())

def today_str(d=None):
    return (d or datetime.date.today()).isoformat()

def parse_date(ds):
    return datetime.date.fromisoformat(ds)

# ---------- Discord webhook ----------
def send_webhook(title, body, color=0xE67E22, ping=True):
    payload = {
        "content": CFG.get("mention", "") if ping else "",
        "embeds": [{"title": title, "description": body[:4000], "color": color}],
        "allowed_mentions": {"parse": ["everyone"] if ping else []},
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        WEBHOOK, data=data,
        headers={"Content-Type": "application/json", "User-Agent": "TrenerAI/3.0"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return 200 <= r.status < 300
    except Exception as e:
        log_error(f"webhook failed: {e}")
        return False

def log_error(msg):
    with open(os.path.join(STATE, "errors.log"), "a", encoding="utf-8") as f:
        f.write(f"{datetime.datetime.now().isoformat()} {msg}\n")

# ---------- typ dnia ----------
def day_type(d=None):
    d = d or datetime.date.today()
    ds = today_str(d)
    if ds in CFG["shift_start_dates"]:
        return "shift"
    if today_str(d - datetime.timedelta(days=1)) in CFG["shift_start_dates"]:
        return "postshift"
    return "normal"

# ---------- planer (minimalizacja marnowania) ----------
def _meal_products(meal):
    return set(k for k, _ in meal["produkty"])

def recalc_total(day):
    total = {"kcal": 0, "p": 0, "f": 0, "c": 0}
    for s in SLOTS:
        mm = meal_macros(MEAL_BY_ID[day[s]])
        for k in total:
            total[k] += mm[k]
    day["total"] = total
    return total

def generate_plan(start=None, days=None, force=False):
    """Buduje plan na 'days' dni od 'start'. Greedy: preferuje posiłki, których produkty
    już są w planie -> mniej różnych produktów do kupienia -> mniej marnowania."""
    start = start or datetime.date.today()
    days = days or CFG.get("plan_days", 7)
    tg = CFG["targets"]
    used_products = set()
    used_count = {}
    menu = {}
    for i in range(days):
        d = start + datetime.timedelta(days=i)
        dt = day_type(d)
        portable = (dt == "shift")
        pools = {}
        for slot in SLOTS:
            c = MEALS[slot]
            if portable:
                c = [m for m in c if m.get("portable")] or MEALS[slot]
            pools[slot] = c
        # losujemy wiele zestawów dnia i wybieramy najbliższy celu makro (+ mniej marnowania, + różnorodność)
        best, best_score = None, 1e18
        for _ in range(500):
            pick = {s: random.choice(pools[s]) for s in SLOTS}
            tot = {"kcal": 0, "p": 0, "f": 0, "c": 0}
            overlap = rep = 0
            for s in SLOTS:
                mm = meal_macros(pick[s])
                for k in tot:
                    tot[k] += mm[k]
                overlap += len(_meal_products(pick[s]) & used_products)
                rep += used_count.get(pick[s]["id"], 0)
            # logika wysiłku: kolacja/śniadanie mają być proste (bez gotowania w nocy),
            # obiad to danie gotowane (bez kary)
            cook_pen = 0
            for s in SLOTS:
                if not pick[s].get("portable"):
                    cook_pen += {"sniadanie": 6, "przedtreningowy": 8, "obiad": 0, "kolacja": 20}[s]
            score = (abs(tot["kcal"] - tg["kcal"]) + 2.0 * abs(tot["p"] - tg["protein"])
                     + 1.5 * abs(tot["f"] - tg["fat"]) + 0.4 * abs(tot["c"] - tg["carbs"])
                     - 8 * overlap + 25 * rep + cook_pen)
            score += 15 * max(0, 55 - tot["f"])   # zdrowa podłoga tłuszczu (~55 g min)
            score += 10 * max(0, tot["p"] - 210)   # nie przesadzaj z białkiem powyżej ~210 g
            if score < best_score:
                best_score, best = score, pick
        day = {"day_type": dt}
        for s in SLOTS:
            day[s] = best[s]["id"]
            used_count[best[s]["id"]] = used_count.get(best[s]["id"], 0) + 1
            used_products |= _meal_products(best[s])
        recalc_total(day)
        menu[today_str(d)] = day
    plan = {"start": today_str(start), "days": days, "menu": menu}
    save_json(PLAN_PATH, plan)
    return plan

def get_plan():
    if os.path.exists(PLAN_PATH):
        return load_json(PLAN_PATH)
    return generate_plan()

def get_day(d=None):
    """Zwraca plan dnia; jeśli dzień wypada poza aktualnym planem -> przeplanuj od tego dnia."""
    d = d or datetime.date.today()
    ds = today_str(d)
    plan = get_plan()
    if ds not in plan["menu"]:
        plan = generate_plan(start=d)
    return plan["menu"][ds]

def reroll(slot, d=None):
    d = d or datetime.date.today()
    ds = today_str(d)
    plan = get_plan()
    if ds not in plan["menu"]:
        plan = generate_plan(start=d)
    day = plan["menu"][ds]
    old = day[slot]
    cands = MEALS[slot]
    if day.get("day_type") == "shift":
        cands = [m for m in cands if m.get("portable")] or MEALS[slot]
    choices = [m for m in cands if m["id"] != old] or cands
    day[slot] = random.choice(choices)["id"]
    recalc_total(day)
    save_json(PLAN_PATH, plan)
    return day

# ---------- lista zakupów ----------
_JEDN_DISPLAY = {"lyzka": "łyżka", "sloik": "słoik", "opak": "opak.", "plaster": "plastry", "kromka": "kromki"}

def _qty_display(key, q):
    jedn = PROD[key]["jedn"]
    if jedn == "g":
        return f"{q/1000:.1f} kg" if q >= 1000 else f"{int(round(q))} g"
    if jedn == "ml":
        return f"{q/1000:.1f} l" if q >= 1000 else f"{int(round(q))} ml"
    return f"{int(round(q))} {_JEDN_DISPLAY.get(jedn, jedn)}"

def _amount(q, jedn):
    if jedn == "g":
        return f"{q/1000:.1f} kg" if q >= 1000 else f"{int(q)} g"
    if jedn == "ml":
        return f"{q/1000:.1f} l" if q >= 1000 else f"{int(q)} ml"
    return f"{int(q)} {_JEDN_DISPLAY.get(jedn, jedn)}"

def _buy_line(key, qty):
    """Zwraca (tekst ile kupić, koszt) licząc CAŁE opakowania."""
    info = PROD[key]
    opak = info.get("opak", 1)
    n = max(1, math.ceil(qty / opak - 1e-9))
    cost = n * info["cena"]
    if opak == 1:
        disp = f"{int(round(qty))} {_JEDN_DISPLAY.get(info['jedn'], info['jedn'])}"
    else:
        disp = f"{n} opak. ({_amount(opak, info['jedn'])})"
    return disp, cost

def _aggregate(plan, dates, trw):
    agg = {}
    for ds in dates:
        day = plan["menu"][ds]
        for slot in SLOTS:
            for k, q in MEAL_BY_ID[day[slot]]["produkty"]:
                if k in PROD and PROD[k].get("trw", "trwale") == trw:
                    agg[k] = agg.get(k, 0) + q
    return agg

def _render_groups(agg):
    groups, sub = {}, 0.0
    for k, q in agg.items():
        disp, cost = _buy_line(k, q)
        sub += cost
        groups.setdefault(PROD[k]["kat"], []).append((PROD[k]["nazwa"], disp, cost))
    out = ""
    for kat, label in KAT_ORDER:
        items = sorted(groups.get(kat, []), key=lambda x: -x[2])
        if not items:
            continue
        out += f"__{label}__\n"
        for nazwa, disp, cost in items:
            out += f"• {nazwa} — {disp}  (~{cost:.1f} zł)\n"
    return out, sub

def msg_shopping(plan=None):
    plan = plan or get_plan()
    dates = sorted(plan["menu"].keys())
    days = len(dates)
    body, fresh_total, pantry_total = "", 0.0, 0.0

    # ŚWIEŻE – w partiach, żeby nie zgniło
    batches = [dates] if days <= 4 else [dates[:(days + 1) // 2], dates[(days + 1) // 2:]]
    for idx, bd in enumerate(batches, 1):
        agg = _aggregate(plan, bd, "swieze")
        if not agg:
            continue
        lines, sub = _render_groups(agg)
        fresh_total += sub
        rng = f"{bd[0]} → {bd[-1]}" if len(bd) > 1 else bd[0]
        head = f"🔴 ŚWIEŻE — partia {idx} ({rng})" if len(batches) > 1 else f"🔴 ŚWIEŻE ({rng})"
        body += f"**{head}**\n_kup tuż przed tymi dniami – szybko się psuje_\n{lines}_partia: ~{sub:.0f} zł_\n\n"

    # BAZA / SPIŻARNIA – kup raz
    agg = _aggregate(plan, dates, "trwale")
    if agg:
        lines, pantry_total = _render_groups(agg)
        body += f"**🧱 BAZA / SPIŻARNIA**\n_kupujesz raz, nie psuje się i starczy na długo_\n{lines}_baza: ~{pantry_total:.0f} zł_\n\n"

    body += (f"💰 **Świeże: ~{fresh_total:.0f} zł** (na ten plan)\n"
             f"🧱 **Baza: ~{pantry_total:.0f} zł** (jednorazowo, starczy na kolejne tygodnie)\n"
             f"🧮 Pierwsze zakupy razem: ~{fresh_total + pantry_total:.0f} zł")
    return "🛒 LISTA ZAKUPÓW", body

# ---------- SPIŻARNIA: zakupy dzienne z pamięcią resztek ----------
def needed_for_day(day):
    agg = {}
    for slot in SLOTS:
        for k, q in MEAL_BY_ID[day[slot]]["produkty"]:
            agg[k] = agg.get(k, 0) + q
    return agg

SPIZ_PATH = os.path.join(STATE, "spizarnia.json")

def load_spiz():
    if os.path.exists(SPIZ_PATH):
        return load_json(SPIZ_PATH)
    return {"inv": {}, "log": {}}

def reset_spiz():
    save_json(SPIZ_PATH, {"inv": {}, "log": {}})

def ensure_day(ds, plan):
    """Rozlicza dany dzień raz: dokupuje całe opakowania gdy brakuje i zużywa składniki.
    Stan spiżarni (s['inv']) jest TRWAŁY – nie kasuje go reset planu. Jeśli posiłki dnia
    się zmieniły (reroll/nowy plan), dzień jest cofany i liczony od nowa."""
    s = load_spiz()
    cur_slots = {slot: plan["menu"][ds][slot] for slot in SLOTS}
    if ds in s["log"] and s["log"][ds].get("slots") == cur_slots:
        return s["log"][ds]
    if ds in s["log"]:                       # posiłki się zmieniły -> cofnij dzień
        s["inv"] = dict(s["log"][ds]["inv_before"])
        del s["log"][ds]
    inv = s["inv"]
    inv_before = dict(inv)
    needed = needed_for_day(plan["menu"][ds])
    buy, have = [], []
    for k, q in needed.items():
        if k not in PROD:
            log_error(f"brak produktu w cenniku: {k}")
            continue
        cur = inv.get(k, 0)
        if cur > 0:
            have.append([k, min(cur, q), max(0, cur - q)])
        if cur < q:
            deficit = q - cur
            opak = PROD[k].get("opak", 1)
            n = max(1, math.ceil(deficit / opak - 1e-9))
            buy.append([k, n, n * PROD[k]["cena"]])
            inv[k] = cur + n * opak - q
        else:
            inv[k] = cur - q
    s["log"][ds] = {"inv_before": inv_before, "buy": buy, "have": have,
                    "inv_after": dict(inv), "slots": cur_slots}
    s["inv"] = inv
    save_json(SPIZ_PATH, s)
    return s["log"][ds]

def _render_buy(buy):
    groups, total = {}, 0.0
    for k, n, cost in buy:
        total += cost
        opak, jedn = PROD[k].get("opak", 1), PROD[k]["jedn"]
        disp = f"{n} {_JEDN_DISPLAY.get(jedn, jedn)}" if opak == 1 else f"{n} opak. ({_amount(opak, jedn)})"
        groups.setdefault(PROD[k]["kat"], []).append((PROD[k]["nazwa"], disp, cost))
    out = ""
    for kat, label in KAT_ORDER:
        items = sorted(groups.get(kat, []), key=lambda x: -x[2])
        if not items:
            continue
        out += f"__{label}__\n"
        for nazwa, disp, cost in items:
            out += f"• {nazwa} — {disp}  (~{cost:.1f} zł)\n"
    return out, total

def msg_today_shopping(d=None):
    d = d or datetime.date.today()
    ds = today_str(d)
    plan = get_plan()
    if ds not in plan["menu"]:
        plan = generate_plan(start=d)
    res = ensure_day(ds, plan)
    buy, have, inv = res["buy"], res["have"], res["inv_after"]
    body = ""
    if buy:
        lines, total = _render_buy(buy)
        fresh = sum(c for k, n, c in buy if PROD.get(k, {}).get("trw") == "swieze")
        pantry = total - fresh
        body += f"**🛒 DO KUPIENIA DZIŚ**\n{lines}"
        body += f"\n💰 **RAZEM: ~{total:.0f} zł**"
        if pantry > 0.5:
            body += f"  _(świeże ~{fresh:.0f} zł + spiżarnia ~{pantry:.0f} zł jednorazowo)_"
        body += "\n\n"
    else:
        body += "**🛒 DO KUPIENIA DZIŚ**\nNic – wszystko masz w domu 🎉\n\n"
    buy_keys = {k for k, _, _ in buy}
    covered = [(k, u, l) for (k, u, l) in have if u > 0 and k not in buy_keys]
    if covered:
        body += "**🏠 JUŻ MASZ (nie kupuj)**\n"
        for k, used, left in covered:
            j = PROD[k]["jedn"]
            body += f"• {PROD[k]['nazwa']} — bierzesz {_amount(used, j)}, zostanie {_amount(left, j)}\n"
        body += "\n"
    leftovers = [(k, q) for k, q in inv.items() if q > 0.001]
    if leftovers:
        body += "**📦 ZAPAS W SPIŻARNI (pamiętam)**\n_(nie kupuj, dopóki się nie skończy)_\n"
        for k, q in sorted(leftovers, key=lambda x: -x[1]):
            body += f"• {PROD[k]['nazwa']} — {_amount(q, PROD[k]['jedn'])}\n"
    return f"🛒 ZAKUPY NA DZIŚ ({ds})", body

# ---------- formatowanie posiłków ----------
def fmt_ingredients(meal):
    lines = [f"• {PROD[k]['nazwa']} — {_qty_display(k, q)}" for k, q in meal["produkty"]]
    return "\n".join(lines) + "\n• + sól / pieprz / przyprawy wg uznania"

def fmt_meal(slot, meal):
    kroki = "\n".join(f"{i}. {k}" for i, k in enumerate(meal["kroki"], 1))
    mm = meal_macros(meal)
    return (
        f"**{meal['name']}**\n"
        f"```\n{mm['kcal']} kcal | B {mm['p']}g | T {mm['f']}g | W {mm['c']}g\n```"
        f"🛒 **Składniki (ile czego):**\n{fmt_ingredients(meal)}\n\n"
        f"👨‍🍳 **Jak zrobić krok po kroku:**\n{kroki}\n\n"
        f"_Nie pasuje? Wpisz:_ `!zmien {slot}`"
    )

def day_recipes(d=None):
    """Lista (tytuł, treść) z pełnym przepisem każdego posiłku dnia."""
    day = get_day(d)
    return [(f"🍽️ {SLOT_LABELS[slot]}", fmt_meal(slot, MEAL_BY_ID[day[slot]])) for slot in SLOTS]

def day_total_footer(day):
    t = day["total"]
    return f"\n📊 *Suma dnia:* {t['kcal']} kcal | B {t['p']}g | T {t['f']}g | W {t['c']}g"

# ---------- gotowe wiadomości (title, body) ----------
def msg_meal(slot, d=None):
    day = get_day(d)
    meal = MEAL_BY_ID[day[slot]]
    return f"🍽️ {SLOT_LABELS[slot]}", fmt_meal(slot, meal) + "\n" + day_total_footer(day)

def msg_reroll(slot, d=None):
    day = reroll(slot, d)
    meal = MEAL_BY_ID[day[slot]]
    return f"🔄 NOWY {SLOT_LABELS[slot]}", fmt_meal(slot, meal) + "\n" + day_total_footer(day)

def msg_today(d=None):
    d = d or datetime.date.today()
    day = get_day(d)
    body = ""
    for slot in SLOTS:
        m = MEAL_BY_ID[day[slot]]
        mm = meal_macros(m)
        body += f"**{SLOT_LABELS[slot]}** — {m['name']}\n`{mm['kcal']} kcal | B{mm['p']} T{mm['f']} W{mm['c']}`\n\n"
    t = day["total"]
    diff = t["kcal"] - CFG["targets"]["kcal"]
    znak = "+" if diff >= 0 else ""
    body += (f"📊 **SUMA DNIA:** {t['kcal']} kcal | B {t['p']}g | T {t['f']}g | W {t['c']}g\n"
             f"🎯 **Cel:** {CFG['targets']['kcal']} kcal (różnica {znak}{diff})\n")
    brak_b = CFG["targets"]["protein"] - t["p"]
    if brak_b >= 12:
        scoops = max(1, round(brak_b / 23))
        body += f"💪 **Białko niżej o {brak_b}g** — dosyp {scoops}× miarkę KFD WPC (+{scoops*23}g B, +{scoops*113} kcal).\n"
    body += "\n_Przepis:_ `!przepis obiad`  ·  _Zamiana:_ `!zmien obiad`  ·  _Zakupy:_ `!zakupy`"
    return f"📋 DZIŚ JESZ ({today_str(d)})", body

def log_weight(val):
    with open(os.path.join(PROGRESS, "waga.csv"), "a", encoding="utf-8") as f:
        f.write(f"{today_str()},{val}\n")
    return "⚖️ WAGA ZAPISANA", f"**{val} kg** ({today_str()})\nWażymy się o tej samej porze, na czczo. Trzymaj tak!"

def progres_message(n=10):
    parts = []
    wpath = os.path.join(PROGRESS, "waga.csv")
    if os.path.exists(wpath):
        with open(wpath, encoding="utf-8") as f:
            rows = [r.strip() for r in f if r.strip()][-n:]
        if rows:
            parts.append("**⚖️ Waga (ostatnie):**\n" + "\n".join(rows))
    return "📈 TWÓJ PROGRES", ("\n\n".join(parts) if parts else "Brak danych. Zapisz wagę: `!waga 81.5`")

# ---------- harmonogram dnia ----------
def schedule_for(dt):
    if dt == "shift":
        return [
            ("08:00", "shift_start", "🛡️ ZMIANA 24H – START", "txt",
             "Zaczynamy dobę na służbie. Woda na start 💧, jedzenie spakowane. Pilnujemy makro nawet w pracy 💪"),
            ("08:05", "daysummary", None, "daysummary", None),
            ("08:06", "shopping", None, "shoptoday", None),
            ("09:00", "sniadanie", "🍴 ŚNIADANIE (w pracy)", "meal:sniadanie", None),
            ("12:30", "obiad", "🍽️ OBIAD (w pracy)", "meal:obiad", None),
            ("17:00", "przedtreningowy", "🍴 POSIŁEK", "meal:przedtreningowy", None),
            ("20:30", "kolacja", "🌙 KOLACJA", "meal:kolacja", None),
            ("21:00", "water_eve", "💧 NAWODNIENIE", "txt",
             "Szklanka wody. Trzymaj się z dala od słodyczy z automatu – masz zaplanowane posiłki."),
            ("05:30", "endgame", "☕ KOŃCÓWKA ZMIANY", "txt",
             "Ostatnia prosta do 7:00. Woda. Po powrocie (~7:30): prysznic i sen do 14:00."),
        ]
    if dt == "postshift":
        return [
            ("14:00", "wake", "🌅 POBUDKA po nocce", "txt",
             "Spałeś po zmianie – super. Otwórz okno, szklanka wody 💧. Dziś łapiemy z powrotem rytm jedzenia."),
            ("14:05", "daysummary", None, "daysummary", None),
            ("14:06", "shopping", None, "shoptoday", None),
            ("14:45", "obiad", "🍽️ DUŻY POSIŁEK 1", "meal:obiad", None),
            ("17:00", "przedtreningowy", "🍴 POSIŁEK 2", "meal:przedtreningowy", None),
            ("20:30", "kolacja", "🌙 KOLACJA", "meal:kolacja", None),
            ("00:30", "sleep", "🛌 SEN", "txt", "Wracamy do normalnego rytmu. Telefon na bok. Dobranoc 💤"),
        ]
    # normal
    return [
        ("11:00", "wake", "🌅 POBUDKA", "txt",
         "Wstajemy! Otwórz okno, wpuść światło. Nowy dzień – pilnujemy makro 💪"),
        ("11:05", "daysummary", None, "daysummary", None),
        ("11:06", "shopping", None, "shoptoday", None),
        ("11:10", "water_am", "💧 SZKLANKA WODY", "txt", "300-500 ml wody od razu po wstaniu. Nawadniamy organizm po nocy."),
        ("11:45", "sniadanie", "🍳 ŚNIADANIE", "meal:sniadanie", None),
        ("13:30", "water_mid", "💧 WODA", "txt", "Kolejna szklanka. Cel na dzień: ok. 3 litry."),
        ("14:15", "przedtreningowy", "⚡ DRUGI POSIŁEK", "meal:przedtreningowy", None),
        ("16:45", "obiad", "🍽️ OBIAD", "meal:obiad", None),
        ("18:30", "water_pm", "💧 WODA", "txt", "Szklanka wody. Trzymamy nawodnienie do końca dnia."),
        ("20:30", "kolacja", "🌙 KOLACJA", "meal:kolacja", None),
        ("23:30", "winddown", "😴 WYCISZENIE", "txt",
         "Koniec ekranów najlepiej teraz. Przygotuj jedzenie na jutro. Dobranoc 💤"),
        ("00:45", "sleep", "🛌 SEN", "txt", "Czas spać. Sen 7-8h to połowa sukcesu. Dobranoc 💤"),
    ]

def build_message(d, key, title, mtype, txt):
    if mtype == "txt":
        return title, txt
    if mtype == "daysummary":
        return msg_today(d)
    if mtype == "shoptoday":
        return msg_today_shopping(d)
    if mtype.startswith("meal:"):
        slot = mtype.split(":", 1)[1]
        day = get_day(d)
        meal = MEAL_BY_ID[day[slot]]
        return title, fmt_meal(slot, meal) + "\n" + day_total_footer(day)
    return title, txt or ""

# ---------- silnik przypomnień ----------
def _sent_path(d):
    return os.path.join(STATE, f"sent-{today_str(d)}.json")

def due_reminders(now=None):
    now = now or datetime.datetime.now()
    d = now.date()
    dt = day_type(d)
    get_day(d)  # upewnij się, że plan obejmuje dziś
    sp = _sent_path(d)
    sent = load_json(sp) if os.path.exists(sp) else []
    late = CFG.get("late_window_min", 120)
    out = []
    for (hhmm, key, title, mtype, txt) in schedule_for(dt):
        if key in sent:
            continue
        h, m = map(int, hhmm.split(":"))
        sched = now.replace(hour=h, minute=m, second=0, microsecond=0)
        delta = (now - sched).total_seconds() / 60.0
        if delta < 0:
            continue
        if delta > late:
            sent.append(key)
            continue
        t, body = build_message(d, key, title, mtype, txt)
        out.append((t, body))
        sent.append(key)
    save_json(sp, sent)
    return out, dt
