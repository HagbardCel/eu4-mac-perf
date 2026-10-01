import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_frame_model as model  # noqa: E402


def frame(phase: int, update_cpu_ns: int=2_000_000,
          render_cpu_ns: int=1_000_000, draws: int=10) -> dict:
    row={key:0 for key in model.FRAME_FIELDS}
    row.update({"phase":phase,"update_id":1,"render_id":1,
        "wall_ns":20_000_000,"cpu_ns":3_000_000,
        "update_wall_ns":10_000_000,"update_cpu_ns":update_cpu_ns,
        "render_wall_ns":5_000_000,"render_cpu_ns":render_cpu_ns,
        "draws":draws,"forwarded_draws":draws,
        "suppressed_draws":0,"flags":0})
    return row


class FrameModelTests(unittest.TestCase):
    def test_phase_summary_uses_phase_tagged_measurement_population_and_real_duration(self):
        phase=model.PHASE_NUMBER["E30"]
        # Settling rows are deliberately untagged by the C producer.
        rows=[frame(0) for _ in range(150)] + [frame(phase) for _ in range(900)]
        for index,row in enumerate(rows[150:]):
            row["render_executed"]=int(index%3==0)
            row["render_cpu_ns"]=3_000_000 if row["render_executed"] else 0
        result=model.phase_summary(rows,"E30",30)
        self.assertEqual(result["updates"],900)
        self.assertEqual(result["update_attempts_s"],30)
        self.assertEqual(result["executed_renders_s"],10)
        self.assertEqual(result["mean_render_cpu_ms_per_executed_render"],3)

    def test_control_command_preserves_probe_owned_ack_and_failure_counters(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"control.bin"
            path.write_bytes(bytes(model.CONTROL_SIZE))
            control=model.SharedControl(path)
            struct=model.struct
            struct.pack_into("<QQQ",control.map,56,7,11,13)
            generation=control.set("cadence_30",8,19_000_000,33_333_333,2,True)
            self.assertEqual(generation,1)
            self.assertEqual(control.acknowledged(),7)
            self.assertEqual(control.hook_failures(),11)
            self.assertEqual(control.dropped_records(),13)
            self.assertEqual(control.map.size(),model.CONTROL_SIZE)
            self.assertEqual(struct.unpack_from("<I",control.map,4)[0]%2,0)
            command= model.CONTROL_COMMAND.unpack_from(control.map)
            self.assertEqual(command[2:6],(model.MODE["cadence_30"],8,3,2))
            self.assertEqual(command[6:9],(19_000_000,33_333_333,1))
            control.close()

    def test_causal_report_includes_c_minus_b_with_expected_sign(self):
        names=("A0","B","A1","C","A2","D","A3","E30","A4","E15","A5")
        phases=[]
        for name in names:
            cpu={"A0":8,"A1":8,"B":2,"C":5,"A2":8,"D":7,
                 "A3":8,"E30":8,"A4":8,"E15":8,"A5":8}[name]
            phases.append({"phase":name,"status":"complete",
                "median_update_cpu_ms":cpu,"update_attempts_s":50})
        contrasts=model.causal_contrasts(phases)
        self.assertAlmostEqual(contrasts["C_minus_B"]["cpu_ms_per_update"],3)
        self.assertAlmostEqual(contrasts["C_minus_B"]["local_control_sensitivity_ms_per_update"],3)
        self.assertAlmostEqual(contrasts["C"]["cpu_difference_control_minus_test_ms"],3)
        self.assertTrue(contrasts["C"]["cadence_valid"])

    def test_topology_only_does_not_recommend_command_or_bucket_reuse(self):
        phases=[{"phase":name,"status":"complete","median_update_cpu_ms":cpu,
                 "update_attempts_s":50}
                for name,cpu in (("A0",8),("B",7),("A1",8),("C",6),
                                 ("A2",8),("D",7),("A3",8))]
        temporal={"draw_topology":{"identical_consecutive_fraction":1.0}}
        recommendations=model.derive_recommendations(phases,{},temporal,{})
        self.assertFalse(any("cache" in item["intervention"] or "reuse" in item["intervention"]
                             for item in recommendations))

    def test_draw_identity_includes_geometry_source_signature_and_index_range(self):
        first=["D","1","100","1","7","11","12","4","3","5123","64","4294967295","1"]
        changed=["D","2","100","1","7","13","12","4","3","5123","64","4294967295","1"]
        result=model.temporal_differences([first,changed])
        self.assertEqual(result["draw_identity"]["identical_consecutive_fraction"],0)

    def test_uniform_sequence_keeps_generic_payload_byte_sizes(self):
        trace=[
            ["U","1","100","7","4",str((12<<32)|123)],
            ["U","2","100","7","4",str((12<<32)|123)],
        ]
        result=model.temporal_differences(trace)
        self.assertEqual(result["uniform_payloads"]["compared_payloads"],1)
        self.assertEqual(result["uniform_call_sequence"]["identical_fraction"],1)

    def test_render_envelope_exclusive_time_stays_unexplained(self):
        trace=[
            ["Q","1","1","-1","8","1","110","100","10","10","1","0","0"],
            ["Q","1","1","0","0","1","100","90","10","10","1","1","0"],
            ["Q","1","1","1","2","1","90","80","89","79","1","2","0"],
            ["Q","1","1","2","5","1","1","1","1","1","1","3","0"],
        ]
        result=model.scope_tree_summary(trace,[{"name":"A0"}])["phases"]["1"]
        self.assertEqual(result["tree_reconciliation"],"passed")
        self.assertEqual(result["coverage_95_percent_gate"],"not met")
        render=result["envelope_coverage"][model.SCOPE_NAMES[2]]
        self.assertEqual(render["cpu_fraction"],1/80)
        self.assertEqual(render["residual_cpu_ms"],79/1e6)

    def test_pacing_cannot_improve_coverage(self):
        trace=[
            ["Q","1","1","-1","8","1","1000","1000","0","0","1","0","0"],
            ["Q","1","1","0","0","1","10","10","10","10","1","1","0"],
            ["Q","1","1","0","9","1","990","990","990","990","1","2","0"],
        ]
        result=model.scope_tree_summary(trace,[{"name":"A0"}])["phases"]["1"]
        self.assertEqual(result["tree_reconciliation"],"passed")
        self.assertEqual(result["envelope_coverage"][model.SCOPE_NAMES[0]]["cpu_fraction"],0)
        self.assertEqual(result["coverage_95_percent_gate"],"not met")

    def test_gpu_samples_are_segmented_by_context_and_pass(self):
        result=model.gpu_summary([
            ["G","1","10","100","200","1","0","1","7","8","0","0","0"],
            ["G","1","10","300","400","2","1","2","7","8","0","0","0"],
            ["G","1","10","0","0","2","1","3","7","8","4","0","0"],
        ])
        value=result["phases"]["1"]
        self.assertEqual(value["context_count"],2)
        self.assertIsNone(value["median_whole_render_ms"])
        self.assertEqual(value["cross_context_aggregation"],"not performed")
        self.assertEqual(value["missing_segment_counts"],{"4":1})
        self.assertEqual(set(value["context_pass_segments"]),{"1:0","2:1"})

    def test_legacy_records_remain_readable_but_cannot_pass_gate(self):
        tree=model.scope_tree_summary([["Q","1","1","-1","8","1","10","10","10","10"]],[{"name":"A0"}])
        self.assertEqual(len(tree["legacy_records"]),1)
        self.assertEqual(tree["phases"]["1"]["coverage_95_percent_gate"],"not met")
        self.assertEqual(len(model.gpu_summary([["G","1","1","200","100","1"]])["legacy_unqualified_samples"]),1)

    def test_one_eligibility_filter_excludes_boundaries_for_costs_and_details(self):
        window={"name":"A0","measurement_epoch":7,"start_ns":100,"end_ns":200}
        rows=[]
        for i,(start,end,epoch) in enumerate(((90,110,7),(110,120,7),(190,210,7),(120,130,6))):
            r=frame(1);r.update(update_id=i+1,measurement_epoch=epoch,start_ns=start,end_ns=end)
            rows.append(r)
        selected=[r for r in rows if model.eligible_frame(r,window)]
        self.assertEqual([r["update_id"] for r in selected],[2])
        trace=[["D","1"]+["0"]*11+["7",str(i),"1"] for i in range(1,5)]
        trace+=[["G","1","1","10","20","1","0","1","7","3","0","0","0"]]
        self.assertEqual(len(model.eligible_trace(trace,rows,[window])),1)
        summary=model.phase_summary(rows,"A0",1,window)
        self.assertEqual(summary["updates"],1)
        self.assertEqual(summary["update_attempts_s"],2)  # Start events, including end-crossing frame.

    def test_detail_rearm_preserves_measurement_epoch(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"control";path.write_bytes(bytes(model.CONTROL_SIZE))
            control=model.SharedControl(path)
            try:
                control.set("profile",1,measurement=False)
                control.set("profile",1,detail_frames=2,measurement=True)
                epoch=control.measurement_epoch
                control.set("profile",1,detail_frames=2,measurement=True)
                self.assertEqual(control.measurement_epoch,epoch)
                control.set("profile",0,measurement=False)
                control.set("profile",2,measurement=True)
                self.assertEqual(control.measurement_epoch,epoch+1)
            finally: control.close()

    def test_power_curve_uses_native_30_and_15_render_rates(self):
        phases=[{"phase":name,"status":"complete","executed_renders_s":rate}
                for name,rate in (("A3",52),("A4",51),("A5",53),
                                  ("E30",30),("E15",15))]
        power={name:{"combined_w":10+rate*.1}
               for name,rate in (("A3",52),("A4",51),("A5",53),
                                 ("E30",30),("E15",15))}
        result=model.power_fps_scaling(phases,power)
        self.assertEqual(result["status"],"complete")
        self.assertEqual([point["phase"] for point in result["points"]],
                         ["native","E30","E15"])
        self.assertAlmostEqual(result["linear_fit"]["frame_driven_w_per_fps"],.1)

    def test_fixed_cadence_lpm_is_reported_separately_from_natural_cadence(self):
        names=("LPMF0","LPMF","LPMF1")
        phases=[{"phase":name,"median_update_cpu_ms":value,"update_attempts_s":50}
                for name,value in zip(names,(2,2.4,2.1))]
        power={name:{"combined_w":value} for name,value in zip(names,(15,12,14))}
        conclusions=model.derive_conclusions(phases,{},power)
        self.assertTrue(any("Fixed-cadence Low Power DVFS check" in item
                            for item in conclusions))

    def test_power_recovery_restores_journaled_value(self):
        with tempfile.TemporaryDirectory() as directory:
            journal=Path(directory)/"power-mode-journal.json"
            journal.write_text('{"setting":"powermode","value":"2",'
                               '"previous":{"mode":"high"}}')
            completed=model.subprocess.CompletedProcess([],0,"","")
            with mock.patch.object(model.subprocess,"run",return_value=completed) as run, \
                 mock.patch.object(model.base,"power_state",return_value={"mode":"high"}):
                result=model._restore_power_mode(journal)
            self.assertEqual(result["value"],"2")
            self.assertEqual(run.call_args.args[0][-3:],
                             [str(model.POWER_HELPER_INSTALLED),"powermode","2"])


if __name__ == "__main__":
    unittest.main()
