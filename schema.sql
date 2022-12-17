 
CREATE TABLE dm_flow (
    user_id BIGINT PRIMARY KEY,
    dms_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    dm_channel BIGINT NULL,
    dm_webhook TEXT NULL
);


CREATE TABLE custom_commands(
    command_string TEXT PRIMARY KEY,
    description TEXT,
    command_content TEXT,
    embed JSONB,
    aliases_to TEXT REFERENCES custom_commands.command_string ON DELETE CASCADE
);