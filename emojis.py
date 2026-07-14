def tge(emoji_id: str, fallback: str) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'

WRK_SYMBOL_ID     = "5093898072811898950"
BADGE_OWNER_ID    = "5093738587791296253"
BADGE_PLUSH1_ID   = "5093839648371771344"
BADGE_ECOADMIN_ID = "5093713423577909638"
BADGE_ADMIN_ID    = "5093944660322158424"

# Bot messages — plain text/unicode (bots can't use arbitrary custom emoji IDs)
WRK          = "WRK$"
BADGE_OWNER  = "👑"
BADGE_PLUSH1 = "🐸"
BADGE_ECOADMIN = "🛡"
BADGE_ADMIN  = "⚔️"

BADGE_MAP = {
    "owner":        BADGE_OWNER,
    "ecoadmin":     BADGE_ECOADMIN,
    "admin":        BADGE_ADMIN,
    "plush_pepe_1": BADGE_PLUSH1,
}
