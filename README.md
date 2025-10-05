# Discordbot1x1x1x1
for my discord bot

if you want to use this bot in your own server make sure you replace the hardcoded channel and bot token, also make sure you have a functioning sql data base with these tabels (sql queries are provided bellow)
the bot has basic moderation, support and leveling commands
-- --------------------------------------
-- Database Schema Creation Queries
-- These queries create the necessary tables if they do not exist.
-- Run once during bot startup in the `setup_database_schema` function.
-- --------------------------------------

-- Create bot_config table
-- Purpose: Stores key-value pairs for bot configuration (e.g., LOGGING_CHANNEL_ID).
CREATE TABLE IF NOT EXISTS bot_config (
    name VARCHAR(255) PRIMARY KEY,
    value TEXT
);

-- Create case_logs table
-- Purpose: Stores moderation actions (e.g., bans, kicks, mutes) for auditing.
CREATE TABLE IF NOT EXISTS case_logs (
    `id` INT AUTO_INCREMENT PRIMARY KEY,
    `user_id` BIGINT NOT NULL,
    `moderator_id` BIGINT NOT NULL,
    `action` VARCHAR(50) NOT NULL,
    `reason` TEXT,
    `duration` VARCHAR(50) DEFAULT NULL,
    `timestamp` TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Create user_levels table
-- Purpose: Stores user XP, level, message count, and last XP gain timestamp for leveling system.
CREATE TABLE IF NOT EXISTS user_levels (
    guild_id BIGINT NOT NULL,
    user_id BIGINT NOT NULL,
    xp INT DEFAULT 0,
    level INT DEFAULT 0,
    message_count INT DEFAULT 0,
    last_xp_gain TIMESTAMP NULL,
    PRIMARY KEY (guild_id, user_id)
);

-- Create level_config table
-- Purpose: Stores guild-specific leveling configuration (XP range, multiplier, cooldown, channels, roles).
CREATE TABLE IF NOT EXISTS level_config (
    guild_id BIGINT PRIMARY KEY,
    xp_min INT DEFAULT 1,
    xp_max INT DEFAULT 10,
    xp_multiplier INT DEFAULT 100,
    xp_cooldown_seconds INT DEFAULT 60,
    level_up_channel_id BIGINT,
    top_message_role_id BIGINT,
    current_top_user_id BIGINT
);

-- Create level_roles table
-- Purpose: Maps levels to role IDs for automatic role assignment.
CREATE TABLE IF NOT EXISTS level_roles (
    guild_id BIGINT NOT NULL,
    level INT NOT NULL,
    role_id BIGINT NOT NULL,
    PRIMARY KEY (guild_id, level)
);

-- --------------------------------------
-- Runtime Queries
-- These queries are used during bot operation for reading and writing data.
-- Executed asynchronously via the `async_db_runner` function.
-- --------------------------------------

-- Fetch Bot Configuration
-- Purpose: Retrieves all configuration key-value pairs.
-- Used by: fetch_bot_config, /config view
SELECT name, value FROM bot_config;

-- Fetch User Case Logs
-- Purpose: Retrieves moderation history for a specific user.
-- Used by: async_get_user_caselogs, /cases command
-- Parameters: user_id (BIGINT)
SELECT `id`, `action`, `reason`, `duration`, `moderator_id`, `timestamp`
FROM case_logs
WHERE `user_id` = %s
ORDER BY `id` DESC;

-- Set Bot Configuration
-- Purpose: Inserts or updates a configuration key-value pair.
-- Used by: async_set_bot_config, /config set
-- Parameters: name (VARCHAR), value (TEXT)
INSERT INTO bot_config (name, value)
VALUES (%s, %s)
ON DUPLICATE KEY UPDATE value = VALUES(value);

-- Log Moderation Case
-- Purpose: Logs a moderation action (e.g., ban, kick, mute).
-- Used by: async_log_case, /ban, /kick, /mute, /unmute, /warn, /unban
-- Parameters: user_id (BIGINT), moderator_id (BIGINT), action (VARCHAR), reason (TEXT), duration (VARCHAR, nullable)
INSERT INTO case_logs (user_id, moderator_id, action, reason, duration)
VALUES (%s, %s, %s, %s, %s);

-- Fetch Level Config
-- Purpose: Retrieves leveling configuration for a guild.
-- Used by: async_get_level_config, /level commands, on_message
-- Parameters: guild_id (BIGINT)
SELECT * FROM level_config WHERE guild_id = %s;

-- Set Level Config
-- Purpose: Inserts or updates a leveling configuration value for a guild.
-- Used by: async_set_level_config, /level set_xp_range, /level set_xp_multiplier, /level set_xp_cooldown, /level set_level_up_channel, /level set_top_role
-- Parameters: guild_id (BIGINT), value (varies by column), column_name (dynamic)
INSERT INTO level_config (guild_id, %s) VALUES (%s, %s)
ON DUPLICATE KEY UPDATE %s = %s;

-- Fetch User Level Data
-- Purpose: Retrieves XP, level, message count, and last XP gain for a user in a guild.
-- Used by: async_get_user_level, /level add_xp, /level remove_xp, on_message
-- Parameters: guild_id (BIGINT), user_id (BIGINT)
SELECT xp, level, message_count, last_xp_gain FROM user_levels WHERE guild_id = %s AND user_id = %s;

-- Initialize User Level Data
-- Purpose: Creates a new user level entry if it doesn't exist.
-- Used by: async_get_user_level, on_message
-- Parameters: guild_id (BIGINT), user_id (BIGINT)
INSERT INTO user_levels (guild_id, user_id) VALUES (%s, %s);

-- Update User Level Data
-- Purpose: Updates XP, level, message count, or last XP gain for a user.
-- Used by: async_update_user_level, /level add_xp, /level remove_xp, on_message
-- Parameters: xp (INT, nullable), level (INT, nullable), message_count (INT, nullable), last_xp_gain (TIMESTAMP, nullable), guild_id (BIGINT), user_id (BIGINT)
UPDATE user_levels SET xp = %s, level = %s, message_count = %s, last_xp_gain = %s WHERE guild_id = %s AND user_id = %s;

-- Add Level Role
-- Purpose: Assigns a role to a specific level in a guild.
-- Used by: async_add_level_role, /level set_role
-- Parameters: guild_id (BIGINT), level (INT), role_id (BIGINT)
INSERT INTO level_roles (guild_id, level, role_id) VALUES (%s, %s, %s)
ON DUPLICATE KEY UPDATE role_id = %s;

-- Fetch Level Role
-- Purpose: Retrieves the role ID for a specific level in a guild.
-- Used by: async_get_level_role, on_message, /level add_xp
-- Parameters: guild_id (BIGINT), level (INT)
SELECT role_id FROM level_roles WHERE guild_id = %s AND level = %s;

-- Fetch Top User
-- Purpose: Retrieves the user with the highest message count in a guild.
-- Used by: async_get_top_user, /level update_top
-- Parameters: guild_id (BIGINT)
SELECT user_id FROM user_levels WHERE guild_id = %s ORDER BY message_count DESC LIMIT 1;

-- Fetch User Rank
-- Purpose: Retrieves the rank of a user based on XP in a guild.
-- Used by: async_get_user_rank, /level rank
-- Parameters: guild_id (BIGINT), user_id (BIGINT)
SELECT user_id, xp FROM user_levels WHERE guild_id = %s ORDER BY xp DESC;
