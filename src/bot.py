import os
import discord
from discord.ext import commands
from discord import app_commands
from datetime import datetime, timezone, timedelta
import aiosqlite
from flask import Flask
from threading import Thread
from typing import List, Tuple, Optional

# ======================================
# Config / Timezone
# ======================================
BRAZIL_TZ = timezone(timedelta(hours=-3))

GUILD_IDS = [1404325825599246346]

# Bot setup
intents = discord.Intents.default()
intents.message_content = True
intents.members = True
bot = commands.Bot(command_prefix='!', intents=intents)

app = Flask('')


@app.route('/')
def home():
    return "Bot is running"


def run():
    app.run(host='0.0.0.0', port=8080)


def keep_alive():
    t = Thread(target=run)
    t.start()


# ======================================
# Database
# ======================================
DB_PATH = 'timesheet.db'


async def setup_database():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute('''
            CREATE TABLE IF NOT EXISTS time_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                entry_type TEXT,         -- 'entrada' | 'saida' | 'pausa' | 'retorno'
                timestamp DATETIME,
                notes TEXT
            )
        ''')
        await db.commit()


# ======================================
# Helpers de UI
# ======================================
def _user_nick(member: discord.Member) -> str:
    return member.display_name


def _user_avatar(member: discord.Member) -> str:
    return member.display_avatar.url if member.display_avatar else discord.Embed.Empty


def _fmt_hora_br(dt: datetime) -> str:
    return dt.astimezone(BRAZIL_TZ).strftime('%d/%m/%Y %H:%M:%S')


def _fmt_dia_label(dt: datetime) -> str:
    # Mapeamento dos dias da semana para portugues
    dias_semana = {
        'Monday': 'Seg',
        'Tuesday': 'Ter', 
        'Wednesday': 'Qua',
        'Thursday': 'Qui',
        'Friday': 'Sex',
        'Saturday': 'Sab',
        'Sunday': 'Dom'
    }
    
    dt_br = dt.astimezone(BRAZIL_TZ)
    dia_en = dt_br.strftime('%A')  # Nome do dia em inglas
    dia_pt = dias_semana.get(dia_en, dia_en[:3])  # Converte para portuguas
    return dt_br.strftime(f'%d/%m/%Y ({dia_pt})')


def _make_clock_embed(action: str,
                      member: discord.Member,
                      when: datetime,
                      color: int,
                      mention: str,
                      hint: str = None,
                      notes: Optional[str] = None) -> discord.Embed:
    emoji = {
        'entrada': '??',
        'saida': '??',
        'pausa': '??',
        'retorno': '??'
    }.get(action.lower(), '?')
    action_title = action.capitalize()
    nick = _user_nick(member)
    embed = discord.Embed(title=f'{emoji} {action_title} registrada',
                          description=f'{mention} (**{nick}**)',
                          color=color,
                          timestamp=when)
    embed.add_field(name='Horario (GMT-3)',
                    value=f'`{_fmt_hora_br(when)}`',
                    inline=True)
    if notes:
        embed.add_field(name='Notas', value=notes[:1024], inline=False)
    embed.set_author(name=nick, icon_url=_user_avatar(member))
    embed.set_thumbnail(url=_user_avatar(member))
    embed.set_footer(text=hint or 'Tenha um bom trabalho!')
    return embed


def _make_warning_embed(title: str,
                        message: str,
                        mention: Optional[str] = None) -> discord.Embed:
    desc = f'{mention} {message}' if mention else message
    return discord.Embed(title=f'?? {title}', description=desc, color=0xF1C40F)


def _make_danger_embed(title: str,
                       message: str,
                       icon_url: Optional[str] = None) -> discord.Embed:
    e = discord.Embed(title=f'??? {title}', description=message, color=0xE74C3C)
    if icon_url:
        e.set_thumbnail(url=icon_url)
    return e


# ======================================
# Parsing de datas e calculo de duracao (com pausas)
# ======================================
def _parse_timestamp_to_brazil_tz(ts: str) -> datetime:
    for fmt in ('%Y-%m-%d %H:%M:%S.%f%z', '%Y-%m-%d %H:%M:%S.%f',
                '%Y-%m-%d %H:%M:%S'):
        try:
            dt = datetime.strptime(ts, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=BRAZIL_TZ)
            return dt.astimezone(BRAZIL_TZ)
        except ValueError:
            continue
    return datetime.now(BRAZIL_TZ)


def _fmt_duration_seconds(total_seconds: float) -> str:
    total_seconds = int(total_seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    return f'{hours}h {minutes}min'


async def _fetch_entries(db, user_id: int,
                         dias: int) -> List[Tuple[str, str, Optional[str]]]:
    cursor = await db.execute(
        '''
        SELECT entry_type, timestamp, notes
        FROM time_entries
        WHERE user_id = ?
        AND datetime(timestamp) >= datetime('now', ?)
        ORDER BY timestamp ASC
        ''', (user_id, f'-{dias} days'))
    return await cursor.fetchall()


# ======================================
# Lagica de relatario (agrupar por dia e descontar pausas)
# ======================================
def _build_daily_fields(
    entries: List[Tuple[str, str, Optional[str]]]
) -> Tuple[List[Tuple[str, str]], float]:
    fields: List[Tuple[str, str]] = []
    current_day_label: Optional[str] = None
    entry_open_time: Optional[datetime] = None
    paused_from: Optional[datetime] = None
    day_seconds = 0
    period_seconds = 0
    day_lines: List[str] = []

    def flush_day():
        nonlocal fields, day_seconds, day_lines, current_day_label
        if current_day_label is None:
            return
        subtotal = _fmt_duration_seconds(day_seconds)
        value = "```\n" + "\n".join(day_lines) + (
            "\n" if day_lines else "") + f"Subtotal: {subtotal}\n```"
        fields.append((f"?? {current_day_label}", value))
        day_seconds = 0
        day_lines = []

    for entry_type, ts, notes in entries:
        bt = _parse_timestamp_to_brazil_tz(ts)
        day_label = _fmt_dia_label(bt)

        if current_day_label is None or day_label != current_day_label:
            if current_day_label is not None:
                paused_from = None
                flush_day()
            current_day_label = day_label

        if entry_type == 'entrada':
            entry_open_time = bt
            day_lines.append(f"?? Entrada   {bt.strftime('%H:%M:%S')}")
            if notes: day_lines.append(f"   ? notas: {notes}")
        elif entry_type == 'pausa':
            if entry_open_time and not paused_from:
                paused_from = bt
                day_lines.append(f"?? Pausa     {bt.strftime('%H:%M:%S')}")
                if notes: day_lines.append(f"   ? notas: {notes}")
            else:
                day_lines.append(
                    f"?? Pausa     {bt.strftime('%H:%M:%S')} (sem entrada ativa)"
                )
        elif entry_type == 'retorno':
            if entry_open_time and paused_from:
                pause_seconds = (bt - paused_from).total_seconds()
                day_lines.append(
                    f"?? Retorno   {bt.strftime('%H:%M:%S')}  (pausa: {_fmt_duration_seconds(pause_seconds)})"
                )
                if notes: day_lines.append(f"   ? notas: {notes}")
                paused_from = None
            else:
                day_lines.append(
                    f"?? Retorno   {bt.strftime('%H:%M:%S')} (sem pausa aberta)"
                )
        elif entry_type == 'saida':
            if entry_open_time:
                raw_seconds = (bt - entry_open_time).total_seconds()

                # Recontar pausas fechadas entre entrada e saada
                pause_total = 0
                start = entry_open_time
                end = bt
                open_pause = None
                for et2, ts2, _ in entries:
                    t2 = _parse_timestamp_to_brazil_tz(ts2)
                    if t2 < start or t2 > end:
                        continue
                    if et2 == 'pausa':
                        if open_pause is None:
                            open_pause = t2
                    elif et2 == 'retorno':
                        if open_pause is not None:
                            pause_total += (t2 - open_pause).total_seconds()
                            open_pause = None
                if paused_from:
                    pause_total += (bt - paused_from).total_seconds()
                    paused_from = None

                worked = max(0, raw_seconds - pause_total)
                day_seconds += worked
                period_seconds += worked

                day_lines.append(f"?? Saada     {bt.strftime('%H:%M:%S')}")
                if pause_total > 0:
                    day_lines.append(
                        f"? Pausas    {_fmt_duration_seconds(pause_total)} (descontadas)"
                    )
                day_lines.append(
                    f"?? Duraaao   {_fmt_duration_seconds(worked)}")

                entry_open_time = None
            else:
                day_lines.append(
                    f"?? Saada     {bt.strftime('%H:%M:%S')} (sem entrada)")
        else:
            day_lines.append(f"? {entry_type}   {bt.strftime('%H:%M:%S')}")

    if entry_open_time:
        day_lines.append(
            "?? Registro em aberto: altima entrada nao possui saada.")
    flush_day()

    return fields, period_seconds


def _chunk_fields(fields: List[Tuple[str, str]],
                  per_embed: int = 5) -> List[List[Tuple[str, str]]]:
    chunks = []
    for i in range(0, len(fields), per_embed):
        chunks.append(fields[i:i + per_embed])
    return chunks


def _make_report_embeds(target: discord.Member, dias: int,
                        fields: List[Tuple[str, str]],
                        period_seconds: float) -> List[discord.Embed]:
    target_nick = _user_nick(target)
    target_mention = target.mention

    header = discord.Embed(
        title="?? Seu Relatario de Ponto",
        description=
        f"{target_mention} **{target_nick}**\nPeraodo: altimos **{dias}** dias",
        color=0x3498DB)
    header.set_thumbnail(url=_user_avatar(target))
    header.set_author(name=target_nick, icon_url=_user_avatar(target))
    embeds = [header]

    for chunk in _chunk_fields(fields, per_embed=5):
        e = discord.Embed(color=0x2980B9)
        for name, value in chunk:
            e.add_field(name=name, value=value, inline=False)
        embeds.append(e)

    total_fmt = _fmt_duration_seconds(period_seconds)
    footer = discord.Embed(title="?? Resumo do Peraodo",
                           description=f"**Total trabalhado:** `{total_fmt}`",
                           color=0x2C3E50)
    embeds.append(footer)
    return embeds


# ======================================
# Comandos de texto (prefixo !)
# ======================================
@bot.command(name='entrada')
async def cmd_entrada(ctx):
    await _handle_entrada(ctx, notes=None)


@bot.command(name='saida')
async def cmd_saida(ctx):
    await _handle_saida(ctx, notes=None)


@bot.command(name='pausar')
async def cmd_pausar(ctx):
    await _handle_pausa(ctx, notes=None)


@bot.command(name='retomar')
async def cmd_retomar(ctx):
    await _handle_retorno(ctx, notes=None)


@bot.command(name='relatorio')
async def report(ctx, dias: int = 7):
    async with aiosqlite.connect(DB_PATH) as db:
        entries = await _fetch_entries(db, ctx.author.id, dias)

    if not entries:
        await ctx.send(embed=_make_warning_embed(
            "Sem registros",
            f"{ctx.author.mention} Nenhum registro encontrado nos altimos {dias} dias."
        ))
        return

    fields, period_seconds = _build_daily_fields(entries)
    embeds = _make_report_embeds(ctx.author, dias, fields, period_seconds)
    for em in embeds:
        await ctx.send(embed=em)


@bot.command(name='limpar')
async def clear_user_report(ctx, user: discord.Member):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            'SELECT COUNT(*) FROM time_entries WHERE user_id = ?', (user.id, ))
        count = await cursor.fetchone()
        total = count[0] if count else 0

        if total == 0:
            await ctx.send(embed=_make_warning_embed(
                "Nada para limpar",
                f"{user.mention} (**{_user_nick(user)}**) nao possui registros."
            ))
            return

        await db.execute('DELETE FROM time_entries WHERE user_id = ?',
                         (user.id, ))
        await db.commit()

        msg = (
            f"**Usuario:** {user.mention} (**{_user_nick(user)}**)\n"
            f"**Aaao:** Registros removidos\n"
            f"**Quantidade:** `{total}`\n"
            f"**Por:** {ctx.author.mention} (**{_user_nick(ctx.author)}**)\n"
            f"**Quando:** `{_fmt_hora_br(datetime.now(BRAZIL_TZ))}`")
        e = _make_danger_embed("Registros de ponto limpos",
                               msg,
                               icon_url=_user_avatar(user))
        e.set_footer(text="Atenaao: esta aaao a irreversavel.")
        await ctx.send(embed=e)


# ======================================
# Painel interativo (botaes sem modal, mensagens PaBLICAS)
# ======================================
class TimePanel(discord.ui.View):
    # View persistente precisa timeout=None
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Entrada",
        style=discord.ButtonStyle.success,
        emoji="??",
        custom_id="timepanel:entrada"  # <?? custom_id estavel
    )
    async def btn_entrada(self, interaction: discord.Interaction,
                          button: discord.ui.Button):
        await _handle_entrada_ctx_public(interaction, notes=None)

    @discord.ui.button(
        label="Saada",
        style=discord.ButtonStyle.danger,
        emoji="??",
        custom_id="timepanel:saida"  # <?? custom_id estavel
    )
    async def btn_saida(self, interaction: discord.Interaction,
                        button: discord.ui.Button):
        await _handle_saida_ctx_public(interaction, notes=None)

    @discord.ui.button(
        label="Pausar",
        style=discord.ButtonStyle.secondary,
        emoji="??",
        custom_id="timepanel:pausa"  # <?? custom_id estavel
    )
    async def btn_pausar(self, interaction: discord.Interaction,
                         button: discord.ui.Button):
        await _handle_pausa_ctx_public(interaction, notes=None)

    @discord.ui.button(
        label="Retomar",
        style=discord.ButtonStyle.primary,
        emoji="??",
        custom_id="timepanel:retorno"  # <?? custom_id estavel
    )
    async def btn_retomar(self, interaction: discord.Interaction,
                          button: discord.ui.Button):
        await _handle_retorno_ctx_public(interaction, notes=None)

    @discord.ui.button(
        label="Relatario (7 dias)",
        style=discord.ButtonStyle.primary,
        emoji="??",
        custom_id="timepanel:relatorio"  # <?? custom_id estavel
    )
    async def btn_relatorio(self, interaction: discord.Interaction,
                            button: discord.ui.Button):
        async with aiosqlite.connect(DB_PATH) as db:
            entries = await _fetch_entries(db, interaction.user.id, 7)
        if not entries:
            await interaction.response.send_message(embed=_make_warning_embed(
                "Sem registros",
                f"{interaction.user.mention} Nenhum registro encontrado nos altimos 7 dias."
            ),
                                                    ephemeral=False)
            return
        fields, period_seconds = _build_daily_fields(entries)
        embeds = _make_report_embeds(interaction.user, 7, fields,
                                     period_seconds)
        await interaction.response.send_message(embeds=embeds, ephemeral=False)


@bot.command(name='painel')
async def painel(ctx):
    """Envia o painel interativo com botaes."""
    view = TimePanel()
    await ctx.send("?? **Painel de Ponto** a use os botaes abaixo:", view=view)


# ======================================
# Implementaaaes das aaaes (compartilhadas)
# ======================================
async def _handle_entrada(ctx, notes: Optional[str]):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            'SELECT entry_type FROM time_entries WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1',
            (ctx.author.id, ))
        last = await cursor.fetchone()

        if last and last[0] == 'entrada':
            await ctx.send(embed=_make_warning_embed(
                'Entrada ja registrada',
                'voca ja registrou **entrada**. Use `!saida` quando encerrar as atividades.',
                ctx.author.mention))
            return

        now = datetime.now(BRAZIL_TZ)
        await db.execute(
            'INSERT INTO time_entries (user_id, entry_type, timestamp, notes) VALUES (?, ?, ?, ?)',
            (ctx.author.id, 'entrada', now, notes))
        await db.commit()

        await ctx.send(
            embed=_make_clock_embed('entrada',
                                    ctx.author,
                                    now,
                                    0x2ECC71,
                                    ctx.author.mention,
                                    hint='Use !saida quando terminar.',
                                    notes=notes))


async def _handle_saida(ctx, notes: Optional[str]):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            'SELECT entry_type FROM time_entries WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1',
            (ctx.author.id, ))
        last = await cursor.fetchone()

        if not last or last[0] == 'saida':
            await ctx.send(embed=_make_warning_embed(
                'Entrada necessaria',
                'voca precisa registrar **entrada** primeiro. Use `!entrada` para comeaar.',
                ctx.author.mention))
            return

        now = datetime.now(BRAZIL_TZ)
        await db.execute(
            'INSERT INTO time_entries (user_id, entry_type, timestamp, notes) VALUES (?, ?, ?, ?)',
            (ctx.author.id, 'saida', now, notes))
        await db.commit()

        await ctx.send(embed=_make_clock_embed('saida',
                                               ctx.author,
                                               now,
                                               0xE74C3C,
                                               ctx.author.mention,
                                               hint='Bom descanso! ?',
                                               notes=notes))


async def _handle_pausa(ctx, notes: Optional[str]):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            'SELECT entry_type, timestamp FROM time_entries WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1',
            (ctx.author.id, ))
        last = await cursor.fetchone()

        if not last or last[0] not in ('entrada', 'retorno'):
            await ctx.send(embed=_make_warning_embed(
                'Nao a possavel pausar',
                'Voca precisa estar **em jornada ativa** (apas `!entrada` ou `Retomar`) para pausar.',
                ctx.author.mention))
            return

        now = datetime.now(BRAZIL_TZ)
        await db.execute(
            'INSERT INTO time_entries (user_id, entry_type, timestamp, notes) VALUES (?, ?, ?, ?)',
            (ctx.author.id, 'pausa', now, notes))
        await db.commit()

        await ctx.send(embed=_make_clock_embed('pausa',
                                               ctx.author,
                                               now,
                                               0x95A5A6,
                                               ctx.author.mention,
                                               hint='Use Retomar para voltar.',
                                               notes=notes))


async def _handle_retorno(ctx, notes: Optional[str]):
    async with aiosqlite.connect(DB_PATH) as db:
        cursor = await db.execute(
            '''
            SELECT entry_type, timestamp FROM time_entries
            WHERE user_id = ? ORDER BY timestamp DESC LIMIT 1
            ''', (ctx.author.id, ))
        last = await cursor.fetchone()

        if not last or last[0] != 'pausa':
            await ctx.send(embed=_make_warning_embed(
                'Nao a possavel retomar',
                'Voca precisa estar **pausado** para retomar.',
                ctx.author.mention))
            return

        now = datetime.now(BRAZIL_TZ)
        await db.execute(
            'INSERT INTO time_entries (user_id, entry_type, timestamp, notes) VALUES (?, ?, ?, ?)',
            (ctx.author.id, 'retorno', now, notes))
        await db.commit()

        await ctx.send(embed=_make_clock_embed('retorno',
                                               ctx.author,
                                               now,
                                               0x1ABC9C,
                                               ctx.author.mention,
                                               hint='Jornada ativa.',
                                               notes=notes))


# Versaes para Interaction (botaes) a **pablicas**
async def _handle_entrada_ctx_public(interaction: discord.Interaction,
                                     notes: Optional[str]):

    class Dummy:
        author = interaction.user

        async def send(self, *args, **kwargs):
            if interaction.response.is_done():
                await interaction.followup.send(*args, **kwargs)
            else:
                await interaction.response.send_message(*args, **kwargs)

    await _handle_entrada(Dummy(), notes)


async def _handle_saida_ctx_public(interaction: discord.Interaction,
                                   notes: Optional[str]):

    class Dummy:
        author = interaction.user

        async def send(self, *args, **kwargs):
            if interaction.response.is_done():
                await interaction.followup.send(*args, **kwargs)
            else:
                await interaction.response.send_message(*args, **kwargs)

    await _handle_saida(Dummy(), notes)


async def _handle_pausa_ctx_public(interaction: discord.Interaction,
                                   notes: Optional[str]):

    class Dummy:
        author = interaction.user

        async def send(self, *args, **kwargs):
            if interaction.response.is_done():
                await interaction.followup.send(*args, **kwargs)
            else:
                await interaction.response.send_message(*args, **kwargs)

    await _handle_pausa(Dummy(), notes)


async def _handle_retorno_ctx_public(interaction: discord.Interaction,
                                     notes: Optional[str]):

    class Dummy:
        author = interaction.user

        async def send(self, *args, **kwargs):
            if interaction.response.is_done():
                await interaction.followup.send(*args, **kwargs)
            else:
                await interaction.response.send_message(*args, **kwargs)

    await _handle_retorno(Dummy(), notes)


# ======================================
# Slash Commands (/) a pablicos
# ======================================
@bot.tree.command(name="entrada", description="Registrar entrada")
async def slash_entrada(interaction: discord.Interaction):
    await _handle_entrada_ctx_public(interaction, notes=None)


@bot.tree.command(name="saida", description="Registrar saada")
async def slash_saida(interaction: discord.Interaction):
    await _handle_saida_ctx_public(interaction, notes=None)


@bot.tree.command(name="pausar", description="Pausar jornada atual")
async def slash_pausar(interaction: discord.Interaction):
    await _handle_pausa_ctx_public(interaction, notes=None)


@bot.tree.command(name="retomar", description="Retomar apas pausa")
async def slash_retomar(interaction: discord.Interaction):
    await _handle_retorno_ctx_public(interaction, notes=None)


@bot.tree.command(name="relatorio",
                  description="Exibe seu relatario de ponto agrupado por dia.")
@app_commands.describe(
    dias="Namero de dias a incluir no relatario (padrao: 7)")
async def relatorio_slash(interaction: discord.Interaction, dias: int = 7):
    async with aiosqlite.connect(DB_PATH) as db:
        entries = await _fetch_entries(db, interaction.user.id, dias)

    if not entries:
        await interaction.response.send_message(embed=_make_warning_embed(
            "Sem registros",
            f"{interaction.user.mention} Nenhum registro encontrado nos altimos {dias} dias."
        ),
                                                ephemeral=False)
        return

    fields, period_seconds = _build_daily_fields(entries)
    embeds = _make_report_embeds(interaction.user, dias, fields,
                                 period_seconds)
    await interaction.response.send_message(embeds=embeds, ephemeral=False)


@bot.tree.command(name="painel",
                  description="Postar painel de ponto com botaes")
async def slash_painel(interaction: discord.Interaction):
    view = TimePanel()
    await interaction.response.send_message(
        "?? **Painel de Ponto** a use os botaes abaixo:", view=view)


# ======================================
# Ready + View persistente + Sync de Slash
# ======================================
@bot.event
async def on_ready():
    print(f"[READY] Logado como {bot.user} (id={bot.user.id})")
    # 1) Banco e View persistente (precisa custom_id nos botaes e timeout=None)
    try:
        await setup_database()
        bot.add_view(TimePanel())
        print("[READY] Database ok e View persistente registrada.")
    except Exception as e:
        print(f"[ERRO] Ao preparar database/View: {e}")

    # 2) Sync dos slash por GUILD (aparece instantaneamente sa nesses servidores)
    try:
        if GUILD_IDS:
            for gid in GUILD_IDS:
                guild = discord.Object(id=gid)
                synced_guild = await bot.tree.sync(guild=guild)
                print(
                    f"[SLASH] Sync GUILD {gid}: {len(synced_guild)} comandos.")
        else:
            print("[SLASH] Nenhum GUILD_ID configurado para sync imediato.")
    except Exception as e:
        print(f"[ERRO] Sync por guild: {e}")

    # 3) Sync GLOBAL (necessario para aparecer na aba aComandosa do perfil do bot)
    #    Observaaao: pode levar ata ~1h para propagar no Discord.
    try:
        synced_global = await bot.tree.sync()
        print(
            f"[SLASH] Sync GLOBAL: {len(synced_global)} comandos publicados.")
    except Exception as e:
        print(f"[ERRO] Sync global: {e}")

    # 4) (Opcional) Presenaa/atividade do bot
    try:
        activity = discord.Activity(type=discord.ActivityType.watching,
                                    name="/entrada a /saida a /relatorio")
        await bot.change_presence(status=discord.Status.online,
                                  activity=activity)
        print("[READY] Presenaa atualizada.")
    except Exception as e:
        print(f"[ERRO] Ao atualizar presenaa: {e}")


# ======================================
# Run
# ======================================
if __name__ == "__main__":
    keep_alive()
    bot.run(os.environ['DISCORD_TOKEN'])
