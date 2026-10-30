"""Integration tests: run in WSL after pip install -r requirements.txt.
Only uses temporary SQLite in an isolated APP_ENV=test process.
"""
import os,sys,tempfile,unittest
from pathlib import Path
os.environ.pop('SUPABASE_DB_URL',None)  # NEVER let tests connect to the volunteer database
os.environ['APP_ENV']='test';os.environ['D2S_TEST_ALLOW_SQLITE']='1';
_temp=tempfile.TemporaryDirectory()
os.environ['DATABASE_URL']='sqlite:///'+str(Path(_temp.name)/'test.db')
os.environ['SECRET_KEY']='test-only-secret-should-not-be-used-in-prod'
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import app,DB,Participant,Drawing,StudyEnrollment


def sample(off=0):
    return [[[20+off+i*10,110-65*(1-((i-8)/8)**2)] for i in range(17)]]

def dyn(s):return {'times':[[float(i*10) for i in range(len(stroke))] for stroke in s], 'pointer_type':'mouse'}

class HttpTest(unittest.TestCase):
    def setUp(self):
        self.c=app.test_client();self.c.get('/api/state')
    def post(self,path,p):
        with self.c.session_transaction() as sess:csrf=sess['csrf']
        return self.c.post(path,json=p,headers={'X-D2S-CSRF':csrf})
    def test_full_guided_protocol(self):
        r=self.post('/api/start',{'consent':True,'share_reference':False})
        self.assertEqual(r.status_code,201,r.get_json())
        self.assertEqual(r.get_json()['study']['phase'],'practice')
        code=r.get_json()['recovery_code']
        phase_counts=[('practice',2),('enrollment',8),('owner_immediate',6),('near_miss',3),('true_wrong_shape',2)]
        for phase,count in phase_counts:
            for i in range(count):
                s=sample(i*.1)
                z=self.post('/api/drawing',{'strokes':s,'dynamics':dyn(s),
                     'phase':phase,'step':i+1,'category':'informed_forgery'}) # browser cannot pick category
                self.assertEqual(z.status_code,200,z.get_json())
        with app.app_context():
            self.assertEqual(DB().query(StudyEnrollment).count(),1)
            rows=DB().query(Drawing).all()
            self.assertEqual(len(rows),21)
            self.assertEqual(sum(d.category=='enrollment' for d in rows),8)
            self.assertEqual(sum(d.category=='owner_test' for d in rows),6)
            self.assertTrue(all(d.result is not None for d in rows if d.category=='owner_test'))
        z=self.c.get('/api/state').get_json()
        self.assertEqual(z['study']['phase'],'complete') # No consenting external target yet
        # Pseudonymous return code allows recovery on a different browser.
        other=app.test_client();other.get('/api/state')
        with other.session_transaction() as sess:csrf=sess['csrf']
        r=other.post('/api/resume',json={'code':code},headers={'X-D2S-CSRF':csrf})
        self.assertEqual(r.status_code,200)
    def test_no_public_dev(self):
        self.assertEqual(self.c.get('/dev').status_code,404)
    def test_refuse_without_consent(self):
        self.assertEqual(self.post('/api/start',{'consent':False}).status_code,400)

if __name__=='__main__':unittest.main()
