"""Bounded cache refresh without relabeling old source data as new."""
import unittest
from unittest.mock import patch
import remote_data as r

class Response:
    def __init__(self,value):self.value=value
    def raise_for_status(self):pass
    def json(self):return self.value

class JsonCacheTests(unittest.TestCase):
    def setUp(self):r._cache.clear();r._cache_nonce=0;self.calls=[]
    def get(self,url,**kwargs):self.calls.append((url,kwargs));return Response({'checked_at':'2026-10-03T19:49:00Z'})
    def tearDown(self):r._cache.clear();r._cache_nonce=0
    def test_cache_hit_then_bounded_new_url(self):
        with patch.object(r.time,'time',return_value=600):
            a=r.fetch_json('research_status.json','runtime-state',ttl=60,get=self.get)
        with patch.object(r.time,'time',return_value=659):r.fetch_json('research_status.json','runtime-state',ttl=60,get=self.get)
        self.assertEqual(len(self.calls),1)
        with patch.object(r.time,'time',return_value=661):
            b=r.fetch_json('research_status.json','runtime-state',ttl=60,get=self.get)
        self.assertEqual(len(self.calls),2);self.assertNotEqual(self.calls[0][0],self.calls[1][0])
        self.assertEqual(a,b);self.assertEqual(b['checked_at'],'2026-10-03T19:49:00Z')
        self.assertEqual(self.calls[-1][1]['headers']['Cache-Control'],'no-cache')
    def test_future_cache_time_and_manual_refresh_bypass_old_entries(self):
        with patch.object(r.time,'time',return_value=600):r.fetch_json('x.json',get=self.get)
        with patch.object(r.time,'time',return_value=500):r.fetch_json('x.json',get=self.get)
        self.assertEqual(len(self.calls),2)
        previous=self.calls[-1][0]
        with patch.object(r.time,'time_ns',return_value=123456):r.clear_json_cache()
        with patch.object(r.time,'time',return_value=500):r.fetch_json('x.json',get=self.get)
        self.assertEqual(len(self.calls),3);self.assertNotEqual(previous,self.calls[-1][0])
    def test_failed_refresh_does_not_fall_back_to_cached_old_data(self):
        with patch.object(r.time,'time',return_value=600):r.fetch_json('x.json',get=self.get)
        def bad(*args,**kwargs):raise TimeoutError('offline')
        with patch.object(r.time,'time',return_value=1000):self.assertIsNone(r.fetch_json('x.json',get=bad))

if __name__=='__main__':unittest.main()
