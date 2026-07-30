import ast
from pathlib import Path

from bot import _PUBLIC_COMMANDS


def _registered_commands():
    tree = ast.parse(Path("bot.py").read_text())
    commands = []
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
    assert {"app", "help", "balance", "profile", "shop"} <= set(published)

