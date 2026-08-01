import ast
from pathlib import Path

from bot import _PUBLIC_COMMANDS
from command_catalog import COMMANDS, build_help_text


_REMOVED_GROUP_ADMIN_COMMANDS = {
    "mute", "dmute", "unmute", "ban", "dban", "unban", "kick", "dkick",
    "warn", "dwarn", "warns", "resetwarns", "report", "purge", "promote",
    "demote", "setup", "setlog", "setbottopic", "clearbottopic", "admins",
    "rules", "setrules", "dlog", "cleanservice", "setwelcome", "setgoodbye",
    "welcome", "goodbye", "givehalo", "removehalo", "halos", "exportsettings",
    "importsettings", "inactives", "connect", "addautoreply", "removeautoreply",
    "autoreplies", "setflood", "setfloodaction", "antiflood", "addblocked",
    "removeblocked", "blocklist", "setblocklistaction", "lock", "unlock",
    "locks", "antiraid", "setantiraid",
}


def _registered_commands():
    commands = [name for command in COMMANDS for name in command.names]
    tree = ast.parse(Path("bot.py").read_text())
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "CommandHandler"
            and node.args
            and isinstance(node.args[0], ast.Constant)
        ):
            commands.append(node.args[0].value)
    return commands


def test_command_registration_is_unique_and_within_telegram_limit():
    commands = _registered_commands()
    assert len(commands) == len(set(commands))
    assert len(commands) <= 100


def test_published_command_menu_only_contains_working_commands():
    registered = set(_registered_commands())
    published = [command for command, _description in _PUBLIC_COMMANDS]
    assert len(published) == len(set(published))
    assert set(published) <= registered
    assert {"app", "help", "wallet", "coins", "balance", "profile", "flex", "shop"} <= set(published)


def test_help_is_generated_from_every_public_command():
    help_text = build_help_text()
    for command in COMMANDS:
        if command.category != "Start":
            assert f"`{command.usage}`" in help_text


def test_telegram_menu_descriptions_fit_platform_limits():
    assert all(1 <= len(description) <= 256 for _command, description in _PUBLIC_COMMANDS)


def test_group_admin_commands_are_not_registered_or_published():
    assert _REMOVED_GROUP_ADMIN_COMMANDS.isdisjoint(_registered_commands())
    assert _REMOVED_GROUP_ADMIN_COMMANDS.isdisjoint(
        command for command, _description in _PUBLIC_COMMANDS
    )
