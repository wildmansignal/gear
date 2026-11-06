"""One-time: create the Gear Pro product, prices and a Payment Link in Stripe.

Usage:  STRIPE_SECRET_KEY=sk_live_... python stripe_setup.py [--monthly 39] [--yearly 390]
Prints the payment link URLs to paste into the sales page, and the webhook
endpoint to register (Developers → Webhooks → Add endpoint).
"""
import argparse, os, sys
import stripe

p = argparse.ArgumentParser()
p.add_argument("--monthly", type=float, default=39)
p.add_argument("--yearly", type=float, default=390)
p.add_argument("--public-url", default=os.environ.get("PUBLIC_URL", "https://gear.167-172-142-0.sslip.io"))
a = p.parse_args()

stripe.api_key = os.environ.get("STRIPE_SECRET_KEY") or sys.exit("set STRIPE_SECRET_KEY")
product = stripe.Product.create(name="Gear Pro", description="AI model access inside Gear, metered monthly. Cancel anytime.")
monthly = stripe.Price.create(product=product.id, unit_amount=int(a.monthly * 100), currency="usd", recurring={"interval": "month"})
yearly = stripe.Price.create(product=product.id, unit_amount=int(a.yearly * 100), currency="usd", recurring={"interval": "year"})
success = f"{a.public_url}/activate?session_id={{CHECKOUT_SESSION_ID}}"
links = {}
for label, price in (("monthly", monthly), ("yearly", yearly)):
    link = stripe.PaymentLink.create(
        line_items=[{"price": price.id, "quantity": 1}],
        after_completion={"type": "redirect", "redirect": {"url": success}},
        allow_promotion_codes=True,
    )
    links[label] = link.url
print("product:", product.id)
print("monthly price:", monthly.id, "->", links["monthly"])
print("yearly price:", yearly.id, "->", links["yearly"])
print("\nRegister a webhook at:", f"{a.public_url}/stripe/webhook")
print("events: checkout.session.completed, invoice.paid, customer.subscription.updated, customer.subscription.deleted")
print("then put the signing secret in server/.env as STRIPE_WEBHOOK_SECRET")
