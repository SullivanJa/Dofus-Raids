from __future__ import annotations

import logging
import os
import sqlite3
import unicodedata

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv


load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("dofus-raids")

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise ValueError("Aucun token configuré dans DISCORD_TOKEN")

SYNC_COMMANDS_ON_STARTUP = os.getenv("SYNC_COMMANDS_ON_STARTUP", "false").lower() == "true"
DATABASE_PATH = os.getenv("DATABASE_PATH", "dofus_raids.db")

database_dir = os.path.dirname(DATABASE_PATH)
if database_dir:
    os.makedirs(database_dir, exist_ok=True)

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

db = sqlite3.connect(DATABASE_PATH, timeout=30)
db.execute("PRAGMA foreign_keys = ON")
db.execute("PRAGMA busy_timeout = 5000")
db.execute("PRAGMA journal_mode = WAL")
cursor = db.cursor()


DOFUS_CLASSES = [
    "Féca", "Osamodas", "Enutrof", "Sram", "Xélor",
    "Écaflip", "Éniripsa", "Iop", "Crâ", "Sadida",
    "Sacrieur", "Pandawa", "Roublard", "Zobal", "Steamer",
    "Éliotrope", "Huppermage", "Ouginak", "Forgelance"
]

CLASS_EMOJIS = {
    "Féca": "<:feca:1519030094230978684>",
    "Osamodas": "<:osa:1519030099448561815>",
    "Enutrof": "<:ENU:1519030092913709096>",
    "Sram": "<:SRAM:1519030056171606146>",
    "Xélor": "<:XELOR:1519030058407432253>",
    "Écaflip": "<:ECA:1519029994385571995>",
    "Éniripsa": "<:ENI:1519030090653110435>",
    "Iop": "<:IOP:1519030098139812001>",
    "Crâ": "<:CRA:1519029966132613282>",
    "Sadida": "<:SADI:1519030054972166174>",
    "Sacrieur": "<:SACRI:1519030053386846360>",
    "Pandawa": "<:PANDA:1519030050844966963>",
    "Roublard": "<:ROUB:1519030051918708826>",
    "Zobal": "<:ZOBAL:1519030059753541705>",
    "Steamer": "<:STEAMER:1519030057157529611>",
    "Éliotrope": "<:ELIO:1519030089445019770>",
    "Huppermage": "<:HUPPER:1519030095430418572>",
    "Ouginak": "<:OUGI:1519030049779744970>",
    "Forgelance": "<:FORGELANCE:1519031079770521630>",
}

SPEC_OPTIONS = [
    "Air", "Eau", "Feu", "Terre", "Neutre", "Multi",
    "Crit", "Do Pou", "Soin", "Tank", "Retrait PA",
    "Retrait PM", "Tout",
]


def ensure_column(table: str, column: str, definition: str):
    cursor.execute(f"PRAGMA table_info({table})")
    columns = [row[1] for row in cursor.fetchall()]

    if column not in columns:
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
        db.commit()


def setup_database():
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS raids (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        guild_id INTEGER,
        nom TEXT NOT NULL,
        date_text TEXT NOT NULL,
        objectif TEXT NOT NULL,
        max_players INTEGER NOT NULL,
        channel_id INTEGER,
        message_id INTEGER,
        created_by INTEGER NOT NULL,
        validated INTEGER NOT NULL DEFAULT 0,
        validated_at TEXT
    )
    """)

    # Ancienne table conservée pour éviter de casser la base existante
    # Elle sert surtout à stocker présent/absent par raid et par utilisateur
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS signups (
        raid_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        pseudo TEXT NOT NULL,
        classe TEXT NOT NULL,
        niveau TEXT,
        build TEXT,
        status TEXT NOT NULL DEFAULT 'present',
        selected INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (raid_id, user_id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS player_profiles (
        user_id INTEGER PRIMARY KEY,
        discord_name TEXT NOT NULL
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS raid_admin_roles (
        guild_id INTEGER NOT NULL,
        role_id INTEGER NOT NULL,
        PRIMARY KEY (guild_id, role_id)
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS raid_settings (
        guild_id INTEGER PRIMARY KEY,
        announce_role_id INTEGER
    )
    """)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS player_characters (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        name TEXT NOT NULL,
        classe TEXT NOT NULL,
        specs TEXT NOT NULL,
        stuff_opti INTEGER NOT NULL DEFAULT 0
    )
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS signup_characters (
        raid_id INTEGER NOT NULL,
        user_id INTEGER NOT NULL,
        character_id INTEGER NOT NULL,
        selected INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (raid_id, user_id, character_id)
    )
    """)

    db.commit()

    ensure_column("signups", "selected", "INTEGER NOT NULL DEFAULT 0")
    ensure_column("raids", "validated", "INTEGER NOT NULL DEFAULT 0")
    ensure_column("signup_characters", "selected", "INTEGER NOT NULL DEFAULT 0")
    ensure_column("raids", "validated_at", "TEXT")
    ensure_column("raids", "guild_id", "INTEGER")
    cursor.execute("""
        UPDATE signups
        SET status = 'present'
        WHERE status IN ('bench', 'late', 'tentative')
    """)

    cursor.execute("""
        UPDATE signups
        SET selected = 0
        WHERE status = 'absence'
    """)

    cursor.execute("""
        UPDATE signups
        SET status = 'absent'
        WHERE status = 'absence'
    """)

    cursor.execute("""
        UPDATE signup_characters
        SET selected = 1
        WHERE selected = 0
          AND EXISTS (
              SELECT 1
              FROM signups s
              WHERE s.raid_id = signup_characters.raid_id
                AND s.user_id = signup_characters.user_id
                AND s.selected = 1
          )
    """)

    db.commit()


setup_database()


def clean_one_line(text: str | None) -> str:
    if not text:
        return ""
    return " ".join(str(text).split())


def normalize_key(text: str) -> str:
    text = clean_one_line(text).lower()
    text = unicodedata.normalize("NFD", text)
    text = "".join(char for char in text if unicodedata.category(char) != "Mn")
    text = text.replace(" ", "").replace("-", "").replace("_", "")
    return text


def trim(text: str | None, limit: int) -> str:
    text = clean_one_line(text)
    if len(text) <= limit:
        return text
    return text[:limit - 1] + "…"


def shorten_field(text: str, limit: int = 1024) -> str:
    if len(text) <= limit:
        return text
    return text[:limit - 25] + "\n… liste trop longue"


def get_display_name(user: discord.abc.User) -> str:
    return clean_one_line(getattr(user, "display_name", user.name))


def upsert_profile(user: discord.abc.User):
    cursor.execute("""
        INSERT INTO player_profiles (user_id, discord_name)
        VALUES (?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET discord_name = excluded.discord_name
    """, (user.id, get_display_name(user)))
    db.commit()


def get_class_icon(classe: str) -> str:
    if not classe:
        return "•"
    return CLASS_EMOJIS.get(classe, f"• {classe}")


def emoji_for_select(value: str):
    emoji = CLASS_EMOJIS.get(value)
    if not emoji:
        return None
    if isinstance(emoji, str) and emoji.startswith("<"):
        return discord.PartialEmoji.from_str(emoji)
    return emoji


def is_raid_admin(interaction: discord.Interaction) -> bool:
    user = interaction.user

    if not isinstance(user, discord.Member):
        return False

    if interaction.guild is None:
        return False

    perms = user.guild_permissions
    if perms.administrator or perms.manage_guild:
        return True

    cursor.execute("""
        SELECT role_id
        FROM raid_admin_roles
        WHERE guild_id = ?
    """, (interaction.guild.id,))

    allowed_role_ids = {row[0] for row in cursor.fetchall()}

    for role in user.roles:
        if role.id in allowed_role_ids:
            return True

    return False


def can_manage_raid_settings(interaction: discord.Interaction) -> bool:
    user = interaction.user

    if not isinstance(user, discord.Member):
        return False

    perms = user.guild_permissions
    return perms.administrator or perms.manage_guild


def get_announce_role_id(guild_id: int) -> int | None:
    cursor.execute("""
        SELECT announce_role_id
        FROM raid_settings
        WHERE guild_id = ?
    """, (guild_id,))

    row = cursor.fetchone()
    if not row or row[0] is None:
        return None

    return int(row[0])


def get_announce_mention(guild: discord.Guild) -> str:
    role_id = get_announce_role_id(guild.id)

    if role_id is None:
        return ""

    role = guild.get_role(role_id)
    if role is None:
        return ""

    return role.mention


def add_spacer(embed: discord.Embed):
    embed.add_field(
        name="\u200b",
        value="━━━━━━━━━━━━━━━━━━━━",
        inline=False
    )


def format_character_row(row) -> str:
    """
    row = (user_id, discord_name, character_id, character_name, classe, specs, stuff_opti, status, selected)
    """
    user_id, discord_name, character_id, character_name, classe, specs, stuff_opti, status, selected = row

    icon = get_class_icon(classe)
    name = trim(character_name, 18)
    specs_text = trim(specs, 24)

    if specs_text:
        return f"{icon} **{name}** - {specs_text}"

    return f"{icon} **{name}**"


def format_absent_row(row) -> str:
    user_id, discord_name = row
    return f"🚫 **{trim(discord_name, 28)}**"


def format_signup_row(row) -> str:
    user_id, discord_name, waiting_count, total_count = row
    return f"**{trim(discord_name, 24)}** ({waiting_count}/{total_count})"

def get_waiting_user_summary_rows_for_raid(raid_id: int):
    cursor.execute("""
        SELECT
            s.user_id,
            COALESCE(p.discord_name, s.pseudo) AS discord_name,
            SUM(CASE WHEN sc.selected = 0 THEN 1 ELSE 0 END) AS waiting_count,
            COUNT(sc.character_id) AS total_count
        FROM signups s
        JOIN signup_characters sc
            ON sc.raid_id = s.raid_id
            AND sc.user_id = s.user_id
        LEFT JOIN player_profiles p
            ON p.user_id = s.user_id
        WHERE s.raid_id = ?
          AND s.status = 'present'
        GROUP BY s.user_id, COALESCE(p.discord_name, s.pseudo)
        HAVING SUM(CASE WHEN sc.selected = 0 THEN 1 ELSE 0 END) > 0
        ORDER BY discord_name
    """, (raid_id,))

    return cursor.fetchall()
def add_character_table(
    embed: discord.Embed,
    title: str,
    rows: list,
    empty_text: str,
    row_formatter=format_character_row,
    max_rows_per_block: int = 6
):
    if not rows:
        embed.add_field(
            name=title,
            value=empty_text,
            inline=False
        )
        return

    rows_per_block = max_rows_per_block * 3

    for block_index, start in enumerate(range(0, len(rows), rows_per_block)):
        block = rows[start:start + rows_per_block]
        section_title = title if block_index == 0 else f"{title} — suite {block_index + 1}"
        columns = [[], [], []]

        for index, row in enumerate(block):
            columns[index % 3].append(row_formatter(row))

        for column_index, column in enumerate(columns):
            value = "\n\n".join(column) if column else "\u200b"
            embed.add_field(
                name=section_title if column_index == 0 else "\u200b",
                value=shorten_field(value),
                inline=True
            )


def get_raid_data(raid_id: int, guild_id: int | None = None):
    if guild_id is None:
        cursor.execute("""
            SELECT id, nom, date_text, objectif, max_players, validated
            FROM raids
            WHERE id = ?
        """, (raid_id,))
    else:
        cursor.execute("""
            SELECT id, nom, date_text, objectif, max_players, validated
            FROM raids
            WHERE id = ?
              AND guild_id = ?
        """, (raid_id, guild_id))

    return cursor.fetchone()


def get_character_rows_for_raid(raid_id: int, selected: int | None = None):
    selected_filter = ""
    params = [raid_id]

    if selected is not None:
        selected_filter = "AND sc.selected = ?"
        params.append(selected)

    cursor.execute(f"""
        SELECT
            s.user_id,
            COALESCE(p.discord_name, s.pseudo) AS discord_name,
            c.id,
            c.name,
            c.classe,
            c.specs,
            c.stuff_opti,
            s.status,
            sc.selected
        FROM signups s
        JOIN signup_characters sc
            ON sc.raid_id = s.raid_id
            AND sc.user_id = s.user_id
        JOIN player_characters c
            ON c.id = sc.character_id
        LEFT JOIN player_profiles p
            ON p.user_id = s.user_id
        WHERE s.raid_id = ?
          AND s.status = 'present'
          {selected_filter}
        ORDER BY sc.selected DESC, c.classe, c.name
    """, tuple(params))

    return cursor.fetchall()


def get_absent_rows_for_raid(raid_id: int):
    cursor.execute("""
        SELECT
            s.user_id,
            COALESCE(p.discord_name, s.pseudo) AS discord_name
        FROM signups s
        LEFT JOIN player_profiles p
            ON p.user_id = s.user_id
        WHERE s.raid_id = ?
          AND s.status = 'absent'
        ORDER BY discord_name
    """, (raid_id,))
    return cursor.fetchall()


def count_present_users(raid_id: int) -> int:
    cursor.execute("""
        SELECT COUNT(*)
        FROM signups
        WHERE raid_id = ? AND status = 'present'
    """, (raid_id,))
    return cursor.fetchone()[0]


def count_selected_characters(raid_id: int) -> int:
    return len(get_character_rows_for_raid(raid_id, selected=1))

def count_recent_raid_participations(user_id: int, guild_id: int, days: int = 14) -> int:
    cursor.execute("""
        SELECT COUNT(DISTINCT r.id)
        FROM raids r
        JOIN signup_characters sc
            ON sc.raid_id = r.id
        JOIN signups s
            ON s.raid_id = sc.raid_id
            AND s.user_id = sc.user_id
        WHERE r.guild_id = ?
          AND sc.user_id = ?
          AND sc.selected = 1
          AND s.status = 'present'
          AND r.validated = 1
          AND r.validated_at IS NOT NULL
          AND datetime(r.validated_at) >= datetime('now', ?)
    """, (guild_id, user_id, f"-{days} days"))

    return cursor.fetchone()[0]


def get_member_participation_history(user_id: int, guild_id: int):
    cursor.execute("""
        SELECT
            r.id,
            r.nom,
            r.date_text,
            r.validated_at,
            c.name,
            c.classe
        FROM raids r
        JOIN signup_characters sc
            ON sc.raid_id = r.id
        JOIN player_characters c
            ON c.id = sc.character_id
        JOIN signups s
            ON s.raid_id = sc.raid_id
            AND s.user_id = sc.user_id
        WHERE r.guild_id = ?
          AND sc.user_id = ?
          AND sc.selected = 1
          AND s.status = 'present'
          AND r.validated = 1
        ORDER BY datetime(r.validated_at) DESC, r.id DESC, c.name
    """, (guild_id, user_id))

    return cursor.fetchall()


def get_finished_raid_roster_rows(raid_id: int):
    cursor.execute("""
        SELECT
            COALESCE(p.discord_name, s.pseudo) AS discord_name,
            c.name,
            c.classe
        FROM signup_characters sc
        JOIN signups s
            ON s.raid_id = sc.raid_id
            AND s.user_id = sc.user_id
        JOIN player_characters c
            ON c.id = sc.character_id
        LEFT JOIN player_profiles p
            ON p.user_id = s.user_id
        WHERE sc.raid_id = ?
          AND sc.selected = 1
          AND s.status = 'present'
        ORDER BY c.classe, discord_name, c.name
    """, (raid_id,))

    return cursor.fetchall()
def get_signup_status(raid_id: int, user_id: int) -> str | None:
    cursor.execute("""
        SELECT status
        FROM signups
        WHERE raid_id = ? AND user_id = ?
    """, (raid_id, user_id))
    row = cursor.fetchone()
    return row[0] if row else None


def user_has_selected_character(raid_id: int, user_id: int) -> bool:
    cursor.execute("""
        SELECT COUNT(*)
        FROM signup_characters
        WHERE raid_id = ?
          AND user_id = ?
          AND selected = 1
    """, (raid_id, user_id))
    return cursor.fetchone()[0] > 0


def build_raid_embed(raid_id: int) -> discord.Embed:
    raid = get_raid_data(raid_id)

    if not raid:
        return discord.Embed(
            title="Raid introuvable",
            description="Ce raid n'existe plus.",
            color=discord.Color.red()
        )

    raid_id, nom, date_text, objectif, max_players, validated = raid

    selected_rows = get_character_rows_for_raid(raid_id, selected=1)
    waiting_rows = get_character_rows_for_raid(raid_id, selected=0)
    absent_rows = get_absent_rows_for_raid(raid_id)
    present_users = count_present_users(raid_id)

    selected_count = len(selected_rows)
    waiting_count = len(waiting_rows)
    offered_count = selected_count + waiting_count

    waiting_user_rows = get_waiting_user_summary_rows_for_raid(raid_id)

    color = discord.Color.green() if validated else discord.Color.purple()
    raid_status = "✅ ROSTER VALIDÉ" if validated else "🟣 RAID OUVERT"

    embed = discord.Embed(
        title=f"⚔️ Raid Dofus — {nom}",
        color=color
    )

    embed.add_field(
        name=raid_status,
        value="━━━━━━━━━━━━━━━━━━━━",
        inline=False
    )
    embed.add_field(
        name="Objectifs :",
        value=(
            f"{objectif}\n"
        ),
        inline=False
    )
    embed.add_field(
        name="📌 Informations",
        value=(
            f"**Date :** {date_text}\n"
            f"**Format :** {max_players} places"
        ),
        inline=True
    )

    embed.add_field(
        name="📊 État du roster",
        value=(
            f"**Joueurs présents :** {present_users}\n"
            f"**Inscrits :** {offered_count}\n"
            f"**Sélectionnés :** {selected_count}/{max_players}\n"
            f"**Absents :** {len(absent_rows)}"
        ),
        inline=True
    )

    embed.add_field(name="\u200b", value="\u200b", inline=True)

    add_spacer(embed)

    add_character_table(
        embed=embed,
        title=f"⭐ Roster en cours — {selected_count}/{max_players}",
        rows=selected_rows,
        empty_text="Aucun personnage sélectionné pour le moment."
    )

    add_spacer(embed)

    add_character_table(
        embed=embed,
        title=f"📝 Inscrits — {len(waiting_user_rows)}",
        rows=waiting_user_rows,
        empty_text="Aucun inscrit en attente de sélection.",
        row_formatter=format_signup_row
    )

    if absent_rows:
        add_spacer(embed)
        add_character_table(
            embed=embed,
            title=f"🚫 Absents — {len(absent_rows)}",
            rows=absent_rows,
            empty_text="Aucun absent.",
            row_formatter=format_absent_row
        )

    embed.set_footer(text=f"Raid ID: {raid_id}")
    return embed


def build_final_roster_embed(raid_id: int) -> discord.Embed:
    raid = get_raid_data(raid_id)

    if not raid:
        return discord.Embed(
            title="Raid introuvable",
            description="Ce raid n'existe plus.",
            color=discord.Color.red()
        )

    raid_id, nom, date_text, objectif, max_players, validated = raid
    selected_rows = get_character_rows_for_raid(raid_id, selected=1)

    embed = discord.Embed(
        title=f"✅ Roster final — {nom}",
        color=discord.Color.green()
    )

    embed.add_field(
        name="📌 Informations",
        value=(
            f"**Date :** {date_text}\n"
            f"**Objectif :** {objectif}\n"
            f"**Personnages validés :** {len(selected_rows)}/{max_players}"
        ),
        inline=False
    )

    add_spacer(embed)

    add_character_table(
        embed=embed,
        title="Composition validée",
        rows=selected_rows,
        empty_text="Aucun personnage validé."
    )

    embed.set_footer(text=f"Raid ID: {raid_id}")
    return embed


async def refresh_raid_message(client: discord.Client, raid_id: int):
    cursor.execute("""
        SELECT channel_id, message_id
        FROM raids
        WHERE id = ?
    """, (raid_id,))

    row = cursor.fetchone()
    if not row:
        return

    channel_id, message_id = row
    if not channel_id or not message_id:
        return

    try:
        channel = client.get_channel(channel_id)
        if channel is None:
            channel = await client.fetch_channel(channel_id)

        message = await channel.fetch_message(message_id)
        await message.edit(embed=build_raid_embed(raid_id), view=RaidView(raid_id))
    except discord.NotFound:
        return


def get_user_characters(user_id: int):
    cursor.execute("""
        SELECT id, name, classe, specs, stuff_opti
        FROM player_characters
        WHERE user_id = ?
        ORDER BY name
        LIMIT 25
    """, (user_id,))
    return cursor.fetchall()


class AddCharacterNameModal(discord.ui.Modal):
    def __init__(self, raid_id: int | None = None):
        super().__init__(title="Ajouter un personnage")
        self.raid_id = raid_id
        self.name = discord.ui.TextInput(
            label="Nom du personnage",
            placeholder="Ex: XxRoxordu93xX",
            max_length=40,
            required=True
        )
        self.add_item(self.name)

    async def on_submit(self, interaction: discord.Interaction):
        upsert_profile(interaction.user)
        character_name = clean_one_line(str(self.name.value))

        if not character_name:
            await interaction.response.send_message(
                "❌ Le nom du personnage ne peut pas être vide.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"Personnage : **{character_name}**\nChoisis sa classe.",
            view=ChooseClassForCharacterView(character_name, self.raid_id),
            ephemeral=True
        )


class ChooseClassForCharacterSelect(discord.ui.Select):
    def __init__(self, character_name: str, raid_id: int | None):
        self.character_name = character_name
        self.raid_id = raid_id

        options = [
            discord.SelectOption(
                label=classe,
                value=classe,
                emoji=emoji_for_select(classe)
            )
            for classe in DOFUS_CLASSES
        ]

        super().__init__(
            placeholder="Choisis la classe du personnage",
            min_values=1,
            max_values=1,
            options=options,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        classe = self.values[0]
        await interaction.response.edit_message(
            content=(
                f"Personnage : **{self.character_name}**\n"
                f"Classe : {get_class_icon(classe)} **{classe}**\n"
                "Choisis une ou plusieurs spés."
            ),
            view=ChooseSpecsForCharacterView(self.character_name, classe, self.raid_id)
        )


class ChooseClassForCharacterView(discord.ui.View):
    def __init__(self, character_name: str, raid_id: int | None):
        super().__init__(timeout=180)
        self.add_item(ChooseClassForCharacterSelect(character_name, raid_id))


class ChooseSpecsForCharacterSelect(discord.ui.Select):
    def __init__(self, character_name: str, classe: str, raid_id: int | None):
        self.character_name = character_name
        self.classe = classe
        self.raid_id = raid_id

        options = [
            discord.SelectOption(label=spec, value=spec)
            for spec in SPEC_OPTIONS
        ]

        super().__init__(
            placeholder="Choisis les spés / rôles du personnage",
            min_values=1,
            max_values=min(5, len(options)),
            options=options,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        specs = ", ".join(self.values)
        await interaction.response.edit_message(
            content=(
                f"Personnage : **{self.character_name}**\n"
                f"Classe : {get_class_icon(self.classe)} **{self.classe}**\n"
                f"Spés : **{specs}**\n"
                "Le stuff est-il opti ?"
            ),
            view=ChooseStuffOptiView(self.character_name, self.classe, specs, self.raid_id)
        )


class ChooseSpecsForCharacterView(discord.ui.View):
    def __init__(self, character_name: str, classe: str, raid_id: int | None):
        super().__init__(timeout=180)
        self.add_item(ChooseSpecsForCharacterSelect(character_name, classe, raid_id))


class ChooseStuffOptiView(discord.ui.View):
    def __init__(self, character_name: str, classe: str, specs: str, raid_id: int | None):
        super().__init__(timeout=180)
        self.character_name = character_name
        self.classe = classe
        self.specs = specs
        self.raid_id = raid_id

    async def save_character(self, interaction: discord.Interaction, stuff_opti: int):
        upsert_profile(interaction.user)

        cursor.execute("""
            INSERT INTO player_characters (user_id, name, classe, specs, stuff_opti)
            VALUES (?, ?, ?, ?, ?)
        """, (
            interaction.user.id,
            self.character_name,
            self.classe,
            self.specs,
            stuff_opti
        ))
        db.commit()

        opti_text = "opti" if stuff_opti else "non opti"
        content = (
            f"✅ Personnage ajouté : {get_class_icon(self.classe)} **{self.character_name}** "
            f"— {self.classe} — {self.specs} · {opti_text}"
        )

        if self.raid_id is not None:
            if user_has_selected_character(self.raid_id, interaction.user.id):
                content += (
                    "\n\nUn de tes personnages est déjà validé dans le roster. "
                    "Tu ne peux plus modifier ton inscription, tu peux seulement te mettre absent."
                )
                await interaction.response.edit_message(content=content, view=None)
                return

            content += "\n\nTu peux maintenant choisir avec quel(s) personnage(s) tu veux venir."
            await interaction.response.edit_message(
                content=content,
                view=CharacterOfferView(self.raid_id, interaction.user.id)
            )
            return

        await interaction.response.edit_message(content=content, view=ProfileManageView())

    @discord.ui.button(label="Oui, stuff opti", emoji="✅", style=discord.ButtonStyle.success, row=0)
    async def opti_yes(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.save_character(interaction, 1)

    @discord.ui.button(label="Non", emoji="⚠️", style=discord.ButtonStyle.secondary, row=0)
    async def opti_no(self, interaction: discord.Interaction, button: discord.ui.Button):
        await self.save_character(interaction, 0)


class ProfileManageView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=180)

    @discord.ui.button(label="Ajouter un perso", emoji="➕", style=discord.ButtonStyle.success, row=0)
    async def add_character(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddCharacterNameModal())

    @discord.ui.button(label="Supprimer un perso", emoji="🗑️", style=discord.ButtonStyle.secondary, row=0)
    async def delete_character(self, interaction: discord.Interaction, button: discord.ui.Button):
        characters = get_user_characters(interaction.user.id)
        if not characters:
            await interaction.response.send_message(
                "Tu n'as aucun personnage à supprimer.",
                ephemeral=True
            )
            return

        await interaction.response.send_message(
            "Choisis le personnage à supprimer.",
            view=DeleteCharacterView(interaction.user.id),
            ephemeral=True
        )


class DeleteCharacterSelect(discord.ui.Select):
    def __init__(self, user_id: int):
        self.user_id = user_id
        characters = get_user_characters(user_id)
        options = []

        for character_id, name, classe, specs, stuff_opti in characters:
            opti_text = "opti" if stuff_opti else "non opti"
            options.append(
                discord.SelectOption(
                    label=trim(f"{name} — {classe}", 100),
                    value=str(character_id),
                    description=trim(f"{specs} · {opti_text}", 100),
                    emoji=emoji_for_select(classe)
                )
            )

        super().__init__(
            placeholder="Choisis le personnage à supprimer",
            min_values=1,
            max_values=1,
            options=options,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Ce menu ne t'est pas destiné.", ephemeral=True)
            return

        character_id = int(self.values[0])

        cursor.execute("""
            SELECT name
            FROM player_characters
            WHERE id = ? AND user_id = ?
        """, (character_id, interaction.user.id))
        row = cursor.fetchone()

        if not row:
            await interaction.response.send_message("Personnage introuvable.", ephemeral=True)
            return

        character_name = row[0]

        cursor.execute("DELETE FROM signup_characters WHERE character_id = ?", (character_id,))
        cursor.execute(
            "DELETE FROM player_characters WHERE id = ? AND user_id = ?",
            (character_id, interaction.user.id)
        )
        db.commit()

        await interaction.response.edit_message(
            content=f"🗑️ Personnage supprimé : **{character_name}**.",
            view=None
        )


class DeleteCharacterView(discord.ui.View):
    def __init__(self, user_id: int):
        super().__init__(timeout=180)
        self.add_item(DeleteCharacterSelect(user_id))


class CreateCharacterFromRaidView(discord.ui.View):
    def __init__(self, raid_id: int):
        super().__init__(timeout=180)
        self.raid_id = raid_id

    @discord.ui.button(label="Créer un personnage", emoji="➕", style=discord.ButtonStyle.success, row=0)
    async def create_character(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(AddCharacterNameModal(self.raid_id))


class CharacterOfferSelect(discord.ui.Select):
    def __init__(self, raid_id: int, user_id: int):
        self.raid_id = raid_id
        self.user_id = user_id
        characters = get_user_characters(user_id)

        options = []
        for character_id, name, classe, specs, stuff_opti in characters:
            opti_text = "opti" if stuff_opti else "non opti"
            options.append(
                discord.SelectOption(
                    label=trim(f"{name} — {classe}", 100),
                    value=str(character_id),
                    description=trim(f"{specs} · {opti_text}", 100),
                    emoji=emoji_for_select(classe)
                )
            )

        super().__init__(
            placeholder="Choisis 1 ou 2 personnages à proposer",
            min_values=1,
            max_values=min(2, len(options)),
            options=options,
            custom_id=f"raid:{raid_id}:offer:{user_id}",
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.user.id != self.user_id:
            await interaction.response.send_message("Ce menu ne t'est pas destiné.", ephemeral=True)
            return

        if user_has_selected_character(self.raid_id, interaction.user.id):
            await interaction.response.send_message(
                "Un de tes personnages est déjà validé dans le roster. Tu ne peux plus modifier ton inscription, tu peux seulement te mettre absent.",
                ephemeral=True
            )
            return

        chosen_ids = [int(value) for value in self.values]

        placeholders = ",".join("?" for _ in chosen_ids)
        cursor.execute(f"""
            SELECT COUNT(*)
            FROM player_characters
            WHERE user_id = ?
              AND id IN ({placeholders})
        """, (interaction.user.id, *chosen_ids))
        valid_count = cursor.fetchone()[0]

        if valid_count != len(chosen_ids):
            await interaction.response.send_message(
                "❌ Un des personnages sélectionnés n'appartient pas à ton profil.",
                ephemeral=True
            )
            return

        upsert_profile(interaction.user)
        discord_name = get_display_name(interaction.user)

        cursor.execute("""
            INSERT INTO signups (raid_id, user_id, pseudo, classe, niveau, build, status, selected)
            VALUES (?, ?, ?, 'Profil', NULL, NULL, 'present', 0)
            ON CONFLICT(raid_id, user_id)
            DO UPDATE SET
                pseudo = excluded.pseudo,
                status = 'present',
                selected = 0
        """, (self.raid_id, interaction.user.id, discord_name))

        cursor.execute("""
            DELETE FROM signup_characters
            WHERE raid_id = ? AND user_id = ?
        """, (self.raid_id, interaction.user.id))

        for character_id in chosen_ids:
            cursor.execute("""
                INSERT INTO signup_characters (raid_id, user_id, character_id, selected)
                VALUES (?, ?, ?, 0)
            """, (self.raid_id, interaction.user.id, character_id))

        db.commit()
        await refresh_raid_message(interaction.client, self.raid_id)

        await interaction.response.edit_message(
            content=f"✅ Présence enregistrée. Tu proposes **{len(chosen_ids)}** personnage(s). Les admins choisiront lequel vient.",
            view=None
        )


class CharacterOfferView(discord.ui.View):
    def __init__(self, raid_id: int, user_id: int):
        super().__init__(timeout=180)
        self.add_item(CharacterOfferSelect(raid_id, user_id))


class AdminCharacterSelect(discord.ui.Select):
    def __init__(self, raid_id: int, member: discord.Member):
        self.raid_id = raid_id
        self.member = member

        cursor.execute("""
            SELECT c.id, c.name, c.classe, c.specs, c.stuff_opti, sc.selected
            FROM signup_characters sc
            JOIN player_characters c ON c.id = sc.character_id
            JOIN signups s ON s.raid_id = sc.raid_id AND s.user_id = sc.user_id
            WHERE sc.raid_id = ?
              AND sc.user_id = ?
              AND s.status = 'present'
            ORDER BY c.name
        """, (raid_id, member.id))
        self.characters = cursor.fetchall()

        raid = get_raid_data(raid_id)
        max_players = raid[4]
        selected_count = count_selected_characters(raid_id)
        already_selected_count = sum(1 for row in self.characters if row[5] == 1)
        remaining_if_replace = max_players - selected_count + already_selected_count
        self.max_selectable = min(len(self.characters), max(0, remaining_if_replace))

        options = []
        for character_id, name, classe, specs, stuff_opti, is_selected in self.characters:
            opti_text = "opti" if stuff_opti else "non opti"
            prefix = "⭐ " if is_selected else ""
            options.append(
                discord.SelectOption(
                    label=trim(f"{prefix}{name} — {classe}", 100),
                    value=str(character_id),
                    description=trim(f"{specs} · {opti_text}", 100),
                    emoji=emoji_for_select(classe),
                    default=bool(is_selected)
                )
            )

        super().__init__(
            placeholder=f"Choisis le(s) perso(s) de {trim(member.display_name, 50)}",
            min_values=1,
            max_values=self.max_selectable,
            options=options,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_raid_admin(interaction):
            await interaction.response.send_message("Tu n'as pas la permission.", ephemeral=True)
            return

        chosen_ids = [int(value) for value in self.values]
        placeholders = ",".join("?" for _ in chosen_ids)

        cursor.execute(f"""
            SELECT COUNT(*)
            FROM signup_characters
            WHERE raid_id = ?
              AND user_id = ?
              AND character_id IN ({placeholders})
        """, (self.raid_id, self.member.id, *chosen_ids))
        valid_count = cursor.fetchone()[0]

        if valid_count != len(chosen_ids):
            await interaction.response.send_message(
                "❌ Un personnage sélectionné n'est pas proposé par ce membre.",
                ephemeral=True
            )
            return

        raid = get_raid_data(self.raid_id)
        max_players = raid[4]
        current_selected = count_selected_characters(self.raid_id)

        cursor.execute("""
            SELECT COUNT(*)
            FROM signup_characters
            WHERE raid_id = ? AND user_id = ? AND selected = 1
        """, (self.raid_id, self.member.id))
        already_selected = cursor.fetchone()[0]

        new_total = current_selected - already_selected + len(chosen_ids)
        if new_total > max_players:
            await interaction.response.send_message(
                f"Impossible : le roster passerait à {new_total}/{max_players}.",
                ephemeral=True
            )
            return

        cursor.execute("""
            UPDATE signup_characters
            SET selected = 0
            WHERE raid_id = ? AND user_id = ?
        """, (self.raid_id, self.member.id))

        cursor.execute(f"""
            UPDATE signup_characters
            SET selected = 1
            WHERE raid_id = ?
              AND user_id = ?
              AND character_id IN ({placeholders})
        """, (self.raid_id, self.member.id, *chosen_ids))

        cursor.execute("""
            UPDATE signups
            SET selected = 1
            WHERE raid_id = ? AND user_id = ?
        """, (self.raid_id, self.member.id))

        db.commit()
        await refresh_raid_message(interaction.client, self.raid_id)

        await interaction.response.edit_message(
            content=f"✅ Sélection mise à jour pour {self.member.mention} : **{len(chosen_ids)}** personnage(s).",
            view=None
        )


class AdminCharacterSelectionView(discord.ui.View):
    def __init__(self, raid_id: int, member: discord.Member):
        super().__init__(timeout=180)
        self.add_item(AdminCharacterSelect(raid_id, member))


class AdminRemoveCharacterSelect(discord.ui.Select):
    def __init__(self, raid_id: int, member: discord.Member):
        self.raid_id = raid_id
        self.member = member

        cursor.execute("""
            SELECT c.id, c.name, c.classe, c.specs, c.stuff_opti
            FROM signup_characters sc
            JOIN player_characters c ON c.id = sc.character_id
            WHERE sc.raid_id = ?
              AND sc.user_id = ?
              AND sc.selected = 1
            ORDER BY c.name
        """, (raid_id, member.id))
        characters = cursor.fetchall()

        options = []
        for character_id, name, classe, specs, stuff_opti in characters:
            opti_text = "opti" if stuff_opti else "non opti"
            options.append(
                discord.SelectOption(
                    label=trim(f"{name} — {classe}", 100),
                    value=str(character_id),
                    description=trim(f"{specs} · {opti_text}", 100),
                    emoji=emoji_for_select(classe)
                )
            )

        super().__init__(
            placeholder=f"Choisis le(s) perso(s) à retirer",
            min_values=1,
            max_values=len(options),
            options=options,
            row=0
        )

    async def callback(self, interaction: discord.Interaction):
        if not is_raid_admin(interaction):
            await interaction.response.send_message("Tu n'as pas la permission.", ephemeral=True)
            return

        chosen_ids = [int(value) for value in self.values]
        placeholders = ",".join("?" for _ in chosen_ids)

        cursor.execute(f"""
            UPDATE signup_characters
            SET selected = 0
            WHERE raid_id = ?
              AND user_id = ?
              AND character_id IN ({placeholders})
        """, (self.raid_id, self.member.id, *chosen_ids))

        cursor.execute("""
            SELECT COUNT(*)
            FROM signup_characters
            WHERE raid_id = ? AND user_id = ? AND selected = 1
        """, (self.raid_id, self.member.id))
        still_selected = cursor.fetchone()[0]

        cursor.execute("""
            UPDATE signups
            SET selected = ?
            WHERE raid_id = ? AND user_id = ?
        """, (1 if still_selected else 0, self.raid_id, self.member.id))

        db.commit()
        await refresh_raid_message(interaction.client, self.raid_id)

        await interaction.response.edit_message(
            content=f"✅ {len(chosen_ids)} personnage(s) retiré(s) du roster pour {self.member.mention}.",
            view=None
        )


class AdminRemoveCharacterView(discord.ui.View):
    def __init__(self, raid_id: int, member: discord.Member):
        super().__init__(timeout=180)
        self.add_item(AdminRemoveCharacterSelect(raid_id, member))


class RaidView(discord.ui.View):
    def __init__(self, raid_id: int):
        super().__init__(timeout=None)
        self.raid_id = raid_id

    @discord.ui.button(
        label="Présent",
        emoji="✅",
        style=discord.ButtonStyle.success,
        custom_id="raid_status_present",
        row=0
    )
    async def present_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        upsert_profile(interaction.user)

        if user_has_selected_character(self.raid_id, interaction.user.id):
            await interaction.response.send_message(
                "Un de tes personnages est déjà validé dans le roster. Tu ne peux plus modifier ton inscription, tu peux seulement te mettre absent.",
                ephemeral=True
            )
            return

        characters = get_user_characters(interaction.user.id)

        if not characters:
            await interaction.response.send_message(
                (
                    "Tu n'as encore aucun personnage enregistré.\n"
                    "Crée d'abord un personnage, puis clique de nouveau sur **Présent** pour choisir qui proposer."
                ),
                view=CreateCharacterFromRaidView(self.raid_id),
                ephemeral=True
            )
            return

        signup_status = get_signup_status(self.raid_id, interaction.user.id)
        action_text = "Modifie le(s) personnage(s) que tu veux inscrire." if signup_status == "present" else "Choisis avec quel(s) personnage(s) tu veux venir."

        await interaction.response.send_message(
            f"{action_text} Tu peux en proposer **2 maximum**.",
            view=CharacterOfferView(self.raid_id, interaction.user.id),
            ephemeral=True
        )

    @discord.ui.button(
        label="Absent",
        emoji="🚫",
        style=discord.ButtonStyle.secondary,
        custom_id="raid_status_absent",
        row=0
    )
    async def absent_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        upsert_profile(interaction.user)
        discord_name = get_display_name(interaction.user)

        cursor.execute("""
            INSERT INTO signups (raid_id, user_id, pseudo, classe, niveau, build, status, selected)
            VALUES (?, ?, ?, 'Profil', NULL, NULL, 'absent', 0)
            ON CONFLICT(raid_id, user_id)
            DO UPDATE SET
                pseudo = excluded.pseudo,
                status = 'absent',
                selected = 0
        """, (self.raid_id, interaction.user.id, discord_name))

        cursor.execute("""
            DELETE FROM signup_characters
            WHERE raid_id = ? AND user_id = ?
        """, (self.raid_id, interaction.user.id))

        db.commit()
        await refresh_raid_message(interaction.client, self.raid_id)

        await interaction.response.send_message("🚫 Absence enregistrée.", ephemeral=True)


@bot.event
async def on_ready():
    if not getattr(bot, "_persistent_views_loaded", False):
        cursor.execute("""
            SELECT id, message_id
            FROM raids
            WHERE message_id IS NOT NULL
        """)

        for raid_id, message_id in cursor.fetchall():
            bot.add_view(RaidView(raid_id), message_id=message_id)

        bot._persistent_views_loaded = True
        logger.info("Vues persistantes chargées")

    if SYNC_COMMANDS_ON_STARTUP and not getattr(bot, "_commands_synced", False):
        try:
            synced = await bot.tree.sync()
            logger.info("%s commande(s) globale(s) synchronisée(s)", len(synced))
        except discord.HTTPException:
            logger.exception("Erreur lors de la synchronisation globale des commandes")

        bot._commands_synced = True

    logger.info("Connecté en tant que %s", bot.user)


@bot.event
async def on_guild_join(guild: discord.Guild):
    logger.info("Bot ajouté au serveur %s (%s)", guild.name, guild.id)


@bot.tree.error
async def on_app_command_error(
    interaction: discord.Interaction,
    error: app_commands.AppCommandError
):
    logger.error("Erreur commande slash", exc_info=(type(error), error, error.__traceback__))

    message = "❌ Une erreur est survenue pendant l'exécution de la commande."

    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        logger.exception("Impossible d'envoyer le message d'erreur à l'utilisateur")
async def finished_raid_autocomplete(
    interaction: discord.Interaction,
    current: str
) -> list[app_commands.Choice[int]]:
    if interaction.guild is None:
        return []

    if not is_raid_admin(interaction):
        return []

    current_key = normalize_key(current)

    cursor.execute("""
        SELECT id, nom, date_text, max_players, validated_at
        FROM raids
        WHERE guild_id = ?
          AND validated = 1
        ORDER BY datetime(validated_at) DESC, id DESC
        LIMIT 50
    """, (interaction.guild.id,))

    raids = cursor.fetchall()
    choices = []

    for raid_id, nom, date_text, max_players, validated_at in raids:
        selected_count = count_selected_characters(raid_id)

        label = (
            f"#{raid_id} — {nom} — {date_text} — "
            f"{selected_count}/{max_players} terminé"
        )

        if current_key and current_key not in normalize_key(label):
            continue

        choices.append(
            app_commands.Choice(
                name=trim(label, 100),
                value=raid_id
            )
        )

        if len(choices) >= 25:
            break

    return choices
async def raid_autocomplete(
    interaction: discord.Interaction,
    current: str
) -> list[app_commands.Choice[int]]:
    if interaction.guild is None:
        return []

    if not is_raid_admin(interaction):
        return []

    current_key = normalize_key(current)

    cursor.execute("""
        SELECT id, nom, date_text, max_players, validated
        FROM raids
        WHERE guild_id = ?
          AND validated = 0
        ORDER BY id DESC
        LIMIT 50
    """, (interaction.guild.id,))

    raids = cursor.fetchall()
    choices = []

    for raid_id, nom, date_text, max_players, validated in raids:
        selected_count = count_selected_characters(raid_id)

        label = (
            f"#{raid_id} — {nom} — {date_text} — "
            f"{selected_count}/{max_players}"
        )

        if current_key and current_key not in normalize_key(label):
            continue

        choices.append(
            app_commands.Choice(
                name=trim(label, 100),
                value=raid_id
            )
        )

        if len(choices) >= 25:
            break

    return choices
@bot.tree.command(name="raid_creer", description="Créer un raid Dofus")
@app_commands.describe(
    nom="Nom du donjon, boss ou activité",
    date="Date et heure, ex: samedi 21h",
    objectif="Objectif du raid",
    places="Format du raid"
)
@app_commands.choices(places=[
    app_commands.Choice(name="Raid 12 places", value=12),
    app_commands.Choice(name="Raid 16 places", value=16),
])
async def raid_creer(
    interaction: discord.Interaction,
    nom: str,
    date: str,
    objectif: str,
    places: app_commands.Choice[int]
):
    if not is_raid_admin(interaction):
        await interaction.response.send_message("Tu n'as pas la permission.", ephemeral=True)
        return
    max_players = places.value

    if interaction.guild is None:
        await interaction.response.send_message(
            "Commande utilisable uniquement sur un serveur.",
            ephemeral=True
        )
        return

    cursor.execute("""
        INSERT INTO raids (
            guild_id, nom, date_text, objectif, max_players, created_by
        )
        VALUES (?, ?, ?, ?, ?, ?)
    """, (
        interaction.guild.id,
        nom,
        date,
        objectif,
        max_players,
        interaction.user.id
    ))

    db.commit()
    raid_id = cursor.lastrowid

    await interaction.response.send_message(
        embed=build_raid_embed(raid_id),
        view=RaidView(raid_id)
    )

    message = await interaction.original_response()

    cursor.execute("""
        UPDATE raids
        SET channel_id = ?, message_id = ?
        WHERE id = ?
    """, (
        message.channel.id,
        message.id,
        raid_id
    ))

    db.commit()


@bot.tree.command(name="raid_liste", description="Lister les raids Dofus prévus")
async def raid_liste(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message(
            "Commande utilisable uniquement sur un serveur.",
            ephemeral=True
        )
        return

    cursor.execute("""
        SELECT id, nom, date_text, max_players, validated
        FROM raids
        WHERE guild_id = ?
        ORDER BY id DESC
        LIMIT 10
    """, (interaction.guild.id,))

    raids = cursor.fetchall()

    if not raids:
        await interaction.response.send_message("Aucun raid Dofus prévu.", ephemeral=True)
        return

    lines = []
    for raid_id, nom, date_text, max_players, validated in raids:
        selected_count = len(get_character_rows_for_raid(raid_id, selected=1))
        offered_count = len(get_character_rows_for_raid(raid_id, selected=0)) + selected_count
        present_users = count_present_users(raid_id)
        statut = "✅ validé" if validated else "🟣 ouvert"

        lines.append(
            f"**#{raid_id}** — {nom} — {date_text} — "
            f"{selected_count}/{max_players} sélectionnés "
            f"({offered_count} persos proposés par {present_users} joueur(s)) — {statut}"
        )

    await interaction.response.send_message("\n".join(lines), ephemeral=True)

@bot.tree.command(name="raid_inscrits", description="Admin : lister les inscrits et leurs personnages")
@app_commands.describe(raid_id="Raid à choisir")
@app_commands.autocomplete(raid_id=raid_autocomplete)
async def raid_inscrits(interaction: discord.Interaction, raid_id: int):
    if not is_raid_admin(interaction):
        await interaction.response.send_message(
            "Tu n'as pas la permission de voir la liste complète des inscrits.",
            ephemeral=True
        )
        return

    if interaction.guild is None:
        await interaction.response.send_message(
            "Commande utilisable uniquement sur un serveur.",
            ephemeral=True
        )
        return

    raid = get_raid_data(raid_id, interaction.guild.id)

    if not raid:
        await interaction.response.send_message(
            "Ce raid n'existe pas.",
            ephemeral=True
        )
        return

    cursor.execute("""
        SELECT
            s.user_id,
            COALESCE(p.discord_name, s.pseudo) AS discord_name,
            c.name,
            c.classe,
            c.specs,
            c.stuff_opti
        FROM signups s
        JOIN signup_characters sc
            ON sc.raid_id = s.raid_id
            AND sc.user_id = s.user_id
        JOIN player_characters c
            ON c.id = sc.character_id
        LEFT JOIN player_profiles p
            ON p.user_id = s.user_id
        WHERE s.raid_id = ?
          AND s.status = 'present'
        ORDER BY discord_name, c.name
    """, (raid_id,))

    rows = cursor.fetchall()

    if not rows:
        await interaction.response.send_message(
            "Aucun inscrit pour ce raid.",
            ephemeral=True
        )
        return

    lines = []
    current_user_id = None

    for user_id, discord_name, character_name, classe, specs, stuff_opti in rows:
        if user_id != current_user_id:
            current_user_id = user_id
            participation_count = count_recent_raid_participations(user_id, interaction.guild.id)

            lines.append(
                f"* **{trim(discord_name, 40)}** "
                f"(a participé à **{participation_count}** raid(s) ces deux dernières semaines)"
            )

        icon = get_class_icon(classe)
        opti_text = "opti" if stuff_opti else "non opti"

        lines.append(
            f"  -> {icon} **{trim(character_name, 40)}** - {trim(specs, 80)} - {opti_text}"
        )

    message = "\n".join(lines)

    if len(message) <= 1900:
        await interaction.response.send_message(message, ephemeral=True)
        return

    chunks = []
    current_chunk = ""

    for line in lines:
        if len(current_chunk) + len(line) + 1 > 1900:
            chunks.append(current_chunk)
            current_chunk = line
        else:
            if current_chunk:
                current_chunk += "\n"
            current_chunk += line

    if current_chunk:
        chunks.append(current_chunk)

    await interaction.response.send_message(chunks[0], ephemeral=True)

    for chunk in chunks[1:]:
        await interaction.followup.send(chunk, ephemeral=True)
@bot.tree.command(name="raid_selectionner", description="Admin : choisir le(s) perso(s) d'un membre pour le roster")
@app_commands.describe(
    raid_id="Choisir un Raid",
    membre="Membre Discord à sélectionner"
)
@app_commands.autocomplete(raid_id=raid_autocomplete)
async def raid_selectionner(
    interaction: discord.Interaction,
    raid_id: int,
    membre: discord.Member
):
    if not is_raid_admin(interaction):
        await interaction.response.send_message("Tu n'as pas la permission de sélectionner le roster.", ephemeral=True)
        return

    if interaction.guild is None:
        await interaction.response.send_message(
            "Commande utilisable uniquement sur un serveur.",
            ephemeral=True
        )
        return

    raid = get_raid_data(raid_id, interaction.guild.id)
    if not raid:
        await interaction.response.send_message("Ce raid n'existe pas.", ephemeral=True)
        return

    cursor.execute("""
        SELECT status
        FROM signups
        WHERE raid_id = ? AND user_id = ?
    """, (raid_id, membre.id))
    signup = cursor.fetchone()

    if not signup:
        await interaction.response.send_message("Ce membre n'est pas inscrit à ce raid.", ephemeral=True)
        return

    if signup[0] != "present":
        await interaction.response.send_message("Ce membre est marqué absent, il ne peut pas être sélectionné.", ephemeral=True)
        return

    cursor.execute("""
        SELECT COUNT(*)
        FROM signup_characters
        WHERE raid_id = ? AND user_id = ?
    """, (raid_id, membre.id))
    offered_count = cursor.fetchone()[0]

    if offered_count == 0:
        await interaction.response.send_message("Ce membre n'a proposé aucun personnage pour ce raid.", ephemeral=True)
        return

    try:
        view = AdminCharacterSelectionView(raid_id, membre)
    except ValueError:
        await interaction.response.send_message("Le roster est déjà complet.", ephemeral=True)
        return

    await interaction.response.send_message(
        f"Choisis le(s) personnage(s) de {membre.mention} qui viennent au raid.",
        view=view,
        ephemeral=True
    )

@bot.tree.command(name="raid_participation", description="Admin : voir l'historique de participation d'un membre")
@app_commands.describe(membre="Membre Discord à consulter")
async def raid_participation(
    interaction: discord.Interaction,
    membre: discord.Member
):
    if not is_raid_admin(interaction):
        await interaction.response.send_message(
            "Tu n'as pas la permission de voir l'historique des participations.",
            ephemeral=True
        )
        return

    if interaction.guild is None:
        await interaction.response.send_message(
            "Commande utilisable uniquement sur un serveur.",
            ephemeral=True
        )
        return

    rows = get_member_participation_history(membre.id, interaction.guild.id)

    if not rows:
        await interaction.response.send_message(
            f"{membre.mention} n'a aucune participation enregistrée à un raid terminé.",
            ephemeral=True
        )
        return

    lines = [f"📜 **Historique de participation de {trim(membre.display_name, 40)}**"]

    current_raid_id = None

    for raid_id, nom, date_text, validated_at, character_name, classe in rows:
        if raid_id != current_raid_id:
            current_raid_id = raid_id
            lines.append("")
            lines.append(f"* **#{raid_id} — {trim(nom, 60)}** — {trim(date_text, 60)}")

        icon = get_class_icon(classe)
        lines.append(f"  -> {icon} **{trim(character_name, 40)}**")

    message = "\n".join(lines)

    if len(message) <= 1900:
        await interaction.response.send_message(message, ephemeral=True)
        return

    chunks = []
    current_chunk = ""

    for line in lines:
        if len(current_chunk) + len(line) + 1 > 1900:
            chunks.append(current_chunk)
            current_chunk = line
        else:
            if current_chunk:
                current_chunk += "\n"
            current_chunk += line

    if current_chunk:
        chunks.append(current_chunk)

    await interaction.response.send_message(chunks[0], ephemeral=True)

    for chunk in chunks[1:]:
        await interaction.followup.send(chunk, ephemeral=True)
@bot.tree.command(name="all_raid_participation", description="Admin : voir le roster d'un raid terminé")
@app_commands.describe(raid_id="Raid terminé à consulter")
@app_commands.autocomplete(raid_id=finished_raid_autocomplete)
async def all_raid_participation(
    interaction: discord.Interaction,
    raid_id: int
):
    if not is_raid_admin(interaction):
        await interaction.response.send_message(
            "Tu n'as pas la permission de voir les participations des raids terminés.",
            ephemeral=True
        )
        return

    if interaction.guild is None:
        await interaction.response.send_message(
            "Commande utilisable uniquement sur un serveur.",
            ephemeral=True
        )
        return

    raid = get_raid_data(raid_id, interaction.guild.id)

    if not raid:
        await interaction.response.send_message(
            "Ce raid n'existe pas.",
            ephemeral=True
        )
        return

    raid_id, nom, date_text, objectif, max_players, validated = raid

    if not validated:
        await interaction.response.send_message(
            "Ce raid n'est pas encore terminé/validé.",
            ephemeral=True
        )
        return

    rows = get_finished_raid_roster_rows(raid_id)

    if not rows:
        await interaction.response.send_message(
            "Aucun roster validé trouvé pour ce raid.",
            ephemeral=True
        )
        return

    lines = [
        f"✅ **Roster terminé — #{raid_id} — {trim(nom, 60)}**",
        f"📅 {trim(date_text, 80)}",
        ""
    ]

    for discord_name, character_name, classe in rows:
        icon = get_class_icon(classe)
        lines.append(
            f"{icon} **{trim(discord_name, 40)}** - {trim(character_name, 40)}"
        )

    message = "\n".join(lines)

    if len(message) <= 1900:
        await interaction.response.send_message(message, ephemeral=True)
        return

    chunks = []
    current_chunk = ""

    for line in lines:
        if len(current_chunk) + len(line) + 1 > 1900:
            chunks.append(current_chunk)
            current_chunk = line
        else:
            if current_chunk:
                current_chunk += "\n"
            current_chunk += line

    if current_chunk:
        chunks.append(current_chunk)

    await interaction.response.send_message(chunks[0], ephemeral=True)

    for chunk in chunks[1:]:
        await interaction.followup.send(chunk, ephemeral=True)
@bot.tree.command(name="raid_retirer", description="Admin : retirer un ou plusieurs persos d'un membre du roster")
@app_commands.describe(
    raid_id="Choisir un Raid",
    membre="Membre Discord à modifier"
)
@app_commands.autocomplete(raid_id=raid_autocomplete)
async def raid_retirer(
    interaction: discord.Interaction,
    raid_id: int,
    membre: discord.Member
):
    if not is_raid_admin(interaction):
        await interaction.response.send_message("Tu n'as pas la permission de modifier le roster.", ephemeral=True)
        return
    
    if interaction.guild is None:
        await interaction.response.send_message(
            "Commande utilisable uniquement sur un serveur.",
            ephemeral=True
        )
        return

    raid = get_raid_data(raid_id, interaction.guild.id)
    if not raid:
        await interaction.response.send_message(
            "Ce raid n'existe pas sur ce serveur.",
            ephemeral=True
        )
        return

    cursor.execute("""
        SELECT COUNT(*)
        FROM signup_characters
        WHERE raid_id = ? AND user_id = ? AND selected = 1
    """, (raid_id, membre.id))
    selected_count = cursor.fetchone()[0]

    if selected_count == 0:
        await interaction.response.send_message("Ce membre n'a aucun personnage sélectionné dans le roster.", ephemeral=True)
        return

    await interaction.response.send_message(
        f"Choisis le(s) personnage(s) de {membre.mention} à retirer du roster.",
        view=AdminRemoveCharacterView(raid_id, membre),
        ephemeral=True
    )


@bot.tree.command(name="raid_valider", description="Admin : valider et publier le roster final")
@app_commands.describe(raid_id="Choisir un Raid à valider")
@app_commands.autocomplete(raid_id=raid_autocomplete)
async def raid_valider(interaction: discord.Interaction, raid_id: int):
    if not is_raid_admin(interaction):
        await interaction.response.send_message("Tu n'as pas la permission de valider le roster.", ephemeral=True)
        return

    if interaction.guild is None:
        await interaction.response.send_message(
            "Commande utilisable uniquement sur un serveur.",
            ephemeral=True
        )
        return

    raid = get_raid_data(raid_id, interaction.guild.id)
    if not raid:
        await interaction.response.send_message("Ce raid n'existe pas.", ephemeral=True)
        return

    max_players = raid[4]
    selected_count = count_selected_characters(raid_id)

    if selected_count == 0:
        await interaction.response.send_message("Impossible de valider : aucun personnage n'a été sélectionné.", ephemeral=True)
        return

    cursor.execute("""
        UPDATE raids
        SET validated = 1,
            validated_at = COALESCE(validated_at, datetime('now'))
        WHERE id = ?
    """, (raid_id,))
    db.commit()

    await refresh_raid_message(interaction.client, raid_id)

    warning = ""
    if selected_count < max_players:
        warning = f"\n\n⚠️ Le roster n'est pas complet : {selected_count}/{max_players}."

    announce_mention = get_announce_mention(interaction.guild)
    announce_prefix = f"{announce_mention} " if announce_mention else ""

    await interaction.response.send_message(
        content=f"{announce_prefix}Roster final validé pour le raid #{raid_id}.{warning}",
        embed=build_final_roster_embed(raid_id),
        allowed_mentions=discord.AllowedMentions(roles=True, everyone=False, users=False)
    )


@bot.tree.command(name="profil_mes_persos", description="Afficher tes personnages enregistrés")
async def profil_mes_persos(interaction: discord.Interaction):
    upsert_profile(interaction.user)
    rows = get_user_characters(interaction.user.id)

    if not rows:
        await interaction.response.send_message(
            "Tu n'as encore aucun personnage enregistré.",
            view=ProfileManageView(),
            ephemeral=True
        )
        return

    lines = []
    for name_id, name, classe, specs, stuff_opti in rows:
        icon = get_class_icon(classe)
        opti_text = "✅ opti" if stuff_opti else "⚠️ non opti"
        lines.append(f"{icon} **{name}** — {classe} — {specs} · {opti_text}")

    await interaction.response.send_message(
        "\n".join(lines),
        view=ProfileManageView(),
        ephemeral=True
    )


@bot.tree.command(name="profil_ajouter_perso", description="Ajouter un personnage à ton profil")
async def profil_ajouter_perso(interaction: discord.Interaction):
    await interaction.response.send_modal(AddCharacterNameModal())


@bot.tree.command(name="profil_supprimer_perso", description="Supprimer un personnage de ton profil")
async def profil_supprimer_perso(interaction: discord.Interaction):
    characters = get_user_characters(interaction.user.id)
    if not characters:
        await interaction.response.send_message("Tu n'as aucun personnage à supprimer.", ephemeral=True)
        return

    await interaction.response.send_message(
        "Choisis le personnage à supprimer.",
        view=DeleteCharacterView(interaction.user.id),
        ephemeral=True
    )

@bot.tree.command(name="raid_admin_role_add", description="Admin serveur : autoriser un rôle à gérer les raids")
@app_commands.default_permissions(manage_guild=True)
@app_commands.describe(role="Rôle à autoriser pour les commandes raid admin")
async def raid_admin_role_add(
    interaction: discord.Interaction,
    role: discord.Role
):
    
    if interaction.guild is None:
        await interaction.response.send_message(
            "Commande utilisable uniquement sur un serveur.",
            ephemeral=True
        )
        return

    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "Impossible de vérifier tes permissions.",
            ephemeral=True
        )
        return

    if not interaction.user.guild_permissions.administrator and not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message(
            "Tu dois avoir la permission Administrateur ou Gérer le serveur pour configurer les rôles raid admin.",
            ephemeral=True
        )
        return

    cursor.execute("""
        INSERT OR IGNORE INTO raid_admin_roles (guild_id, role_id)
        VALUES (?, ?)
    """, (interaction.guild.id, role.id))

    db.commit()

    await interaction.response.send_message(
        f"✅ Le rôle {role.mention} peut maintenant utiliser les commandes admin raid.",
        ephemeral=True
    )
@bot.tree.command(name="raid_admin_role_remove", description="Admin serveur : retirer un rôle des admins raid")
@app_commands.default_permissions(manage_guild=True)
@app_commands.describe(role="Rôle à retirer des commandes raid admin")
async def raid_admin_role_remove(
    interaction: discord.Interaction,
    role: discord.Role
):
    if interaction.guild is None:
        await interaction.response.send_message(
            "Commande utilisable uniquement sur un serveur.",
            ephemeral=True
        )
        return

    if not isinstance(interaction.user, discord.Member):
        await interaction.response.send_message(
            "Impossible de vérifier tes permissions.",
            ephemeral=True
        )
        return

    if not interaction.user.guild_permissions.administrator and not interaction.user.guild_permissions.manage_guild:
        await interaction.response.send_message(
            "Tu dois avoir la permission Administrateur ou Gérer le serveur pour configurer les rôles raid admin.",
            ephemeral=True
        )
        return

    cursor.execute("""
        DELETE FROM raid_admin_roles
        WHERE guild_id = ? AND role_id = ?
    """, (interaction.guild.id, role.id))

    db.commit()

    await interaction.response.send_message(
        f"✅ Le rôle {role.mention} n'a plus accès aux commandes admin raid.",
        ephemeral=True
    )
@bot.tree.command(name="raid_admin_role_list", description="Admin serveur : lister les rôles admins raid")
@app_commands.default_permissions(manage_guild=True)
async def raid_admin_role_list(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message(
            "Commande utilisable uniquement sur un serveur.",
            ephemeral=True
        )
        return

    cursor.execute("""
        SELECT role_id
        FROM raid_admin_roles
        WHERE guild_id = ?
    """, (interaction.guild.id,))

    role_ids = [row[0] for row in cursor.fetchall()]

    if not role_ids:
        await interaction.response.send_message(
            "Aucun rôle admin raid configuré sur ce serveur.",
            ephemeral=True
        )
        return

    roles_text = []
    for role_id in role_ids:
        role = interaction.guild.get_role(role_id)
        if role:
            roles_text.append(f"- {role.mention}")
        else:
            roles_text.append(f"- rôle introuvable `{role_id}`")

    await interaction.response.send_message(
        "Rôles autorisés pour les commandes admin raid :\n" + "\n".join(roles_text),
        ephemeral=True
    )



@bot.tree.command(name="raid_annonce_role_set", description="Admin serveur : définir le rôle à prévenir lors d'une validation de roster")
@app_commands.default_permissions(manage_guild=True)
@app_commands.describe(role="Rôle à mentionner quand un roster est validé")
async def raid_annonce_role_set(
    interaction: discord.Interaction,
    role: discord.Role
):
    if interaction.guild is None:
        await interaction.response.send_message(
            "Commande utilisable uniquement sur un serveur.",
            ephemeral=True
        )
        return

    if not can_manage_raid_settings(interaction):
        await interaction.response.send_message(
            "Tu dois avoir la permission Administrateur ou Gérer le serveur pour configurer les annonces raid.",
            ephemeral=True
        )
        return

    cursor.execute("""
        INSERT INTO raid_settings (guild_id, announce_role_id)
        VALUES (?, ?)
        ON CONFLICT(guild_id)
        DO UPDATE SET announce_role_id = excluded.announce_role_id
    """, (interaction.guild.id, role.id))

    db.commit()

    await interaction.response.send_message(
        f"✅ Le rôle {role.mention} sera mentionné quand un roster sera validé.",
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none()
    )


@bot.tree.command(name="raid_annonce_role_remove", description="Admin serveur : désactiver la mention de rôle aux validations")
@app_commands.default_permissions(manage_guild=True)
async def raid_annonce_role_remove(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message(
            "Commande utilisable uniquement sur un serveur.",
            ephemeral=True
        )
        return

    if not can_manage_raid_settings(interaction):
        await interaction.response.send_message(
            "Tu dois avoir la permission Administrateur ou Gérer le serveur pour configurer les annonces raid.",
            ephemeral=True
        )
        return

    cursor.execute("""
        INSERT INTO raid_settings (guild_id, announce_role_id)
        VALUES (?, NULL)
        ON CONFLICT(guild_id)
        DO UPDATE SET announce_role_id = NULL
    """, (interaction.guild.id,))

    db.commit()

    await interaction.response.send_message(
        "✅ Les validations de roster ne mentionneront plus de rôle.",
        ephemeral=True
    )


@bot.tree.command(name="raid_annonce_role_show", description="Admin serveur : voir le rôle mentionné aux validations")
@app_commands.default_permissions(manage_guild=True)
async def raid_annonce_role_show(interaction: discord.Interaction):
    if interaction.guild is None:
        await interaction.response.send_message(
            "Commande utilisable uniquement sur un serveur.",
            ephemeral=True
        )
        return

    role_id = get_announce_role_id(interaction.guild.id)

    if role_id is None:
        await interaction.response.send_message(
            "Aucun rôle d'annonce raid n'est configuré sur ce serveur.",
            ephemeral=True
        )
        return

    role = interaction.guild.get_role(role_id)
    if role is None:
        await interaction.response.send_message(
            f"Le rôle configuré est introuvable (`{role_id}`). Tu peux le remplacer avec /raid_annonce_role_set.",
            ephemeral=True
        )
        return

    await interaction.response.send_message(
        f"Rôle mentionné lors des validations de roster : {role.mention}",
        ephemeral=True,
        allowed_mentions=discord.AllowedMentions.none()
    )
@bot.tree.command(name="profil_supprimer_mes_donnees", description="Supprimer toutes tes données enregistrées par le bot")
async def profil_supprimer_mes_donnees(interaction: discord.Interaction):
    user_id = interaction.user.id

    cursor.execute("DELETE FROM signup_characters WHERE user_id = ?", (user_id,))
    cursor.execute("DELETE FROM signups WHERE user_id = ?", (user_id,))
    cursor.execute("DELETE FROM player_characters WHERE user_id = ?", (user_id,))
    cursor.execute("DELETE FROM player_profiles WHERE user_id = ?", (user_id,))

    db.commit()

    await interaction.response.send_message(
        "✅ Toutes tes données enregistrées par le bot ont été supprimées.",
        ephemeral=True
    )
bot.run(TOKEN)
