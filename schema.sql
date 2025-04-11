 
CREATE TABLE IF NOT EXISTS modmail (
    user_id BIGINT PRIMARY KEY,
    channel_id BIGINT NULL
);


CREATE TABLE IF NOT EXISTS custom_commands(
    command_string TEXT PRIMARY KEY,
    description TEXT,
    command_content TEXT,
    embed JSONB,
    aliases_to TEXT REFERENCES custom_commands(command_string) ON DELETE CASCADE
);