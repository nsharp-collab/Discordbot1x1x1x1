import discord
from discord.ext import commands
from discord import app_commands
from discord.ui import View, Modal, TextInput
import mysql.connector
import os
import datetime
import asyncio
import sys
from typing import Optional, Dict, Any, Union
import io
import time
import math
import random

# --- Configuration & Global State ---

# 🔑 IMPORTANT: HARDCODED CONFIGURATION FOR INITIAL STARTUP 🔑
# This ID is used for critical logging if the database fails to load the official ID.
# !!! REPLACE THE PLACEHOLDER ID BELOW WITH YOUR ACTUAL DISCORD CHANNEL ID !!!
HARDCODED_LOGGING_CHANNEL_ID = 1234567890 

# NOTE: For production, please ensure your database host, port, user, 
# and password are kept secure and preferably loaded from secure environment variables.
DB_CONFIG = {
    "host": "localhost",
    "port": 1433, #this is the default port
    "user": "root", #make sure you replace this with an account that has db acsses
    "password": "1234",
    "database": "discordbotdb"
}

# 🔑 HARDCODED FALLBACK TOKEN 🔑
HARDCODED_FALLBACK_TOKEN = "INSERT TOKEN HERE" 

# Global variables initialized at the module level (outside any function)
bot: commands.Bot
tree: app_commands.CommandTree
# Initialize with the hardcoded fallback ID so we can log connection errors immediately after on_ready
logging_channel_id: int = HARDCODED_LOGGING_CHANNEL_ID 
BOT_CREATOR_ID = 829539180441763861 # my disocrd id just dont worry about this
DASHBOARD_URL = "PLACEHOLDER" # use this if you want to make a dashboard for the bot

# --- Embed Helper and Logging Functions ---

def create_base_embed(title: str, description: Optional[str] = None, color: discord.Color = discord.Color.blurple(), thumbnail_url: Optional[str] = None) -> discord.Embed:
    """A helper function for creating consistent, branded embeds."""
    embed = discord.Embed(
        title=f"• {title}",
        description=description,
        color=color,
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text="Burgentruck Bot")
    if thumbnail_url:
        embed.set_thumbnail(url=thumbnail_url)
    return embed

async def send_log_embed(title: str, description: str, color: discord.Color):
    """Sends a log message to the configured logging channel."""
    if hasattr(bot, 'is_ready') and bot.is_ready() and logging_channel_id:
        channel = bot.get_channel(logging_channel_id)
        if channel:
            embed = create_base_embed(title, description, color=color)
            try:
                await channel.send(embed=embed)
            except Exception as e:
                print(f"Failed to send log to channel {logging_channel_id} (Is ID correct and permissions set?): {e}")

async def handle_db_runtime_failure(error: mysql.connector.Error):
    """Logs the database failure to the logging channel during runtime."""
    error_desc = f"**Error Type:** `{type(error).__name__}`\n**Message:** {error}"
    print("CRITICAL: Database connection failed during runtime. Logging to channel.")
    await send_log_embed(
        title="⚠️ CRITICAL DB FAILURE",
        description=f"Bot failed to connect to the database during a command execution. Commands requiring DB access will fail.\n\n{error_desc}",
        color=discord.Color.red()
    )

async def send_moderation_dm(member: Union[discord.Member, discord.User, discord.Object], action: str, guild_name: str, reason: str, duration: Optional[str] = None):
    """Sends a DM to the target user about the moderation action."""
    try:
        if isinstance(member, discord.Object):
            user = await bot.fetch_user(member.id)
        else:
            user = member
        action_title = action.upper()
        embed = create_base_embed(
            f"🚫 Moderation Action: {action_title}",
            f"You have received a **{action_title}** in the server **{guild_name}**.",
            color=discord.Color.dark_red() if action in ("BAN", "KICK", "MUTE", "WARN") else discord.Color.green()
        )
        if duration:
            embed.add_field(name="Duration", value=duration, inline=True)
        embed.add_field(name="Reason", value=reason, inline=False)
        embed.set_footer(text="If you believe this action was taken in error, contact a staff member.")
        await user.send(embed=embed)
    except discord.Forbidden:
        print(f"Could not DM user {member.id} about {action}.")
    except Exception as e:
        print(f"Error sending DM for {action} to {member.id}: {e}")

# --- Synchronous Database Utility Functions (For Startup & Async Wrapper) ---

def _get_sync_connection():
    """Attempts to establish a synchronous connection to the MySQL database."""
    try:
        for i in range(3):
            try:
                conn = mysql.connector.connect(**DB_CONFIG)
                return conn
            except mysql.connector.Error as err:
                if i < 2:
                    time.sleep(1)
                    continue
                raise err
    except mysql.connector.Error as err:
        print(f"Database connection error (Sync): {err}")
        return None

def setup_database_schema():
    """Creates all necessary tables if they do not exist (Synchronous)."""
    conn = None
    try:
        conn = _get_sync_connection()
        if not conn: return
        cursor = conn.cursor()
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS bot_config (
                name VARCHAR(255) PRIMARY KEY,
                value TEXT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS case_logs (
                `id` INT AUTO_INCREMENT PRIMARY KEY,
                `user_id` BIGINT NOT NULL,
                `moderator_id` BIGINT NOT NULL,
                `action` VARCHAR(50) NOT NULL,
                `reason` TEXT,
                `duration` VARCHAR(50) DEFAULT NULL,
                `timestamp` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS user_levels (
                guild_id BIGINT NOT NULL,
                user_id BIGINT NOT NULL,
                xp INT DEFAULT 0,
                level INT DEFAULT 0,
                message_count INT DEFAULT 0,
                last_xp_gain TIMESTAMP NULL,
                PRIMARY KEY (guild_id, user_id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS level_config (
                guild_id BIGINT PRIMARY KEY,
                xp_min INT DEFAULT 1,
                xp_max INT DEFAULT 10,
                xp_multiplier INT DEFAULT 100,
                xp_cooldown_seconds INT DEFAULT 60,
                level_up_channel_id BIGINT,
                top_message_role_id BIGINT,
                current_top_user_id BIGINT
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS level_roles (
                guild_id BIGINT NOT NULL,
                level INT NOT NULL,
                role_id BIGINT NOT NULL,
                PRIMARY KEY (guild_id, level)
            )
        """)
        conn.commit()
        print("Database schema verified and/or created successfully (Existing data preserved).")
    except mysql.connector.Error as err:
        print(f"Error setting up database schema: {err}")
    finally:
        if conn: 
            conn.close()

def fetch_bot_config() -> Dict[str, str]:
    """Fetches all key-value pairs from the bot_config table (Synchronous)."""
    conn = None
    try:
        conn = _get_sync_connection()
        if not conn: return {}
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT name, value FROM bot_config")
        config = {row['name']: row['value'] for row in cursor.fetchall()}
        cursor.close()
        return config
    except mysql.connector.Error as err:
        print(f"Error fetching initial bot config: {err}")
        return {}
    finally:
        if conn: conn.close()

def fetch_bot_token() -> str:
    """Fetches the BOT_TOKEN from the DB, environment, or uses the hardcoded fallback."""
    env_token = os.getenv("DISCORD_BOT_TOKEN")
    config = fetch_bot_config()
    db_token = config.get("BOT_TOKEN")
    token = db_token or env_token or HARDCODED_FALLBACK_TOKEN
    if not db_token:
        print("Warning: BOT_TOKEN not found in database. Using environment variable or hardcoded fallback.")
    return token

# --- Asynchronous Database Execution Wrapper (For Runtime Operations) ---

async def async_db_runner(func, *args, **kwargs):
    """Executes a blocking database operation in a separate thread and handles connection errors."""
    max_retries = 3
    retry_delay = 1
    def _execute_sync_op():
        last_error = None
        current_retry_delay = retry_delay
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except mysql.connector.errors.OperationalError as err:
                last_error = err
                if 'connection' in str(err).lower():
                    print(f"DB OperationalError (Attempt {attempt+1}/{max_retries}): {err}. Retrying in {current_retry_delay}s.")
                    time.sleep(current_retry_delay)
                    current_retry_delay *= 2
                else:
                    raise err 
            except Exception as e:
                last_error = e
                raise e
        if last_error:
            raise last_error
    try:
        return await asyncio.to_thread(_execute_sync_op)
    except mysql.connector.Error as err:
        print(f"CRITICAL DB ERROR during runtime op: {err}")
        if hasattr(bot, 'is_ready') and bot.is_ready():
            await handle_db_runtime_failure(err)
        return None
    except Exception as e:
        print(f"Non-MySQL error during DB op: {e}")
        return None

# --- Asynchronous Database Utility Functions (For Runtime ONLY) ---

async def async_get_user_caselogs(user_id: int):
    """Fetches all case logs for a specific user ID (Async)."""
    def sync_op(user_id):
        conn = _get_sync_connection()
        if not conn: return []
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT `id`, `action`, `reason`, `duration`, `moderator_id`, `timestamp` FROM case_logs WHERE `user_id` = %s ORDER BY `id` DESC", (user_id,))
            logs = cursor.fetchall()
            return logs
        finally:
            cursor.close()
            conn.close()
    return await async_db_runner(sync_op, user_id)

async def async_set_bot_config(name: str, value: str):
    """Sets or updates a configuration value in the bot_config table (Async)."""
    def sync_op(name, value):
        conn = _get_sync_connection()
        if not conn: return
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO bot_config (name, value) VALUES (%s, %s) ON DUPLICATE KEY UPDATE value = VALUES(value)", (name, value))
            conn.commit()
        finally:
            cursor.close()
            conn.close()
    await async_db_runner(sync_op, name, value)

async def async_log_case(user_id: int, mod_id: int, action: str, reason: str, duration: Optional[str] = None) -> Optional[int]:
    """Logs a moderation action to the case_logs table (Async). Returns the new case ID."""
    def sync_op(user_id, mod_id, action, reason, duration):
        conn = _get_sync_connection()
        if not conn: return None
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO case_logs (user_id, moderator_id, action, reason, duration) VALUES (%s, %s, %s, %s, %s)", (user_id, mod_id, action, reason, duration))
            conn.commit()
            return cursor.lastrowid
        finally:
            cursor.close()
            conn.close()
    return await async_db_runner(sync_op, user_id, mod_id, action, reason, duration)

async def async_get_level_config(guild_id: int) -> Optional[Dict[str, Any]]:
    """Fetches leveling config for a guild (Async)."""
    def sync_op(guild_id):
        conn = _get_sync_connection()
        if not conn: return None
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT * FROM level_config WHERE guild_id = %s", (guild_id,))
            config = cursor.fetchone()
            return config
        finally:
            cursor.close()
            conn.close()
    return await async_db_runner(sync_op, guild_id)

async def async_set_level_config(guild_id: int, key: str, value: Any):
    """Sets or updates a leveling config value for a guild (Async)."""
    def sync_op(guild_id, key, value):
        conn = _get_sync_connection()
        if not conn: return
        cursor = conn.cursor()
        try:
            cursor.execute(f"INSERT INTO level_config (guild_id, {key}) VALUES (%s, %s) ON DUPLICATE KEY UPDATE {key} = %s", (guild_id, value, value))
            conn.commit()
        finally:
            cursor.close()
            conn.close()
    await async_db_runner(sync_op, guild_id, key, value)

async def async_get_user_level(guild_id: int, user_id: int) -> Dict[str, Any]:
    """Fetches user level data for a guild (Async). Returns defaults if not found."""
    def sync_op(guild_id, user_id):
        conn = _get_sync_connection()
        if not conn: return {'xp': 0, 'level': 0, 'message_count': 0, 'last_xp_gain': None}
        cursor = conn.cursor(dictionary=True)
        try:
            cursor.execute("SELECT xp, level, message_count, last_xp_gain FROM user_levels WHERE guild_id = %s AND user_id = %s", (guild_id, user_id))
            data = cursor.fetchone()
            if data is None:
                cursor.execute("INSERT INTO user_levels (guild_id, user_id) VALUES (%s, %s)", (guild_id, user_id))
                conn.commit()
                return {'xp': 0, 'level': 0, 'message_count': 0, 'last_xp_gain': None}
            return data
        finally:
            cursor.close()
            conn.close()
    result = await async_db_runner(sync_op, guild_id, user_id)
    return result if result else {'xp': 0, 'level': 0, 'message_count': 0, 'last_xp_gain': None}

async def async_update_user_level(guild_id: int, user_id: int, xp: int = None, level: int = None, message_count: int = None, last_xp_gain: datetime.datetime = None):
    """Updates user level data (Async)."""
    def sync_op(guild_id, user_id, xp, level, message_count, last_xp_gain):
        conn = _get_sync_connection()
        if not conn: return
        cursor = conn.cursor()
        try:
            set_clause = []
            values = []
            if xp is not None:
                set_clause.append("xp = %s")
                values.append(xp)
            if level is not None:
                set_clause.append("level = %s")
                values.append(level)
            if message_count is not None:
                set_clause.append("message_count = %s")
                values.append(message_count)
            if last_xp_gain is not None:
                set_clause.append("last_xp_gain = %s")
                values.append(last_xp_gain)
            if set_clause:
                query = f"UPDATE user_levels SET {', '.join(set_clause)} WHERE guild_id = %s AND user_id = %s"
                values.extend([guild_id, user_id])
                cursor.execute(query, tuple(values))
                conn.commit()
        finally:
            cursor.close()
            conn.close()
    await async_db_runner(sync_op, guild_id, user_id, xp, level, message_count, last_xp_gain)

async def async_add_level_role(guild_id: int, level: int, role_id: int):
    """Adds or updates a level role (Async)."""
    def sync_op(guild_id, level, role_id):
        conn = _get_sync_connection()
        if not conn: return
        cursor = conn.cursor()
        try:
            cursor.execute("INSERT INTO level_roles (guild_id, level, role_id) VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE role_id = %s", (guild_id, level, role_id, role_id))
            conn.commit()
        finally:
            cursor.close()
            conn.close()
    await async_db_runner(sync_op, guild_id, level, role_id)

async def async_get_level_role(guild_id: int, level: int) -> Optional[int]:
    """Fetches role ID for a specific level (Async)."""
    def sync_op(guild_id, level):
        conn = _get_sync_connection()
        if not conn: return None
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT role_id FROM level_roles WHERE guild_id = %s AND level = %s", (guild_id, level))
            result = cursor.fetchone()
            return result[0] if result else None
        finally:
            cursor.close()
            conn.close()
    return await async_db_runner(sync_op, guild_id, level)

async def async_get_top_user(guild_id: int) -> Optional[int]:
    """Fetches the user with the highest message count (Async)."""
    def sync_op(guild_id):
        conn = _get_sync_connection()
        if not conn: return None
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT user_id FROM user_levels WHERE guild_id = %s ORDER BY message_count DESC LIMIT 1", (guild_id,))
            result = cursor.fetchone()
            return result[0] if result else None
        finally:
            cursor.close()
            conn.close()
    return await async_db_runner(sync_op, guild_id)

async def async_get_user_rank(guild_id: int, user_id: int) -> Optional[int]:
    """Fetches the rank of a user based on XP in the guild (Async)."""
    def sync_op(guild_id, user_id):
        conn = _get_sync_connection()
        if not conn: return None
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT user_id, xp FROM user_levels WHERE guild_id = %s ORDER BY xp DESC", (guild_id,))
            users = cursor.fetchall()
            for index, user in enumerate(users, start=1):
                if user[0] == user_id:
                    return index
            return None
        finally:
            cursor.close()
            conn.close()
    return await async_db_runner(sync_op, guild_id, user_id)

# --- UI Views and Modals (For Ban Appeal) ---

class BanAppealModal(Modal, title="Server Ban Appeal Form"):
    def __init__(self, guild_name: str):
        super().__init__()
        self.guild_name = guild_name
    why_unban = TextInput(
        label="Why should your ban be lifted?",
        placeholder="Explain your case, take responsibility, and show remorse.",
        style=discord.TextStyle.paragraph,
        max_length=1500,
        required=True
    )
    evidence = TextInput(
        label="Links to evidence (Optional)",
        placeholder="e.g., screenshots, video links, other evidence.",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=500
    )
    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(thinking=True, ephemeral=True)
        user = interaction.user
        appeal_embed = create_base_embed(
            "📝 NEW BAN APPEAL SUBMITTED",
            f"Appeal submitted by **{user.name}** (`{user.id}`) for server **{self.guild_name}**.",
            color=discord.Color.brand_red()
        )
        appeal_embed.set_thumbnail(url=user.display_avatar.url)
        appeal_embed.add_field(
            name="Why Ban Should Be Lifted",
            value=self.why_unban.value,
            inline=False
        )
        evidence_value = self.evidence.value if self.evidence.value else "None provided."
        appeal_embed.add_field(
            name="Evidence / Links",
            value=evidence_value,
            inline=False
        )
        appeal_embed.set_footer(text=f"Appeal for server: {self.guild_name}")
        if logging_channel_id:
            channel = bot.get_channel(logging_channel_id)
            if channel:
                try:
                    await channel.send(
                        content=f"**New Ban Appeal from:** {user.mention} (`{user.id}`)",
                        embed=appeal_embed
                    )
                except Exception as e:
                    print(f"Failed to send ban appeal to logging channel: {e}")
        await interaction.followup.send(
            embed=create_base_embed(
                "✅ Appeal Submitted",
                f"Your ban appeal for **{self.guild_name}** has been successfully submitted to the server staff for review. You will be contacted via DM if a decision is made.",
                color=discord.Color.green()
            ),
            ephemeral=True
        )

class BanAppealDMView(View):
    def __init__(self, guild_name: str):
        super().__init__(timeout=86400 * 30)
        self.guild_name = guild_name
    @discord.ui.button(label="Submit Ban Appeal", style=discord.ButtonStyle.red, emoji="🚨")
    async def appeal_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_modal(BanAppealModal(guild_name=self.guild_name))

# --- Custom Check for /config & /restart ---

def is_admin_or_creator_check():
    """Custom check that passes if the user is the bot creator OR has Administrator permission."""
    async def predicate(interaction: discord.Interaction) -> bool:
        if interaction.user.id == BOT_CREATOR_ID:
            return True
        if interaction.guild is None: 
            return False
        return interaction.user.guild_permissions.administrator
    return app_commands.check(predicate)

# --- Core Bot Class and Setup ---

class BurgentruckBot(commands.Bot):
    def __init__(self, token: str):
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        intents.guilds = True
        intents.messages = True
        super().__init__(command_prefix="!", intents=intents)
        self.token = token
        self.initial_config_loaded = False
    async def load_initial_config_and_check_db(self):
        """Loads global config and checks the database connection."""
        global logging_channel_id
        def sync_config_op():
            conn = _get_sync_connection()
            if not conn:
                raise mysql.connector.Error("Failed to establish initial database connection for config.")
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT name, value FROM bot_config")
            config = {row['name']: row['value'] for row in cursor.fetchall()}
            cursor.close()
            conn.close()
            return config
        try:
            config = await asyncio.to_thread(sync_config_op)
            db_log_id = int(config.get("LOGGING_CHANNEL_ID", 0) or 0)
            if db_log_id != 0:
                logging_channel_id = db_log_id
            print(f"Config loaded successfully: Log Channel ID={logging_channel_id}")
            return True
        except mysql.connector.Error as err:
            error_desc = f"**Error Type:** `{type(err).__name__}`\n**Message:** {err}"
            await send_log_embed(
                title="⚠️ CRITICAL DB FAILURE",
                description=f"Bot successfully logged in but **failed to connect/load configuration from the database**.\nBot will now shut down as requested.\n\n{error_desc}",
                color=discord.Color.red()
            )
            print(f"CRITICAL: DB connection failed after login. Cannot load config.\n{error_desc}")
            return False
        except Exception as e:
            print(f"Non-MySQL error during config load: {e}")
            return False
    async def on_ready(self):
        global tree
        tree = self.tree
        if not self.initial_config_loaded:
            print(f"Bot connected as {self.user} (ID: {self.user.id})")
            db_successful = await self.load_initial_config_and_check_db()
            if not db_successful:
                print("FATAL: Database failure detected during configuration load. Shutting down as requested.")
                await self.close()
                sys.exit(1)
                return
            await tree.sync()
            self.initial_config_loaded = True
            @self.tree.error
            async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
                if interaction.response.is_done():
                    send_func = interaction.followup.send
                else:
                    send_func = interaction.response.send_message
                if isinstance(error, app_commands.MissingPermissions):
                    missing_perms = [p.replace('_', ' ').title() for p in error.missing_permissions]
                    perm_list = ", ".join(missing_perms)
                    await send_func(
                        embed=create_base_embed("🚫 Access Denied", f"You need the following permission(s): **{perm_list}**.", color=discord.Color.red()),
                        ephemeral=True
                    )
                elif isinstance(error, app_commands.CheckFailure):
                    await send_func(
                        embed=create_base_embed("🚫 Access Denied", "You do not have permission to use this command.", color=discord.Color.red()),
                        ephemeral=True
                    )
                else:
                    print(f"Unhandled app command error: {error}")
                    await send_func(embed=create_base_embed("❌ Error", f"An unexpected error occurred: `{type(error).__name__}`", color=discord.Color.red()), ephemeral=True)
            await send_log_embed("🚀 Bot Operational", f"Bot is now online and running.", color=discord.Color.green())

    async def on_message(self, message: discord.Message):
        if message.author.bot or not message.guild:
            return
        config = await async_get_level_config(message.guild.id)
        if not config:
            print(f"No level config found for guild {message.guild.id}. Skipping leveling logic.")
            return
        user_id = message.author.id
        guild_id = message.guild.id
        user_data = await async_get_user_level(guild_id, user_id)
        # Check XP cooldown
        current_time = datetime.datetime.utcnow()
        cooldown_seconds = config.get('xp_cooldown_seconds', 60)  # Default to 60 if not set
        can_gain_xp = True
        if user_data['last_xp_gain']:
            last_xp_time = user_data['last_xp_gain']
            time_diff = (current_time - last_xp_time).total_seconds()
            if time_diff < cooldown_seconds:
                can_gain_xp = False
        new_message_count = user_data['message_count'] + 1
        if can_gain_xp:
            xp_gain = random.randint(config.get('xp_min', 1), config.get('xp_max', 10))
            new_xp = user_data['xp'] + xp_gain
            xp_mult = config.get('xp_multiplier', 100)
            def calc_level(xp: int) -> int:
                if xp_mult == 0:
                    return 0
                discriminant = 1 + 8 * xp / xp_mult
                return int((-1 + math.sqrt(discriminant)) / 2)
            old_level = user_data['level']
            new_level = calc_level(new_xp)
            await async_update_user_level(guild_id, user_id, xp=new_xp, level=new_level, message_count=new_message_count, last_xp_gain=current_time)
            if new_level > old_level:
                channel_id = config.get('level_up_channel_id')
                if channel_id:
                    channel = bot.get_channel(channel_id)
                    if channel:
                        msg = f"Congrats {message.author.mention}, you reached level {new_level}!"
                        roles_added = []
                        for lvl in range(old_level + 1, new_level + 1):
                            role_id = await async_get_level_role(guild_id, lvl)
                            if role_id:
                                role = message.guild.get_role(role_id)
                                if role:
                                    try:
                                        await message.author.add_roles(role)
                                        roles_added.append(f"Level {lvl} role")
                                    except:
                                        pass
                        if roles_added:
                            msg += f" Gained roles: {', '.join(roles_added)}"
                        await channel.send(msg)
        else:
            await async_update_user_level(guild_id, user_id, message_count=new_message_count)
        # Check for top message sender
        current_top_id = config.get('current_top_user_id')
        top_count = 0
        if current_top_id:
            top_data = await async_get_user_level(guild_id, current_top_id)
            top_count = top_data['message_count']
        if new_message_count > top_count:
            role_id = config.get('top_message_role_id')
            if role_id:
                role = message.guild.get_role(role_id)
                if role:
                    if current_top_id:
                        old_top = message.guild.get_member(current_top_id)
                        if old_top:
                            try:
                                await old_top.remove_roles(role)
                            except:
                                pass
                    try:
                        await message.author.add_roles(role)
                    except:
                        pass
                    await async_set_level_config(guild_id, 'current_top_user_id', user_id)
        await self.process_commands(message)

# --- Application Commands: Utility ---

@app_commands.command(name="say", description="Makes the bot send a message to a specified channel.")
@app_commands.checks.has_permissions(administrator=True)
@app_commands.describe(
    channel="The channel where the message should be sent.",
    message="The content of the message to send."
)
async def say_command(ctx: discord.Interaction, channel: discord.TextChannel, message: str):
    await ctx.response.defer(thinking=True, ephemeral=True)
    try:
        await channel.send(message)
        message_preview = message[:100] + "..." if len(message) > 100 else message
        log_desc = (
            f"**User:** {ctx.user.mention} (`{ctx.user.id}`)\n"
            f"**Target Channel:** {channel.mention}\n"
            f"**Message:** *{message_preview}*"
        )
        await send_log_embed("🗣️ /say Command Used", log_desc, discord.Color.teal())
        await ctx.followup.send(
            embed=create_base_embed("✅ Message Sent", f"Successfully sent the message to {channel.mention}.", color=discord.Color.green())
        )
    except discord.Forbidden:
        await ctx.followup.send(
            embed=create_base_embed("❌ Action Failed", f"I do not have permission to send messages in {channel.mention}.", color=discord.Color.dark_red())
        )
    except Exception as e:
        await ctx.followup.send(embed=create_base_embed("❌ Error", f"An unexpected error occurred: {e}", color=discord.Color.dark_red()))

# --- Application Commands: Moderation ---

@app_commands.command(name="ban", description="Bans a user from the server.")
@app_commands.checks.has_permissions(ban_members=True)
async def ban_command(ctx: discord.Interaction, user: discord.User, reason: str = "No reason provided", delete_days: app_commands.Range[int, 0, 7] = 0):
    await ctx.response.defer(thinking=True)
    guild = ctx.guild
    moderator = ctx.user
    try:
        delete_seconds = delete_days * 24 * 60 * 60  # Convert days to seconds
        await guild.ban(user, reason=reason, delete_message_seconds=delete_seconds)
        case_id = await async_log_case(user.id, moderator.id, "BAN", reason)
        appeal_view = BanAppealDMView(guild_name=guild.name)
        appeal_embed = create_base_embed(
            f"🚫 You Have Been Banned from {guild.name}",
            f"**Reason:** {reason}\n\nIf you believe this was in error, click the button below to submit a formal ban appeal to the staff team.",
            color=discord.Color.red()
        )
        user_message = f"{user.mention} has been banned."
        try:
            await user.send(embed=appeal_embed, view=appeal_view)
            user_message = f"{user.mention} has been banned and sent the **appeal form** via DM."
        except discord.Forbidden:
            user_message = f"{user.mention} has been banned. **Could not send appeal form via DM.**"
        log_desc = f"**User:** {user.mention} (`{user.id}`)\n**Moderator:** {moderator.mention}\n**Reason:** {reason}\n**Case ID:** `{case_id}`"
        await send_log_embed("🔨 User Banned", log_desc, discord.Color.red())
        await ctx.followup.send(
            embed=create_base_embed("✅ Ban Successful", user_message, color=discord.Color.green())
        )
    except discord.Forbidden:
        await ctx.followup.send(embed=create_base_embed("❌ Action Failed", "I do not have permissions to ban that user.", color=discord.Color.dark_red()))
    except Exception as e:
        await ctx.followup.send(embed=create_base_embed("❌ Error", f"An unexpected error occurred: {e}", color=discord.Color.dark_red()))

@app_commands.command(name="kick", description="Kicks a user from the server.")
@app_commands.checks.has_permissions(kick_members=True)
async def kick_command(ctx: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    await ctx.response.defer(thinking=True)
    guild = ctx.guild
    moderator = ctx.user
    try:
        await member.kick(reason=reason)
        case_id = await async_log_case(member.id, moderator.id, "KICK", reason)
        await send_moderation_dm(member, "Kick", guild.name, reason)
        log_desc = f"**User:** {member.mention} (`{member.id}`)\n**Moderator:** {moderator.mention}\n**Reason:** {reason}\n**Case ID:** `{case_id}`"
        await send_log_embed("👟 User Kicked", log_desc, discord.Color.orange())
        await ctx.followup.send(
            embed=create_base_embed("✅ Kick Successful", f"{member.mention} has been kicked.", color=discord.Color.green())
        )
    except discord.Forbidden:
        await ctx.followup.send(embed=create_base_embed("❌ Action Failed", "I do not have permissions to kick that user.", color=discord.Color.dark_red()))
    except Exception as e:
        await ctx.followup.send(embed=create_base_embed("❌ Error", f"An unexpected error occurred: {e}", color=discord.Color.dark_red()))

@app_commands.command(name="unban", description="Unbans a user using their user ID.")
@app_commands.checks.has_permissions(ban_members=True)
async def unban_command(ctx: discord.Interaction, user_id: str, reason: str = "No reason provided"):
    await ctx.response.defer(thinking=True)
    guild = ctx.guild
    moderator = ctx.user
    try:
        user_id_int = int(user_id)
    except ValueError:
        await ctx.followup.send(embed=create_base_embed("❌ Invalid ID", "The provided user ID must be a number.", color=discord.Color.dark_red()))
        return
    try:
        user = await bot.fetch_user(user_id_int)
    except discord.NotFound:
        user = discord.Object(id=user_id_int)
    try:
        await guild.unban(user, reason=reason)
        case_id = await async_log_case(user_id_int, moderator.id, "UNBAN", reason)
        log_desc = f"**User:** {user.mention if hasattr(user, 'mention') else user_id_int} (`{user_id_int}`)\n**Moderator:** {moderator.mention}\n**Reason:** {reason}\n**Case ID:** `{case_id}`"
        await send_log_embed("✅ User Unbanned", log_desc, discord.Color.green())
        await ctx.followup.send(
            embed=create_base_embed("✅ Unban Successful", f"User ID `{user_id}` has been unbanned.", color=discord.Color.green())
        )
    except discord.NotFound:
        await ctx.followup.send(embed=create_base_embed("❌ Action Failed", f"User ID `{user_id}` is not currently banned or could not be found.", color=discord.Color.dark_red()))
    except discord.Forbidden:
        await ctx.followup.send(embed=create_base_embed("❌ Action Failed", "I do not have permissions to unban that user.", color=discord.Color.dark_red()))
    except Exception as e:
        await ctx.followup.send(embed=create_base_embed("❌ Error", f"An unexpected error occurred: {e}", color=discord.Color.dark_red()))

@app_commands.command(name="mute", description="Mutes/timeouts a member for a specified number of minutes (max 28 days).")
@app_commands.checks.has_permissions(moderate_members=True)
async def mute_command(ctx: discord.Interaction, member: discord.Member, duration_minutes: app_commands.Range[int, 1, 40320], reason: str = "No reason provided"):
    await ctx.response.defer(thinking=True)
    guild = ctx.guild
    moderator = ctx.user
    duration = datetime.timedelta(minutes=duration_minutes)
    duration_str = f"{duration_minutes} minutes"
    try:
        await member.timeout(duration, reason=reason)
        case_id = await async_log_case(member.id, moderator.id, "MUTE", reason, duration_str)
        await send_moderation_dm(member, "Mute (Timeout)", guild.name, reason, duration_str)
        log_desc = (
            f"**User:** {member.mention} (`{member.id}`)\n"
            f"**Moderator:** {moderator.mention}\n"
            f"**Duration:** {duration_str}\n"
            f"**Reason:** {reason}\n"
            f"**Case ID:** `{case_id}`"
        )
        await send_log_embed("🔇 User Muted (Timeout)", log_desc, discord.Color.dark_orange())
        await ctx.followup.send(
            embed=create_base_embed("✅ Mute Successful", f"{member.mention} has been muted for {duration_str}.", color=discord.Color.green())
        )
    except discord.Forbidden:
        await ctx.followup.send(embed=create_base_embed("❌ Action Failed", "I do not have permissions to mute that user.", color=discord.Color.dark_red()))
    except Exception as e:
        await ctx.followup.send(embed=create_base_embed("❌ Error", f"An unexpected error occurred: {e}", color=discord.Color.dark_red()))

@app_commands.command(name="unmute", description="Unmutes/removes timeout from a member.")
@app_commands.checks.has_permissions(moderate_members=True)
async def unmute_command(ctx: discord.Interaction, member: discord.Member, reason: str = "No reason provided"):
    await ctx.response.defer(thinking=True)
    guild = ctx.guild
    moderator = ctx.user
    is_timed_out = member.timeout is not None and member.timeout > discord.utils.utcnow()
    if not is_timed_out:
        await ctx.followup.send(embed=create_base_embed("⚠️ Action Failed", f"{member.mention} is not currently timed out (muted).", color=discord.Color.orange()))
        return
    try:
        await member.timeout(None, reason=reason)
        case_id = await async_log_case(member.id, moderator.id, "UNMUTE", reason)
        await send_moderation_dm(member, "Unmute", guild.name, reason)
        log_desc = (
            f"**User:** {member.mention} (`{member.id}`)\n"
            f"**Moderator:** {moderator.mention}\n"
            f"**Reason:** {reason}\n"
            f"**Case ID:** `{case_id}`"
        )
        await send_log_embed("🔊 User Unmuted (Timeout Removed)", log_desc, discord.Color.green())
        await ctx.followup.send(
            embed=create_base_embed("✅ Unmute Successful", f"{member.mention}'s timeout has been removed.", color=discord.Color.green())
        )
    except discord.Forbidden:
        await ctx.followup.send(embed=create_base_embed("❌ Action Failed", "I do not have permissions to remove timeout from that user.", color=discord.Color.dark_red()))
    except Exception as e:
        await ctx.followup.send(embed=create_base_embed("❌ Error", f"An unexpected error occurred: {e}", color=discord.Color.dark_red()))

@app_commands.command(name="warn", description="Issues a formal warning to a member.")
@app_commands.checks.has_permissions(moderate_members=True)
async def warn_command(ctx: discord.Interaction, member: discord.Member, reason: str):
    await ctx.response.defer(thinking=True)
    guild = ctx.guild
    moderator = ctx.user
    try:
        case_id = await async_log_case(member.id, moderator.id, "WARN", reason)
        await send_moderation_dm(member, "Warning", guild.name, reason)
        log_desc = (
            f"**User:** {member.mention} (`{member.id}`)\n"
            f"**Moderator:** {moderator.mention}\n"
            f"**Reason:** {reason}\n"
            f"**Case ID:** `{case_id}`"
        )
        await send_log_embed("⚠️ User Warned", log_desc, discord.Color.gold())
        await ctx.followup.send(
            embed=create_base_embed("✅ Warning Issued", f"{member.mention} has been warned. Case ID: `{case_id}`", color=discord.Color.green())
        )
    except Exception as e:
        await ctx.followup.send(embed=create_base_embed("❌ Error", f"An unexpected error occurred: {e}", color=discord.Color.dark_red()))

@app_commands.command(name="cases", description="Displays all moderation logs for a user.")
@app_commands.checks.has_permissions(moderate_members=True)
async def cases_command(ctx: discord.Interaction, user: discord.User):
    await ctx.response.defer(thinking=True)
    logs = await async_get_user_caselogs(user.id)
    if not logs:
        embed = create_base_embed("✅ Case Logs", f"{user.mention} has no recorded moderation cases.", color=discord.Color.green())
        await ctx.followup.send(embed=embed)
        return
    description_parts = []
    for log in logs:
        moderator_mention = f"<@{log['moderator_id']}>"
        try:
            timestamp_str = log['timestamp'].strftime("%Y-%m-%d %H:%M UTC")
        except:
            timestamp_str = "Unknown Time"
        duration_info = f" | **Duration:** {log['duration']}" if log['duration'] else ""
        reason_display = log['reason'][:50] + "..." if len(log['reason']) > 50 else log['reason']
        case_entry = (
            f"**Case ID:** `{log['id']}` | **Action:** `{log['action']}`{duration_info}\n"
            f"**Moderator:** {moderator_mention}\n"
            f"**Reason:** *{reason_display}*\n"
            f"**Date:** {timestamp_str}\n"
        )
        description_parts.append(case_entry)
    display_logs = description_parts[:10]
    more_info = f"\n...and **{len(logs) - 10}** more old cases not shown." if len(logs) > 10 else ""
    embed = create_base_embed(
        f"📋 Case Logs for {user.name}",
        f"Showing {len(display_logs)} of {len(logs)} total cases.\n\n" + "\n".join(display_logs) + more_info,
        color=discord.Color.blue()
    )
    embed.set_thumbnail(url=user.display_avatar.url)
    await ctx.followup.send(embed=embed)

# --- Utility Commands (/ping, /help, /userinfo, /dashboard) ---

@app_commands.command(name="ping", description="Checks the bot's latency (speed).")
async def ping_command(ctx: discord.Interaction):
    latency_ms = round(bot.latency * 1000)
    embed = create_base_embed(
        "🏓 Pong!",
        f"Latency: **{latency_ms}ms**",
        color=discord.Color.gold() if latency_ms > 100 else discord.Color.green()
    )
    await ctx.response.send_message(embed=embed, ephemeral=True)

@app_commands.command(name="userinfo", description="Displays detailed information about a user.")
async def userinfo_command(ctx: discord.Interaction, user: Optional[discord.User] = None):
    await ctx.response.defer(thinking=True)
    target = user or ctx.user
    embed = create_base_embed(f"👤 User Info: {target.name}", color=target.color if isinstance(target, discord.Member) and target.color != discord.Color.default() else discord.Color.blue())
    embed.set_thumbnail(url=target.display_avatar.url)
    embed.add_field(name="ID", value=f"`{target.id}`", inline=False)
    embed.add_field(name="Account Created", value=discord.utils.format_dt(target.created_at, "R"), inline=True)
    if isinstance(target, discord.Member) and target.guild:
        status = "Normal Member"
        if target.id == BOT_CREATOR_ID:
            status = "Bot Creator 👑"
        elif target.id == target.guild.owner_id:
            status = "Server Owner 🌟"
        elif target.guild_permissions.administrator:
            status = "Server Administrator ✨"
        embed.add_field(name="Server Status", value=status, inline=True)
        embed.add_field(name="Joined Server", value=discord.utils.format_dt(target.joined_at, "R"), inline=True)
        roles = [role.mention for role in target.roles if role.name != "@everyone"]
        roles_value = ", ".join(roles) if roles else "None"
        embed.add_field(name=f"Roles ({len(roles)})", value=roles_value, inline=False)
    else:
        embed.add_field(name="Member Status", value="Not a member of this server.", inline=True)
    await ctx.followup.send(embed=embed)

@app_commands.command(name="help", description="Shows the list of commands and features.")
async def help_command(ctx: discord.Interaction):
    embed = create_base_embed(
        "📚 Burgentruck Bot Help",
        "Here is an overview of the bot's functionality. Use the slash command `/` to see all available commands.",
        color=discord.Color.blurple()
    )
    embed.add_field(
        name="🛡️ Moderation", 
        value="`/ban`, `/unban`, `/kick`, `/mute`, `/unmute`, `/warn`, `/cases`",
        inline=False
    )
    embed.add_field(
        name="⚙️ Utility & Config",
        value="`/config`, `/ping`, `/userinfo`, `/dashboard`, `/say`, `/restart` (Admin/Creator only)",
        inline=False
    )
    embed.add_field(
        name="📈 Leveling",
        value="`/level add_xp`, `/level remove_xp`, `/level set_role`, `/level set_xp_range`, `/level set_xp_multiplier`, `/level set_xp_cooldown`, `/level set_level_up_channel`, `/level set_top_role`, `/level update_top`, `/level rank`",
        inline=False
    )
    await ctx.response.send_message(embed=embed, ephemeral=True)

@app_commands.command(name="dashboard", description="Provides the link to the web dashboard.")
async def dashboard_command(ctx: discord.Interaction):
    embed = create_base_embed(
        "🌐 Web Dashboard",
        f"Manage your server settings, view advanced logs, and customize the bot using our web dashboard. [Click Here to Access]({DASHBOARD_URL})",
        color=discord.Color.teal()
    )
    await ctx.response.send_message(embed=embed)

# --- Configuration & Utility Commands ---

@app_commands.command(name="config", description="View or set bot configuration values.")
@is_admin_or_creator_check()
@app_commands.describe(
    action="The configuration action to perform.",
    key="The configuration key (e.g., LOGGING_CHANNEL_ID).",
    value="The new value for the key."
)
async def config_command(
    ctx: discord.Interaction, 
    action: str = "view",
    key: Optional[str] = None, 
    value: Optional[str] = None
):
    global logging_channel_id
    await ctx.response.defer(thinking=True, ephemeral=True)
    if action.lower() == "view":
        current_config = await async_db_runner(fetch_bot_config)
        if current_config is None:
            await ctx.followup.send(embed=create_base_embed("❌ Error", "Could not fetch configuration. Database connection failed.", color=discord.Color.red()), ephemeral=True)
            return
        config_str = "\n".join([f"**{k}:** `{v}`" for k, v in current_config.items()])
        config_str = config_str if config_str else "No configuration keys found in the database."
        config_str += f"\n\n**Current Runtime Logging ID:** `{logging_channel_id}`"
        embed = create_base_embed("⚙️ Bot Configuration", config_str, color=discord.Color.blue())
        await ctx.followup.send(embed=embed, ephemeral=True)
    elif action.lower() == "set":
        if not key or not value:
            await ctx.followup.send(embed=create_base_embed("❌ Missing Parameters", "Please provide a `key` and a `value` to set configuration.", color=discord.Color.red()), ephemeral=True)
            return
        key = key.upper().strip()
        value = value.strip()
        await async_set_bot_config(key, value)
        if key == "LOGGING_CHANNEL_ID":
            try:
                logging_channel_id = int(value)
            except ValueError:
                pass
        log_desc = f"**Key:** `{key}`\n**New Value:** `{value}`\n**Moderator:** {ctx.user.mention}"
        await send_log_embed("⚙️ Config Updated", log_desc, discord.Color.orange())
        await ctx.followup.send(embed=create_base_embed("✅ Configuration Set", f"Key `{key}` has been set to `{value}`. Global variables updated.", color=discord.Color.green()), ephemeral=True)
    else:
        await ctx.followup.send(embed=create_base_embed("❌ Invalid Action", "Valid actions are `view` or `set`.", color=discord.Color.red()), ephemeral=True)

@app_commands.command(name="restart", description="Restarts the bot (Bot Creator or Admin only).")
@is_admin_or_creator_check()
async def restart_command(ctx: discord.Interaction):
    await ctx.response.defer(thinking=True)
    log_desc = f"Restart requested by {ctx.user.mention} (`{ctx.user.id}`)."
    await send_log_embed("🔄 Bot Restarting", log_desc, discord.Color.red())
    await ctx.followup.send(embed=create_base_embed("🔄 Restarting...", "The bot is shutting down now and should restart shortly.", color=discord.Color.red()))
    await bot.close()
    sys.exit(0)

# --- Leveling Commands ---

level_group = app_commands.Group(name="level", description="Leveling system commands")

@level_group.command(name="add_xp", description="Add XP to a user.")
@is_admin_or_creator_check()
@app_commands.describe(member="The member to add XP to.", amount="The amount of XP to add.")
async def add_xp(ctx: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 1000000]):
    await ctx.response.defer(thinking=True)
    guild_id = ctx.guild.id
    user_id = member.id
    config = await async_get_level_config(guild_id)
    if not config:
        await ctx.followup.send(embed=create_base_embed("❌ Error", "Leveling system not configured for this server.", color=discord.Color.red()))
        return
    user_data = await async_get_user_level(guild_id, user_id)
    old_level = user_data['level']
    new_xp = user_data['xp'] + amount
    xp_mult = config.get('xp_multiplier', 100)
    def calc_level(xp: int) -> int:
        if xp_mult == 0:
            return 0
        discriminant = 1 + 8 * xp / xp_mult
        return int((-1 + math.sqrt(discriminant)) / 2)
    new_level = calc_level(new_xp)
    await async_update_user_level(guild_id, user_id, xp=new_xp, level=new_level)
    if new_level > old_level:
        channel_id = config.get('level_up_channel_id')
        if channel_id:
            channel = bot.get_channel(channel_id)
            if channel:
                msg = f"Congrats {member.mention}, you reached level {new_level} via admin add!"
                roles_added = []
                for lvl in range(old_level + 1, new_level + 1):
                    role_id = await async_get_level_role(guild_id, lvl)
                    if role_id:
                        role = ctx.guild.get_role(role_id)
                        if role:
                            try:
                                await member.add_roles(role)
                                roles_added.append(f"Level {lvl} role")
                            except:
                                pass
                if roles_added:
                    msg += f" Gained roles: {', '.join(roles_added)}"
                await channel.send(msg)
    await ctx.followup.send(embed=create_base_embed("✅ XP Added", f"Added {amount} XP to {member.mention}. New level: {new_level}.", color=discord.Color.green()))

@level_group.command(name="remove_xp", description="Remove XP from a user.")
@is_admin_or_creator_check()
@app_commands.describe(member="The member to remove XP from.", amount="The amount of XP to remove.")
async def remove_xp(ctx: discord.Interaction, member: discord.Member, amount: app_commands.Range[int, 1, 1000000]):
    await ctx.response.defer(thinking=True)
    guild_id = ctx.guild.id
    user_id = member.id
    config = await async_get_level_config(guild_id)
    if not config:
        await ctx.followup.send(embed=create_base_embed("❌ Error", "Leveling system not configured for this server.", color=discord.Color.red()))
        return
    user_data = await async_get_user_level(guild_id, user_id)
    old_level = user_data['level']
    new_xp = max(0, user_data['xp'] - amount)
    xp_mult = config.get('xp_multiplier', 100)
    def calc_level(xp: int) -> int:
        if xp_mult == 0:
            return 0
        discriminant = 1 + 8 * xp / xp_mult
        return int((-1 + math.sqrt(discriminant)) / 2)
    new_level = calc_level(new_xp)
    await async_update_user_level(guild_id, user_id, xp=new_xp, level=new_level)
    if new_level < old_level:
        for lvl in range(new_level + 1, old_level + 1):
            role_id = await async_get_level_role(guild_id, lvl)
            if role_id:
                role = ctx.guild.get_role(role_id)
                if role:
                    try:
                        await member.remove_roles(role)
                    except:
                        pass
    await ctx.followup.send(embed=create_base_embed("✅ XP Removed", f"Removed {amount} XP from {member.mention}. New level: {new_level}.", color=discord.Color.green()))

@level_group.command(name="set_role", description="Set a role for a specific level.")
@is_admin_or_creator_check()
@app_commands.describe(level="The level.", role="The role to assign at that level.")
async def set_level_role(ctx: discord.Interaction, level: app_commands.Range[int, 1, 100], role: discord.Role):
    await ctx.response.defer(thinking=True)
    await async_add_level_role(ctx.guild.id, level, role.id)
    await ctx.followup.send(embed=create_base_embed("✅ Role Set", f"Set {role.mention} for level {level}.", color=discord.Color.green()))

@level_group.command(name="set_xp_range", description="Set the random XP range per message.")
@is_admin_or_creator_check()
@app_commands.describe(min_xp="Minimum XP per message.", max_xp="Maximum XP per message.")
async def set_xp_range(ctx: discord.Interaction, min_xp: app_commands.Range[int, 1, 100], max_xp: app_commands.Range[int, 1, 100]):
    await ctx.response.defer(thinking=True)
    if min_xp > max_xp:
        await ctx.followup.send(embed=create_base_embed("❌ Error", "Minimum XP cannot be greater than maximum XP.", color=discord.Color.red()))
        return
    await async_set_level_config(ctx.guild.id, 'xp_min', min_xp)
    await async_set_level_config(ctx.guild.id, 'xp_max', max_xp)
    await ctx.followup.send(embed=create_base_embed("✅ Config Updated", f"XP range set to {min_xp}-{max_xp} per message.", color=discord.Color.green()))

@level_group.command(name="set_xp_multiplier", description="Set XP multiplier for level ups.")
@is_admin_or_creator_check()
@app_commands.describe(multiplier="The XP multiplier (XP to next level = (current_level + 1) * multiplier).")
async def set_xp_multiplier(ctx: discord.Interaction, multiplier: app_commands.Range[int, 1, 1000]):
    await ctx.response.defer(thinking=True)
    await async_set_level_config(ctx.guild.id, 'xp_multiplier', multiplier)
    await ctx.followup.send(embed=create_base_embed("✅ Config Updated", f"XP multiplier set to {multiplier}.", color=discord.Color.green()))

@level_group.command(name="set_xp_cooldown", description="Set the XP gain cooldown in seconds.")
@is_admin_or_creator_check()
@app_commands.describe(seconds="The cooldown duration in seconds (minimum 10).")
async def set_xp_cooldown(ctx: discord.Interaction, seconds: app_commands.Range[int, 10, 3600]):
    await ctx.response.defer(thinking=True)
    await async_set_level_config(ctx.guild.id, 'xp_cooldown_seconds', seconds)
    await ctx.followup.send(embed=create_base_embed("✅ Config Updated", f"XP cooldown set to {seconds} seconds.", color=discord.Color.green()))

@level_group.command(name="set_level_up_channel", description="Set channel for level up messages.")
@is_admin_or_creator_check()
@app_commands.describe(channel="The channel for level up notifications.")
async def set_level_up_channel(ctx: discord.Interaction, channel: discord.TextChannel):
    await ctx.response.defer(thinking=True)
    await async_set_level_config(ctx.guild.id, 'level_up_channel_id', channel.id)
    await ctx.followup.send(embed=create_base_embed("✅ Config Updated", f"Level up channel set to {channel.mention}.", color=discord.Color.green()))

@level_group.command(name="set_top_role", description="Set role for the user with most messages.")
@is_admin_or_creator_check()
@app_commands.describe(role="The special role for top message sender.")
async def set_top_role(ctx: discord.Interaction, role: discord.Role):
    await ctx.response.defer(thinking=True)
    await async_set_level_config(ctx.guild.id, 'top_message_role_id', role.id)
    await ctx.followup.send(embed=create_base_embed("✅ Config Updated", f"Top message role set to {role.mention}.", color=discord.Color.green()))

@level_group.command(name="update_top", description="Update the top message sender role.")
@is_admin_or_creator_check()
async def update_top(ctx: discord.Interaction):
    await ctx.response.defer(thinking=True)
    guild_id = ctx.guild.id
    config = await async_get_level_config(guild_id)
    if not config or not config.get('top_message_role_id'):
        await ctx.followup.send(embed=create_base_embed("❌ Error", "Top role not configured.", color=discord.Color.red()))
        return
    top_user_id = await async_get_top_user(guild_id)
    if not top_user_id:
        await ctx.followup.send(embed=create_base_embed("❌ Error", "No users found.", color=discord.Color.red()))
        return
    current_top_id = config.get('current_top_user_id')
    role = ctx.guild.get_role(config['top_message_role_id'])
    if role:
        if current_top_id:
            old_top = ctx.guild.get_member(current_top_id)
            if old_top:
                try:
                    await old_top.remove_roles(role)
                except:
                    pass
        top_member = ctx.guild.get_member(top_user_id)
        if top_member:
            try:
                await top_member.add_roles(role)
            except:
                pass
    await async_set_level_config(guild_id, 'current_top_user_id', top_user_id)
    await ctx.followup.send(embed=create_base_embed("✅ Updated", f"Top role assigned to <@{top_user_id}>.", color=discord.Color.green()))

@level_group.command(name="rank", description="Displays your current level and rank in the server.")
@app_commands.describe(user="The user to check (defaults to you).")
async def rank_command(ctx: discord.Interaction, user: Optional[discord.User] = None):
    await ctx.response.defer(thinking=True)
    target = user or ctx.user
    guild_id = ctx.guild.id
    user_data = await async_get_user_level(guild_id, target.id)
    if not user_data:
        await ctx.followup.send(embed=create_base_embed("❌ Error", f"No level data found for {target.mention}.", color=discord.Color.red()))
        return
    rank = await async_get_user_rank(guild_id, target.id)
    rank_str = f"#{rank}" if rank else "Unranked"
    embed = create_base_embed(
        f"📈 Rank for {target.name}",
        f"**Level:** {user_data['level']}\n**XP:** {user_data['xp']}\n**Messages Sent:** {user_data['message_count']}\n**Rank:** {rank_str}",
        color=discord.Color.blue()
    )
    embed.set_thumbnail(url=target.display_avatar.url)
    await ctx.followup.send(embed=embed)

# --- Final Setup and Run ---

setup_database_schema()
BOT_TOKEN = fetch_bot_token()

if __name__ == "__main__":
    bot = BurgentruckBot(token=BOT_TOKEN)
    bot.tree.add_command(ban_command)
    bot.tree.add_command(kick_command)
    bot.tree.add_command(unban_command)
    bot.tree.add_command(mute_command)
    bot.tree.add_command(unmute_command)
    bot.tree.add_command(warn_command)
    bot.tree.add_command(cases_command)
    bot.tree.add_command(config_command)
    bot.tree.add_command(restart_command)
    bot.tree.add_command(ping_command)
    bot.tree.add_command(userinfo_command)
    bot.tree.add_command(help_command)
    bot.tree.add_command(dashboard_command)
    bot.tree.add_command(say_command)
    bot.tree.add_command(level_group)
    try:
        bot.run(BOT_TOKEN)
    except discord.errors.LoginFailure as e:
        print(f"CRITICAL: Failed to log in. Check your BOT_TOKEN.\nError: {e}")
    except Exception as e:
        print(f"An unexpected error occurred during bot runtime: {e}")
