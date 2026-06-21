# -*- coding: utf-8 -*-
"""
TRENER AI – lokalne CLI (test/awaryjnie). Główny tryb to bot.py (Discord 24/7).

  python coach.py dzis            -> dzisiejsze posiłki (konsola)
  python coach.py zakupy          -> lista zakupów + cena (konsola)
  python coach.py plan            -> cały plan (konsola)
  python coach.py nowyplan        -> generuje nowy plan od dziś
  python coach.py push-dzis       -> wysyła dzisiejsze posiłki na Discorda
  python coach.py push-zakupy     -> wysyła listę zakupów na Discorda
  python coach.py reroll <slot>   -> losuje nowy posiłek
  python coach.py test            -> wiadomość testowa
  python coach.py run             -> wysyła zaległe/aktualne przypomnienia (gdyby ktoś chciał Harmonogram Windows)
  python coach.py log-weight 81.5
"""
import sys, datetime
import core

def cmd_dzis():
    day = core.get_day()
    print(f"=== DZIŚ JESZ ({core.today_str()}) – typ dnia: {day['day_type']} ===\n")
    for slot in core.SLOTS:
        m = core.MEAL_BY_ID[day[slot]]
        print(f"[{core.SLOT_LABELS[slot]}] {m['name']}  ({m['kcal']}kcal B{m['p']}/T{m['f']}/W{m['c']})")
    t = day["total"]
    print(f"\nSUMA: {t['kcal']} kcal | B {t['p']}g | T {t['f']}g | W {t['c']}g (cel {core.CFG['targets']['kcal']})")

def cmd_zakupy():
    _, body = core.msg_today_shopping()
    print(body)

def cmd_plan():
    plan = core.get_plan()
    for ds in sorted(plan["menu"].keys()):
        day = plan["menu"][ds]
        print(f"\n{ds} ({day['total']['kcal']} kcal):")
        for slot in core.SLOTS:
            print(f"  - {core.MEAL_BY_ID[day[slot]]['name']}")

def cmd_audit():
    target = core.CFG["targets"]
    slot_target = {"sniadanie": 520, "przedtreningowy": 380, "obiad": 760, "kolacja": 620}
    for slot in core.SLOTS:
        print(f"\n=== {core.SLOT_LABELS[slot]} (cel ~{slot_target[slot]} kcal) ===")
        kcals = []
        for m in core.MEALS[slot]:
            mm = core.meal_macros(m)
            kcals.append(mm["kcal"])
            flag = "  <-- ODSTAJE" if abs(mm["kcal"] - slot_target[slot]) > 120 else ""
            print(f"  {mm['kcal']:>4} kcal  B{mm['p']:>3} T{mm['f']:>3} W{mm['c']:>3}  {m['name']}{flag}")
        print(f"  >> srednia slotu: {sum(kcals)//len(kcals)} kcal (min {min(kcals)}, max {max(kcals)})")
    avg_day = sum(sum(core.meal_macros(m)["kcal"] for m in core.MEALS[s])//len(core.MEALS[s]) for s in core.SLOTS)
    print(f"\n>>> SREDNI DZIEN ~{avg_day} kcal (cel {target['kcal']})")

def main():
    a = sys.argv[1:]
    if not a:
        print(__doc__); return
    c = a[0]
    if c == "dzis": cmd_dzis()
    elif c == "zakupy": cmd_zakupy()
    elif c == "plan": cmd_plan()
    elif c == "nowyplan": core.generate_plan(); print("nowy plan gotowy (spizarnia zostaje)"); cmd_dzis()
    elif c == "resetspizarnia": core.reset_spiz(); print("spizarnia wyzerowana")
    elif c == "audit": cmd_audit()
    elif c == "push-dzis": t, b = core.msg_today(); core.send_webhook(t, b, 0x2ECC71, ping=False); print("wyslano")
    elif c == "push-zakupy": t, b = core.msg_shopping(); core.send_webhook(t, b, 0xF1C40F, ping=False); print("wyslano")
    elif c == "reroll" and len(a) > 1:
        s = core.resolve_slot(a[1])
        if not s: print("slot: sniadanie|przedtreningowy|obiad|kolacja"); return
        t, b = core.msg_reroll(s); core.send_webhook(t, b, 0x3498DB, ping=False); print("nowy posilek:", s)
    elif c == "run":
        msgs, dt = core.due_reminders()
        for (t, b) in msgs: core.send_webhook(t, b, 0xE67E22, ping=True)
        print(f"[{datetime.datetime.now().isoformat()}] {dt}, wyslano: {len(msgs)}")
    elif c == "test":
        ok = core.send_webhook("🎯 TRENER AI – AKTYWNY",
            "Pilnuję Twojego makro 💪\nKomendy: `!dzis`, `!zakupy`, `!zmien obiad`, `!przepis obiad`, `!waga 82`, `!pomoc`.",
            0xE74C3C, ping=True)
        print("OK" if ok else "BLAD")
    elif c == "log-weight" and len(a) > 1:
        t, b = core.log_weight(a[1]); core.send_webhook(t, b, 0x9B59B6, ping=False); print("zapisano")
    else: print(__doc__)

if __name__ == "__main__":
    main()
