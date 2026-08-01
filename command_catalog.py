from dataclasses import dataclass


@dataclass(frozen=True)
class CommandSpec:
    name: str
    description: str
    usage: str
    category: str
    handler: str
    aliases: tuple[str, ...] = ()
    menu: bool = True
    private_only: bool = False

    @property
    def names(self) -> tuple[str, ...]:
        return (self.name, *self.aliases)


COMMANDS = (
    CommandSpec("start", "Start WRK$", "/start", "Start", "cmd_start", menu=False, private_only=True),
    CommandSpec("app", "Open the WRK$ mini-app", "/app", "Start", "cmd_app"),
    CommandSpec("help", "Browse all economy commands", "/help", "Start", "cmd_help"),
    CommandSpec("wallet", "Open your game token wallet", "/wallet", "Wallet & market", "cmd_wallet"),
    CommandSpec("coins", "View the simulated coin market", "/coins", "Wallet & market", "cmd_coins"),
    CommandSpec("balance", "Check your fictional WRK$ balance", "/balance", "Wallet & profile", "cmd_balance", ("bal",)),
    CommandSpec("daily", "Claim your daily WRK$", "/daily", "Wallet & profile", "cmd_daily"),
    CommandSpec("profile", "View a player profile", "/profile [@user]", "Wallet & profile", "cmd_profile", ("p",)),
    CommandSpec("flex", "Flex your WRK$ profile in chat", "/flex [@user]", "Wallet & profile", "cmd_flex"),
    CommandSpec("leaderboard", "Open the WRK$ leaderboards", "/leaderboard", "Wallet & profile", "cmd_leaderboard", ("lb",)),
    CommandSpec("give", "Send fictional WRK$ to a player", "/give @user <amount>", "Wallet & profile", "cmd_give"),
    CommandSpec("work", "Start a tap-to-earn shift", "/work", "Earn & crime", "cmd_work"),
    CommandSpec("jobs", "View jobs and progression", "/jobs", "Earn & crime", "cmd_jobs"),
    CommandSpec("workreminder", "Toggle shift-ready reminders", "/workreminder", "Earn & crime", "cmd_workreminder"),
    CommandSpec("rob", "Attempt an in-game wallet robbery", "/rob @user · or reply /rob", "Earn & crime", "cmd_rob"),
    CommandSpec("hack", "Start an in-game wallet puzzle", "/hack [@user] · or reply /hack", "Earn & crime", "cmd_hack"),
    CommandSpec("guess", "Answer your active hack puzzle", "/guess <word>", "Earn & crime", "cmd_guess", menu=False),
    CommandSpec("slots", "Play slots", "/slots <bet|all>", "Games", "cmd_slots"),
    CommandSpec("coinflip", "Flip for WRK$", "/coinflip <bet|all> [heads|tails]", "Games", "cmd_coinflip", ("cf",)),
    CommandSpec("dice", "Roll against the house", "/dice <bet|all>", "Games", "cmd_dice"),
    CommandSpec("blackjack", "Play blackjack", "/blackjack <bet|all>", "Games", "cmd_blackjack", ("bj",)),
    CommandSpec("crash", "Join multiplayer crash", "/crash <bet|all>", "Games", "cmd_crash"),
    CommandSpec("cashout", "Cash out of crash", "/cashout", "Games", "cmd_cashout"),
    CommandSpec("inventory", "Browse your gift inventory", "/inventory", "Collectibles", "cmd_inventory", ("inv",)),
    CommandSpec("gift", "Show one of your gifts", "/gift <collection> <number>", "Collectibles", "cmd_gift"),
    CommandSpec("pin", "Pin a gift to your profile", "/pin", "Collectibles", "cmd_pin"),
    CommandSpec("shop", "Browse the WRK$ shops", "/shop", "Collectibles", "cmd_shop", ("market",)),
    CommandSpec("buy", "Buy a bank gift", "/buy <collection> <number>", "Collectibles", "cmd_buy"),
    CommandSpec("sell", "Sell one of your gifts", "/sell <collection> <number>", "Collectibles", "cmd_sell"),
    CommandSpec("offer", "Offer WRK$ for another gift", "/offer @user <amount> for <collection> <number>", "Collectibles", "cmd_offer"),
    CommandSpec("offers", "View pending gift offers", "/offers", "Collectibles", "cmd_offers"),
)


PUBLIC_COMMANDS = tuple(
    (command.name, command.description) for command in COMMANDS if command.menu
)


def build_help_text() -> str:
    sections: list[str] = [
        "🎮 *WRK$ Economy Game*",
        "WRK$, GRAM, and memecoin balances are fictional game assets. Memecoin prices move through simulated STONK.fi liquidity pools; only the GRAM/USD cash-out anchor is live.",
    ]
    categories: list[str] = []
    for command in COMMANDS:
        if command.category != "Start" and command.category not in categories:
            categories.append(command.category)
    for category in categories:
        lines = [f"*{category}*"]
        for command in COMMANDS:
            if command.category == category:
                lines.append(f"`{command.usage}` — {command.description}")
        sections.append("\n".join(lines))
    sections.append("Open `/app` for games, shops, profiles, your wallet, and STONK.fi swaps.")
    return "\n\n".join(sections)
