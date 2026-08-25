# -*- coding: utf-8 -*-
import time
import unittest

from cards import CARDS
from server import Game, ROLE_HONEST, answer_variants


class AnswerVariantTests(unittest.TestCase):
    def test_story_cards_offer_fact_preserving_variants(self):
        story_cards = [
            card for card in CARDS
            if card["category"] in ("歷史事件", "神話童話")
        ]
        varied = 0
        for card in story_cards:
            variants = answer_variants(card)
            self.assertEqual(variants[0], card["description"])
            if len(variants) > 1:
                varied += 1
                self.assertEqual(len(variants), 3)
        self.assertGreaterEqual(varied, 150)

    def test_honest_player_gets_round_variant_but_result_is_complete(self):
        card = next(
            card for card in CARDS
            if card["category"] == "歷史事件" and len(answer_variants(card)) > 1
        )
        game = Game()
        game.players = [{"id": "honest", "name": "A", "avatar": "🐰", "color": "#fff", "score": 0}]
        game.card = card
        game.answer_text = answer_variants(card)[-1]
        game.phase = "reading"
        game.roles = {"honest": ROLE_HONEST}
        game.reading_ends_at = time.time() + 60

        reading_view = game.view_for("honest")
        self.assertEqual(reading_view["card"]["description"], game.answer_text)

        game.phase = "result"
        result_view = game.view_for("honest")
        self.assertEqual(result_view["card"]["description"], card["description"])


if __name__ == "__main__":
    unittest.main()
