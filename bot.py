# -*- coding: utf-8 -*-
"""
TRENER AI – bot Discord (24/7). Pilnuje makro + zakupy.
  1) Wysyła przypomnienia o porach dnia (webhookiem, z @everyone), rano pełną listę posiłków.
  2) Słucha komend i zapisuje dane.

Wymaga: pip install -U discord.py
Token: zmienna DISCORD_BOT_TOKEN albo config.json ("bot_token").

KOMENDY:
  !dzis / !menu          – wszystkie dzisiejsze posiłki + suma makro
  !przepis <posilek>     – przepis krok po kroku
  !zmien <posilek>       – losuje inny posiłek na dziś (przelicza zakupy)
  !zakupy                – lista zakupów na cały plan + orientacyjna cena
  !plan                  – posiłki na najbliższe dni
  !nowyplan              – generuje nowy plan od dziś
  !waga <kg>             – zapis wagi
  !progres               – ostatnie wpisy wagi
  !pomoc                 – ta lista
"""
import os, asyncio, datetime
import discord
from discord.ext import commands, tasks
import core

TOKEN = os.environ.get("DISCORD_BOT_TOKEN") or core.CFG.get("bot_token", "")
PREFIX = core.CFG.get("command_prefix", "!")

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)

def embed(title, body, color=0x3498DB):
    return discord.Embed(title=title, description=body[:4000], color=color)

@tasks.loop(seconds=60)
async def reminder_loop():
    try:
        msgs, _dt = await asyncio.to_thread(core.due_reminders)
        for (title, body) in msgs:
            await asyncio.to_thread(core.send_webhook, title, body, 0xE67E22, True)
    except Exception as e:
        core.log_error(f"reminder_loop: {e}")

@reminder_loop.before_loop
async def _before():
    await bot.wait_until_ready()

@bot.event
async def on_ready():
    if not reminder_loop.is_running():
        reminder_loop.start()
    print(f"Zalogowano jako {bot.user} – Trener AI online (makro + zakupy).")

def _bad_slot():
    return embed("❓ Nie znam tego posiłku",
                 "Użyj: `sniadanie`, `przedtreningowy` (2. posiłek), `obiad` lub `kolacja`.\n"
                 "Np. `!zmien obiad` albo `!przepis kolacja`.", 0xE74C3C)

@bot.command(name="dzis", aliases=["dziś", "menu", "plan_dnia"])
async def cmd_dzis(ctx):
    title, body = await asyncio.to_thread(core.msg_today)
    await ctx.send(embed=embed(title, body, 0x2ECC71))
    for (t, b) in await asyncio.to_thread(core.day_recipes):
        await ctx.send(embed=embed(t, b, 0xE67E22))

@bot.command(name="przepis", aliases=["posilek", "danie"])
async def cmd_przepis(ctx, *, slot: str = ""):
    s = core.resolve_slot(slot)
    if not s:
        return await ctx.send(embed=_bad_slot())
    title, body = await asyncio.to_thread(core.msg_meal, s)
    await ctx.send(embed=embed(title, body, 0xE67E22))

@bot.command(name="zmien", aliases=["zmień", "reroll", "losuj", "inny"])
async def cmd_zmien(ctx, *, slot: str = ""):
    s = core.resolve_slot(slot)
    if not s:
        return await ctx.send(embed=_bad_slot())
    title, body = await asyncio.to_thread(core.msg_reroll, s)
    await ctx.send(embed=embed(title, body, 0x3498DB))

@bot.command(name="zakupy", aliases=["lista", "zakupów", "shopping", "dokupienia"])
async def cmd_zakupy(ctx):
    title, body = await asyncio.to_thread(core.msg_today_shopping)
    await ctx.send(embed=embed(title, body, 0xF1C40F))

@bot.command(name="zakupytydzien", aliases=["zakupytydzień", "listatygodniowa", "calytydzien"])
async def cmd_zakupy_tydzien(ctx):
    title, body = await asyncio.to_thread(core.msg_shopping)
    await ctx.send(embed=embed(title, body, 0xF1C40F))

@bot.command(name="spizarnia", aliases=["spiżarnia", "zostalo", "zostało", "magazyn"])
async def cmd_spizarnia(ctx):
    title, body = await asyncio.to_thread(core.msg_today_shopping)
    await ctx.send(embed=embed(title, body, 0xF1C40F))

@bot.command(name="resetspizarnia", aliases=["resetspiżarni", "wyzeruj", "pustaspizarnia"])
async def cmd_reset_spiz(ctx):
    await asyncio.to_thread(core.reset_spiz)
    await ctx.send(embed=embed("🧹 SPIŻARNIA WYZEROWANA",
        "Zapas wyczyszczony – traktuję lodówkę jak pustą. Plan posiłków zostaje bez zmian.\n"
        "Od teraz `!zakupy` znów liczy wszystko od zera.", 0xE74C3C))

@bot.command(name="plan", aliases=["tydzien", "tydzień"])
async def cmd_plan(ctx):
    plan = await asyncio.to_thread(core.get_plan)
    body = ""
    for ds in sorted(plan["menu"].keys()):
        day = plan["menu"][ds]
        dn = datetime.date.fromisoformat(ds)
        dni = ["Pn", "Wt", "Śr", "Czw", "Pt", "Sob", "Nd"][dn.weekday()]
        body += f"**{dni} {ds}** ({day['total']['kcal']} kcal)\n"
        for slot in core.SLOTS:
            body += f"  • {core.MEAL_BY_ID[day[slot]]['name']}\n"
        body += "\n"
    await ctx.send(embed=embed(f"📅 PLAN POSIŁKÓW ({plan['days']} dni)", body, 0x9B59B6))

@bot.command(name="nowyplan", aliases=["przeplanuj", "resetplan"])
async def cmd_nowyplan(ctx):
    await asyncio.to_thread(core.generate_plan)
    title, body = await asyncio.to_thread(core.msg_today)
    await ctx.send(embed=embed("✅ NOWY PLAN GOTOWY", body, 0x2ECC71))

@bot.command(name="waga", aliases=["weight"])
async def cmd_waga(ctx, val: str = ""):
    val = val.replace(",", ".").strip()
    try:
        float(val)
    except ValueError:
        return await ctx.send(embed=embed("⚖️ Podaj wagę", "Np. `!waga 81.5`", 0xE74C3C))
    title, body = await asyncio.to_thread(core.log_weight, val)
    await ctx.send(embed=embed(title, body, 0x9B59B6))

@bot.command(name="progres", aliases=["progress", "postep", "postęp"])
async def cmd_progres(ctx):
    title, body = await asyncio.to_thread(core.progres_message)
    await ctx.send(embed=embed(title, body, 0x9B59B6))

@bot.command(name="pomoc", aliases=["help", "komendy", "trener"])
async def cmd_help(ctx):
    body = (
        "**📋 `!dzis`** – wszystkie dzisiejsze posiłki + suma makro\n"
        "**👨‍🍳 `!przepis <posilek>`** – przepis krok po kroku\n"
        "**🔄 `!zmien <posilek>`** – losuj inny posiłek\n"
        "**🛒 `!zakupy`** – co dokupić DZIŚ + co masz w domu (resztki)\n"
        "**🧾 `!zakupytydzien`** – lista na cały plan (świeże w partiach + baza)\n"
        "**📅 `!plan`** – posiłki na najbliższe dni\n"
        "**♻️ `!nowyplan`** – nowy plan posiłków (spiżarnia zostaje!)\n"
        "**🧹 `!resetspizarnia`** – wyzeruj zapas produktów\n"
        "**⚖️ `!waga <kg>`** – zapisz wagę (`!waga 81.5`)\n"
        "**📈 `!progres`** – ostatnie wpisy wagi\n\n"
        "_Posiłki:_ `sniadanie`, `przedtreningowy`, `obiad`, `kolacja`"
    )
    await ctx.send(embed=embed("💪 TRENER AI – KOMENDY", body, 0xE74C3C))

if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("Brak tokenu! Utwórz plik .env z DISCORD_BOT_TOKEN=... (patrz .env.example / DEPLOY.md).")
    bot.run(TOKEN)
