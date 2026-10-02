import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'benchmark'))
import frame_model_draw_api as api
import eu4_frame_model as model

def _api_row(name, **extra):
    core=api.canonical(name)
    row={'name':name,'canonical':core,'id':api.IDS.get(core,99),'exported':True,'imported':True,
         'reachability':'reachable','observer':'import and dlsym','suppression':core in api.SUPPRESSED}
    row.update(extra)
    return row

class DrawCoverageTests(unittest.TestCase):
    def fixture(self):
        manifest={'schema':2,'apis':[
            _api_row('glDrawArrays'),
            _api_row('glDrawArraysARB',reachability='candidate',imported=False,suppression=True)],
            'resolver_gaps':[]}
        frames=[{'phase':phase,'measurement_epoch':7,'update_id':update,'thread_id':11,'draws':10} for update,phase in ((1,1),(2,4))]
        def record(kind,frame,*payload):
            return list(map(str,[kind,*payload,*([0]*(12-len(payload))),7,frame['update_id'],frame['phase'],11,9,0,0]))
        rows=[]
        for f in frames:
            rows.extend([record('K',f,1),record('A',f,3,10,10 if f['phase']==4 else 0,0 if f['phase']==4 else 10)])
        return manifest,frames,rows

    def test_complete_coverage_and_alias_canonicalization(self):
        m,f,r=self.fixture()
        self.assertEqual(api.canonical('glDrawArraysInstancedARB'),'glDrawArraysInstanced')
        self.assertEqual(api.coverage(m,f,r,{1,4})['status'],'passed')
        self.assertEqual(api.coverage(m,f[:1],r,{1},require_c=False)['stage'],'pre-C')
        self.assertEqual(api.coverage(m,f[:1],r,{1})['status'],'failed')

    def test_candidate_export_only_does_not_block_coverage(self):
        m,f,r=self.fixture()
        m['apis'].append({'name':'glMultiDrawElementsIndirectAPPLE','canonical':'glMultiDrawElementsIndirect',
            'id':7,'exported':True,'imported':False,'reachability':'candidate','observer':'uncovered','suppression':False})
        self.assertEqual(api.coverage(m,f,r,{1,4})['status'],'passed')

    def test_reachable_or_unresolved_uncovered_still_fails(self):
        m,f,r=self.fixture()
        m['apis'].append({'name':'glMultiDrawElements','canonical':'glMultiDrawElements','id':7,'exported':True,'imported':False,
            'reachability':'reachable','observer':'uncovered','suppression':False})
        self.assertEqual(api.coverage(m,f,r,{1,4})['status'],'failed')
        m['apis'][-1].update(reachability='unresolved')
        self.assertEqual(api.coverage(m,f,r,{1,4})['status'],'failed')

    def test_legacy_observers_report_partial_suppression(self):
        m,f,r=self.fixture();m['apis'][0]['suppression']=False;m['apis'][1]['suppression']=False
        self.assertEqual(api.coverage(m,f,r,{1,4})['reason'],'baseline/phase submissions outside suppression set')

    def test_resolver_gaps_and_missing_runtime_markers_block(self):
        m,f,r=self.fixture();m['resolver_gaps']=['NSLookupSymbolInImage']
        self.assertEqual(api.coverage(m,f,r,{1,4})['status'],'failed')
        m['resolver_gaps']=[]
        self.assertEqual(api.coverage(m,f,r+[['R','99','2']],{1,4})['status'],'failed')
        self.assertEqual(api.coverage(m,f,[row for row in r if row[0]!='K'],{1,4})['status'],'unavailable')
        self.assertEqual(api.coverage(None,f,r,{1,4})['status'],'unavailable')

    def test_count_reconciliation_and_forwarded_C_draws(self):
        m,f,r=self.fixture();r[-1][3]='9';r[-1][4]='1'
        self.assertEqual(api.coverage(m,f,r,{1,4})['status'],'failed')
        r[-1][4]='0'
        self.assertIn('inconsistent',api.coverage(m,f,r,{1,4})['reason'])
        r[-1][3]='10';f[1]['draws']=11
        self.assertIn('differ',api.coverage(m,f,r,{1,4})['reason'])

    def test_candidate_classifier_excludes_buffer_selectors(self):
        for n in ('glDrawRangeElements','glMultiDrawElements','glDrawArraysIndirect','glBegin','glCallList','glRectfv','glVertex2f','glBitmap','glCopyPixels'):
            self.assertTrue(api.candidate(n))
        for n in ('glDrawBuffers','glDrawBuffersARB','glDrawBuffer','glBeginQuery','glVertexAttribPointer'):
            self.assertFalse(api.candidate(n))

    def test_claimed_complete_C_gate_is_recomputed_for_old_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            (root/'telemetry.csv').write_text('H,3\n')
            (root/'manifest.json').write_text(json.dumps({'format_version':3,'status':'complete',
                'phases':[],'gates':{'draw_api_coverage':{'status':'passed','reason':'claimed'}}}))
            report=model.analyze(root)
            self.assertEqual(report['release_gates']['draw_api_coverage']['status'],'unavailable')
            self.assertEqual(report['C_intervention'],'partial draw-suppression intervention')

    def test_incomplete_shadow_excludes_structural_claims_but_keeps_CPU(self):
        f={k:0 for k in model.FRAME_FIELDS};f.update(update_id=1,phase=1,measurement_epoch=7,start_ns=110,end_ns=150,flags=2048)
        d=['D','1']+['0']*11+['7','1','1','11','9','3','99']
        q=['Q','1','1','-1','0','1','40','30','40','30','7','0','0','11','9','3']
        rows=model.eligible_trace([d,q],[f],[{'name':'A0','measurement_epoch':7,'start_ns':100,'end_ns':200}])
        self.assertEqual(rows,[q])
