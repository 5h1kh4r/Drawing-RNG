import unittest
from drawing_rng.study_v24 import make_references,evaluate,dtw_distance,VERSION

def arch(dx=0,dy=0):
    return [[[15+dx+i*3,85+dy-55*(1-((i-12)/12)**2)] for i in range(25)]]
def two_arches():
    return [arch()[0],[[x+120,y] for x,y in arch()[0]]]
class V24Test(unittest.TestCase):
    def test_8_references(self):
        samples=[arch(i*.3) for i in range(8)]
        ref=make_references(samples)
        self.assertEqual(len(ref['references']),3)
        self.assertEqual(len(ref['reference_indices']),3)
        self.assertTrue(evaluate(ref,arch())['accepted'])
        self.assertEqual(evaluate(ref,arch())['algorithm_version'],VERSION)
    def test_same_is_zero_dtw(self):
        self.assertAlmostEqual(dtw_distance(arch(),arch()),0)
    def test_dtw_unequal_stroke_count(self):
        self.assertIsNone(dtw_distance(arch(),two_arches()))
    def test_invalid_shape(self):
        ref=make_references([arch() for _ in range(8)])
        self.assertFalse(evaluate(ref,two_arches())['accepted'])
if __name__=='__main__':unittest.main()
