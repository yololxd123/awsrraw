import importlib.util
from pathlib import Path
import unittest
spec=importlib.util.spec_from_file_location('motion',Path(__file__).resolve().parents[1]/'python/mouse_motion.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)

class MotionTests(unittest.TestCase):
    def test_floor_survives_fast_settings(self):
        self.assertGreater(m.travel_seconds(200,100,.031),.20)
        self.assertGreater(m.travel_seconds(200,100,.5),.5)
        self.assertNotEqual(m.travel_seconds(200,100,.031),m.travel_seconds(200,100,.032))
    def test_resampling_is_bounded_and_keeps_endpoints(self):
        path=m.trajectory([(0,10,0),(10,0,10),(11,20,150)],.4)
        self.assertEqual(path[0],(0,0,10))
        self.assertEqual(path[-1],(.4,11,20))
        self.assertTrue(all(0<=x<=11 and 0<=y<=20 for _,x,y in path))
        self.assertTrue(all(0<b[0]-a[0]<=.008001 for a,b in zip(path,path[1:])))
    def test_empty_and_stationary(self):
        self.assertEqual(m.trajectory([],1),[])
        self.assertTrue(all(x==2 and y==3 for _,x,y in m.trajectory([(2,3,0),(2,3,0)],.1)))
