"""The user's slash commands, in one list: /help prints it, the startup panel counts it, and
the system prompt names them so the agent can point the user to the right one."""

COMMANDS = [
    ("/help", "this"),
    ("/status", "model, host, context used, sandbox, reviewer"),
    ("/sessions", "saved sessions in this directory, numbered"),
    ("/resume [n|id]", "switch to one (no argument: pick from a list)"),
    ("/undo", "take back the file changes of the last turn that made any"),
    ("/reset", "start a new session (the old one stays saved)"),
    ("/note <text>", "keep a remark about this session (for fixing things later)"),
    ("/services", "servers the agent started; /services stop <name>"),
    ("/expand", "the last result shown cut short, in full (also Ctrl+O)"),
    ("/messages", "the raw history, as sent to the model"),
    ("/exit", "quit (also Ctrl+D)"),
]
