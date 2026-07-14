def tge(emoji_id: str, fallback: str) -> str:
    return f'<tg-emoji emoji-id="{emoji_id}">{fallback}</tg-emoji>'

WRK_SYMBOL_ID     = "5093898072811898950"
BADGE_OWNER_ID    = "5093738587791296253"
BADGE_PLUSH1_ID   = "5093839648371771344"
BADGE_ECOADMIN_ID = "5093713423577909638"
BADGE_ADMIN_ID    = "5093944660322158424"

WRK           = tge(WRK_SYMBOL_ID,      "💰")
BADGE_OWNER   = tge(BADGE_OWNER_ID,     "👑")
BADGE_PLUSH1  = tge(BADGE_PLUSH1_ID,    "🐸")
BADGE_ECOADMIN = tge(BADGE_ECOADMIN_ID, "🛡")
BADGE_ADMIN   = tge(BADGE_ADMIN_ID,     "⚔️")

BADGE_MAP = {
    "owner":        BADGE_OWNER,
    "ecoadmin":     BADGE_ECOADMIN,
    "admin":        BADGE_ADMIN,
    "plush_pepe_1": BADGE_PLUSH1,
}
