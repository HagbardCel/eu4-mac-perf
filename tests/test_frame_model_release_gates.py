import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'benchmark'))
import eu4_frame_model as model
from frame_model_gates import GateEvidence,REQUIRED,run_budget
from frame_model_workload import paired_summary

class ReleaseTests(unittest.TestCase):
    def test_missing_evidence_fails_closed(self):
        gates=GateEvidence()
        self.assertEqual(set(gates.blockers()),set(REQUIRED))
        with self.assertRaises(ValueError): gates.require()
        for name in REQUIRED: gates.record(name,'passed','test evidence')
        gates.require()
        gates.record('semantic_coverage','failed','Render residual 70%')
        with self.assertRaises(ValueError): gates.require()
        recommendation=model.derive_recommendations([],{}, {},{},gates.entries)[0]
        self.assertEqual(recommendation['status'],'blocked')
        self.assertIn('semantic_coverage',recommendation['evidence'])

    def test_unattended_budget_and_extended_configuration(self):
        self.assertLessEqual(run_budget(model.causal_schedule(10),True),1140)
        self.assertLess(run_budget(model.causal_schedule(10,True),False,False,True),1140)
        with self.assertRaisesRegex(ValueError,'budget'): run_budget(model.causal_schedule(10),True,True)

    def test_paired_confidence_gate_does_not_hide_slow_trials(self):
        self.assertEqual(paired_summary([{'reference':100,'instrumented':101}]*7,.03)['status'],'passed')
        self.assertEqual(paired_summary([{'reference':100,'instrumented':110}]*7,.03)['status'],'failed')

    def test_comparison_rows_do_not_pollute_command_equality(self):
        d=lambda i:['D',str(i),'100','1','7','11','12','4','3','5123','64','0','1']
        result=model.temporal_differences([d(1),d(2),['W','2','100','7','2','16','0','0'],
            ['V','2','100','7','2','0','1','2']])
        self.assertEqual(result['combined_command_state_resource']['identical_consecutive_fraction'],1)
        self.assertEqual(result['uniform_exact_elements']['compared_calls'],1)

    def test_detail_pairs_must_share_epoch_thread_and_window(self):
        d=lambda i,epoch,thread,window:['D',str(i),'100','1','7','11','12','4','3','5123','64','0','1',
            str(epoch),str(i),'1',str(thread),'5',str(window),'99']
        for second in [d(2,8,1,5),d(2,7,2,5),d(2,7,1,6)]:
            result=model.temporal_differences([d(1,7,1,5),second])
            self.assertEqual(result['combined_command_state_resource']['compared_consecutive_pairs'],0)
        result=model.temporal_differences([d(1,7,1,5),d(2,7,1,5)])
        self.assertEqual(result['combined_command_state_resource']['identical_consecutive_fraction'],1)

    def test_sample_windows_compare_local_neighbors_and_require_completeness(self):
        frames=[]
        for i in range(12):
            f={"update_id":i,"render_id":i,"sample_window":5 if 4<=i<8 else 0}
            f.update({k:100 for k in ("update_cpu_ns","update_wall_ns","render_cpu_ns","render_wall_ns")})
            frames.append(f)
        self.assertEqual(model.sampled_perturbation(frames,1)["status"],"passed")
        frames[5]["render_id"]=100
        self.assertEqual(model.sampled_perturbation(frames,1)["status"],"unavailable")
        frames[5]["render_id"]=5
        for f in frames[4:8]: f["render_wall_ns"]=106
        self.assertEqual(model.sampled_perturbation(frames,1)["status"],"failed")
        self.assertEqual(model.sampled_perturbation(frames,6)["status"],"unavailable")

if __name__=='__main__':unittest.main()
