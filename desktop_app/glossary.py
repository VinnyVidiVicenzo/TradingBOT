"""Plain-English definitions, looked up by tooltip.py at hover time.

Looked up by term at the moment a tooltip is shown, not when a screen is
built, so editing a definition here needs no other change anywhere in the app.
"""

from __future__ import annotations

DEFINITIONS: dict[str, str] = {
    "swing practice": "Your separate pretend swing wallet, with a fixed active model. New candidates learn and are tested separately without changing that wallet or its history.",
    "practice agent": "Watch current public prices with a saved $200 pretend wallet. After Start, the independent worker continues its test rules with the window closed while the computer is awake. Pause stops decisions. It cannot use real money.",
    # -- the three ways money can go in ------------------------------------
    "put it all in at once": (
        "Every dollar buys coins on the very first day, all in one go."
    ),
    "spread it over 12 weeks": (
        "The money is split into 12 equal buys, one per week, over about three months."
    ),
    "add a little every month": (
        "No lump sum up front -- instead, a twelfth of the total is added and "
        "spent once a month, for a year."
    ),
    # -- the result table --------------------------------------------------
    "starting month": (
        "Each try starts on the 1st of a month. Trying every possible month "
        "shows how much your result depends on luck of timing."
    ),
    "worst starting month": (
        "The starting month that ended up the lowest. It shows how badly "
        "unlucky timing could have gone."
    ),
    "typical result": (
        "The middle result: half of the starting months ended higher than "
        "this, and half ended lower."
    ),
    "best starting month": (
        "The starting month that ended up the highest. Nobody could have known "
        "in advance which month it would be, so don't read it as a promise."
    ),
    "lost money": (
        "How many starting months ended with less than the amount put in."
    ),
    "doubled your money or better": (
        "How many starting months ended with at least twice the amount put in."
    ),
    "fell by half or more at some point": (
        "How many starting months, at some point during the hold, saw the "
        "value drop to half of an earlier high -- even if it recovered later."
    ),
    "just keeping it as cash": (
        "What you'd have if you simply kept the money as cash the whole time. "
        "Here it never grows or shrinks (a savings account would pay a little "
        "interest, which isn't counted)."
    ),
    # -- the comparison table's columns ------------------------------------
    "your plan": (
        "The plan you picked, tried from every starting month. It includes "
        "assumed trading costs."
    ),
    "stock market fund": (
        "A fund holding the 500 biggest US companies (the S&P 500), with "
        "dividends added back. It is bought on the same days as your plan. No "
        "trading costs or taxes are counted."
    ),
    "savings account": (
        "The same money in a savings account paying the 3-month US Treasury "
        "rate, earning interest from the day after each amount goes in. Real savings "
        "accounts usually pay a little less."
    ),
    # -- the map legend ----------------------------------------------------
    "doubled or better": "Ended with at least twice the money put in.",
    "made money": (
        "Ended with more than was put in, but less than double (or exactly even)."
    ),
    "lost some": "Ended with less than was put in, but at least half of it.",
    "lost over half": "Ended with less than half of what was put in.",
    # -- the plan form -----------------------------------------------------
    "how much money?": (
        "Practice money only: nothing is bought or sold. Amounts are in USDT, a "
        "coin designed to always be worth one US dollar (it has drifted up to "
        "about 3% away from that)."
    ),
    "which coins?": (
        "These are coins that still exist today. Coins that disappeared aren't "
        "in the saved data, so results look better than real life would have "
        "been. Hover a coin for a line about it."
    ),
    "how should it go in?": (
        "Three ways to put the same money to work. Hover each option to see "
        "what it means."
    ),
    "how long would you hold it?": (
        "Each try starts on the 1st of a month and lasts this long, then the "
        "result is counted. Longer holds leave fewer possible starting months."
    ),
    "1 year": (
        "Each try lasts one year from its starting month. This leaves the most "
        "starting months to look at."
    ),
    "2 years": (
        "Each try lasts two years from its starting month. Fewer starting "
        "months are possible than with one year."
    ),
    "3 years": (
        "Each try lasts three years from its starting month. Fewer starting "
        "months are possible than with two years."
    ),
    "see how it would have gone": (
        "Runs your plan from the 1st of every month the saved prices allow. It "
        "takes a second or two. Nothing is bought."
    ),
    # -- the coins ---------------------------------------------------------
    "bitcoin": "The first and largest coin, created in 2009.",
    "ethereum": (
        "One of the largest coins. It can also run programs and other coins on "
        "its network."
    ),
    "solana": (
        "A newer coin (launched in 2020). Its price has swung far more than "
        "Bitcoin's, both up and down."
    ),
    "xrp": (
        "A coin tied to the company Ripple. Binance.US stopped trading it for "
        "about two and a half years during a lawsuit, so its saved prices have "
        "a hole and fewer starting months can be tried with it."
    ),
    "dogecoin": "Started as a joke in 2013 and is still widely traded.",
    "bnb": (
        "The coin of the Binance family of exchanges, so its value is tied to "
        "one company."
    ),
    "zcash": (
        "A privacy-focused coin. The research found very little real use "
        "compared with its price."
    ),
    # -- navigation and other screens ----------------------------------------
    "home": "The start page: what this app is, and where to go.",
    "what we've found so far": (
        "Plain-language write-ups of everything the research found. Nothing is "
        "calculated here; it only shows what was already written."
    ),
    "try your own plan": (
        "Pick an amount, some coins and a way of putting the money in, and see "
        "how it would have gone from every possible starting month."
    ),
    "your past plans": "The plans you've already tried in this app, newest first.",
    "safety check": (
        "A scan of the actual code on this computer for anything that could "
        "place an order or move money."
    ),
    "check now": "Checks selected source files for known forbidden text patterns. It does not prove every possible behavior or check account permissions.",
}


def define(term: str) -> str:
    return DEFINITIONS.get(
        term.strip().lower(), "(no plain-English definition written yet for this term)"
    )
