"""Stripe Connect refund support.

Design: docs/06_integrations/stripe_connector_design.md. NOT owner-ratified
end to end - see that document's Sign-off section, still blank at the time
this package was created (2026-09-18). Every check the design gate lists as
open (Q2.1h, Q2.1i, Q3.0b sign-off, 7.5's tables, Q2.1k) is implemented here
as SPECIFIED, but implementation is not the same act as ratification.

This package contains the refund executor (`actions.py`), which performs the
one externally-mutating Stripe verb this design authorizes: creating a
refund on a direct charge, through the platform secret key plus a
`Stripe-Account` header, never a per-tenant bearer token (design 4.0.1).
"""
