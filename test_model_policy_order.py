"""Display/worker order and fallback-tier integration, without model calls."""
import unittest
from model_policy import effective_order, candidate_models

class SharedOrderTests(unittest.TestCase):
    def test_family_order_and_tier(self):
        ids=['gemini-4-argon-medium','gemini-4-argon-high','gemini-3.8-flash-low','gemini-3.8-flash-high','claude-sonnet-5-5-medium']
        snap={'health':{'models':[{'id':x} for x in ids]},'model_order':[]}
        effective=effective_order(snap)
        self.assertEqual(effective[:2],['gemini-4-argon-high','gemini-4-argon-medium'])
        self.assertEqual(candidate_models(snap),['gemini-4-argon-high','gemini-3.8-flash-high','claude-sonnet-5-5-medium'])
        snap['model_order']=['gemini-3.8-flash-low','gemini-3.8-flash-high','gemini-4-argon-medium','gemini-4-argon-high']
        self.assertEqual(candidate_models(snap)[:2],['gemini-3.8-flash-low','gemini-4-argon-medium'])
        self.assertEqual(candidate_models(snap,allowed=['gemini-4-argon-high','gemini-3.8-flash-high'])[:2],['gemini-4-argon-high','gemini-3.8-flash-high'])
        snap['health']['account']={'state':'known','quota':{'gemini-5h':{'remaining_fraction':0}}}
        self.assertEqual(candidate_models(snap),['claude-sonnet-5-5-medium'])
