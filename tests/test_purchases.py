"""Purchases: order emails, not a store's marketing, answer questions about
what the user bought. All data is invented."""

import json
import sqlite3

import pytest

from services.ingestion.kinds import email_kind
from services.ingestion.store import SCHEMA_VERSION, Store
from services.retrieval.ask import answer_prompt, format_sources, gather
from services.retrieval.router import validate

from .conftest import FakeEmbedder
from .test_email_dates import TODAY, TZ
from .test_folders import RouteChat, synced_store


@pytest.mark.parametrize("subject, body, kind", [
    ("Your Northwind Goods order has shipped", "Order #NW-11873. Items: desk lamp. Unsubscribe", "order"),
    ("Your receipt from Hilltop Hardware", "Deck stain $64.99", "order"),
    ("Package update", "UPS tracking number 1Z999AA10123456784", "order"),
    ("New desk lamps", "New arrivals, 20% off. Shop now. Unsubscribe", "marketing"),
    ("Thanksgiving?", "At your place this year?", "other"),
    ("Weekly garden tips", "Plant garlic now. Unsubscribe", "other"),  # a newsletter, not selling
])
def test_email_kind(subject, body, kind):
    assert email_kind(subject, body) == kind


def test_promotions_label_marks_marketing_but_orders_stay_orders():
    assert email_kind("Garden tips", "Plant garlic", ["Inbox", "Promotions"]) == "marketing"
    assert email_kind("Order shipped", "Order #A12345 shipped", ["Promotions"]) == "order"


def test_sync_records_kinds_and_the_promotions_label(gmail_messages, event_fixtures):
    store, _ = synced_store(gmail_messages, event_fixtures)
    kinds = store.kinds_for([("gmail", i) for i in ("m-order-new", "m-order-old", "m-receipt", "m-lamp-promo",
                                                     "m-newsletter", "m-sister")])
    assert [kinds[("gmail", i)] for i in ("m-order-new", "m-order-old", "m-receipt", "m-lamp-promo",
                                          "m-newsletter", "m-sister")] == \
        ["order", "order", "order", "marketing", "marketing", "other"]  # newsletter: Promotions label


def test_purchase_question_lists_the_actual_orders_first(gmail_messages, event_fixtures):
    store, index = synced_store(gmail_messages, event_fixtures)
    chat = RouteChat({"sources": ["email"], "start_date": None, "end_date": None, "calendar_keywords": [],
                      "mail_folder": None, "about_purchases": True})
    context = gather("when will my brass desk lamp arrive", FakeEmbedder(), index, chat, 3, [], TODAY, TZ, store)
    orders = context.sections[0]
    assert orders.startswith("The user's recent orders, shipments, deliveries, and receipts")
    assert context.order_rows[0]["email_id"] == "m-order-new"
    assert "m-lamp-promo" not in [r["email_id"] for r in context.order_rows]
    assert "Brass desk lamp" in orders  # the item list, past the boilerplate
    # In the topic search the marketing email, though the closest match on words, comes after orders.
    kinds = [h["kind"] for h in context.hits]
    assert kinds.index("order") < kinds.index("marketing") if "marketing" in kinds else True
    assert "Orders:" in format_sources(context, TZ)
    assert "marketing about similar products" in answer_prompt("q", context, TODAY)[0]


def test_other_questions_keep_relevance_so_marketing_can_answer(gmail_messages, event_fixtures):
    """"What's on sale?" is answered by the marketing email."""
    store, index = synced_store(gmail_messages, event_fixtures)
    chat = RouteChat({"sources": ["email"], "start_date": None, "end_date": None, "calendar_keywords": [],
                      "mail_folder": None, "about_purchases": False})
    context = gather("new arrivals desk lamps 20% off shop now", FakeEmbedder(), index, chat, 3, [], TODAY, TZ,
                     store)
    assert "m-lamp-promo" in [h["email_id"] for h in context.hits]
    assert context.order_rows == []


def test_router_purchases_flag_implies_email_and_never_general():
    r = validate(json.dumps({"sources": ["general"], "start_date": None, "end_date": None,
                             "calendar_keywords": [], "mail_folder": None, "about_purchases": True}))
    assert r.purchases and r.sources == ["email"]
    assert not validate('{"sources": ["email"]}').purchases


def test_version_6_store_gets_kinds_backfilled(tmp_path):
    path = tmp_path / "structured.db"
    store = Store(path)
    store.db.execute("insert into emails (account, id, subject, body, date, folders) values "
                     "('gmail', 'o', 'Your order has shipped', 'Order #AB12345', '2026-09-24T10:00:00-04:00', '[]'),"
                     "('gmail', 'p', 'Big sale', 'Tips', '2026-09-24T10:00:00-04:00', '[\"Promotions\"]')")
    store.db.execute("alter table emails drop column kind")
    store.db.execute("pragma user_version = 6")
    store.db.commit()
    store.db.close()
    upgraded = Store(path)
    assert not upgraded.rebuilt and upgraded.db.execute("pragma user_version").fetchone()[0] == SCHEMA_VERSION
    assert upgraded.kinds_for([("gmail", "o"), ("gmail", "p")]) == {("gmail", "o"): "order", ("gmail", "p"): "marketing"}
