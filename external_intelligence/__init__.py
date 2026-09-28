"""Outside market intelligence, as research input only.

This package is the fourth zone of the repository. Its claim is the same as the
research engine's, and it is enforced the same way (``safety.py``):

    reads and analyses external data only -- cannot authenticate, cannot trade,
    cannot move funds.

The architecture is a one-way pipeline::

    external data -> normalized events -> features -> research / statistical
    testing -> (validated) strategy logic -> independent risk controls -> paper
    execution

Only the first four stages live here. Nothing in this package turns a whale
alert, a flow, a funding rate or a liquidation into a buy or sell, and nothing in
it can reach an order: it imports no execution, portfolio, strategy or
account-tools module (a test walks every import to prove it).

Everything a provider sends is untrusted data. It is parsed into typed values or
quarantined; it is never evaluated, never used to build a path or query, and
never treated as an instruction.
"""

__version__ = "1.0.0"
