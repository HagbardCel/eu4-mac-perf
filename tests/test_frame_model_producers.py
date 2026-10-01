import csv
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/"benchmark"))
import eu4_frame_model as model

class ProducerTests(unittest.TestCase):
    def test_shared_native_producers_and_serialization(self):
        compiler=shutil.which("clang") or shutil.which("cc")
        if not compiler: self.skipTest("C compiler unavailable")
        with tempfile.TemporaryDirectory() as directory:
            for name in ("scope","gpu","queue","render_gate"):
                executable=Path(directory)/name
                source=model.ROOT/"tests"/f"frame_model_{name}_harness.c"
                subprocess.run([compiler,"-pthread","-O2","-Wall","-Wextra","-Werror","-o",str(executable),str(source)],check=True,capture_output=True)
                run=subprocess.run([str(executable)],check=True,capture_output=True,text=True)
                if name=="scope":
                    result=model.scope_tree_summary(list(csv.reader(run.stdout.splitlines())),[{"name":"A0"}])["phases"]["1"]
                    self.assertEqual(result["tree_reconciliation"],"passed")
                    nodes=result["nodes"]
                    grandchild=next(n for n in nodes if n["name"]==model.SCOPE_NAMES[5])
                    self.assertEqual(grandchild["scope_path"],[model.SCOPE_NAMES[i] for i in (8,0,2,5)])
                    self.assertEqual(result["envelope_coverage"][model.SCOPE_NAMES[0]]["cpu_fraction"],20/65)


class ControllerTests(unittest.TestCase):
    def test_all_causal_controls_have_matched_update_cadence(self):
        phases=model.causal_schedule(19_000_000)
        self.assertEqual([p["name"] for p in phases],[p[0] for p in model.PHASES])
        self.assertEqual({p["update_period_ns"] for p in phases},{19_000_000})
        pilot=model.causal_schedule(19_000_000,True)
        self.assertEqual([p["name"] for p in pilot],["A0"])
        self.assertTrue(pilot[0]["detail"])
        self.assertEqual(model.PHASE_NUMBER["A0"],1)
        self.assertEqual(model.PHASE_NUMBER["ANATIVE"],110)

    def test_acknowledgement_precedes_timed_window_and_detail_preserves_epoch(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/"control";path.write_bytes(bytes(model.CONTROL_SIZE))
            control=model.SharedControl(path)
            sequence=[]; counter=[0.0]
            def monotonic(): counter[0]+=.1; return counter[0]
            def ack(game,c,generation,label):
                sequence.append(("ack",label,c.measurement_epoch))
                model.struct.pack_into("<Q",c.map,80,c.command_sent_ns)
            def mark(path,event,**kwargs):
                sequence.append((event,kwargs,control.measurement_epoch))
                return {"monotonic_ns":model.time.monotonic_ns(),"wall_ns":model.time.time_ns()}
            try:
                with mock.patch.object(model,"_wait_ack",side_effect=ack), \
                     mock.patch.object(model.time,"monotonic",side_effect=monotonic), \
                     mock.patch.object(model.time,"sleep"), \
                     mock.patch.object(model.auto,"mark",side_effect=mark), \
                     mock.patch.object(model.auto,"focus",return_value=True):
                    game=mock.Mock();game.poll.return_value=None
                    window=model._wait_phase(game,3,control,Path(directory)/"events","A0","profile",19_000_000,detail=True)
                events=[r[0] for r in sequence]
                start_index=events.index("phase_start")
                self.assertEqual(sequence[start_index-1][:2],("ack","A0 measurement enable"))
                arms=[r for r in sequence if r[0]=="detail_sample_armed"]
                self.assertEqual(len(arms),2)
                self.assertEqual({r[2] for r in arms},{window["measurement_epoch"]})
                self.assertEqual(window["enable_generation"],2)
                self.assertEqual(control.measurement_epoch,1)
            finally: control.close()

    def test_timestamped_event_rates_use_same_window_even_for_boundary_frames(self):
        window={"name":"A0","measurement_epoch":7,"start_ns":100,"end_ns":200}
        frame={key:0 for key in model.FRAME_FIELDS}
        frame.update(phase=1,measurement_epoch=7,update_id=1,start_ns=110,end_ns=130)
        events=[["E","7","1","1",str(kind),str(timestamp)] for kind,timestamp in
                ((1,99),(1,110),(2,120),(3,120),(4,150),(4,190),(4,200))]
        result=model.phase_summary([frame],"A0",1,window,events)
        self.assertEqual(result["update_attempts_s"],1)
        self.assertEqual(result["present_calls_s"],2)
        self.assertEqual(result["executed_renders_s"],1)

    def test_aggregate_ratios_instead_of_median_gate(self):
        # Nine tiny fully attributed frames cannot hide one large residual frame.
        trace=[]
        for update in range(10):
            wall=1000 if update==9 else 1
            identified=1
            def q(parent,scope,iw,ew,node):
                return list(map(str,["Q",update,1,parent,scope,1,iw,iw,ew,ew,7,node,0]))
            trace.extend([q(-1,8,wall,0,0),q(0,0,wall,0,1),q(1,2,wall,wall-identified,2),q(2,5,identified,identified,3)])
        result=model.scope_tree_summary(trace,[{"name":"A0"}])["phases"]["1"]
        self.assertEqual(result["per_frame_coverage_distributions"][model.SCOPE_NAMES[2]]["cpu"]["median"],1)
        self.assertLess(result["envelope_coverage"][model.SCOPE_NAMES[2]]["cpu_fraction"],.02)
        self.assertEqual(result["coverage_95_percent_gate"],"not met")


class ReportTests(unittest.TestCase):
    def test_discovery_report_contains_residual_table_and_partial_diagnosis(self):
        import json
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            frame={key:0 for key in model.FRAME_FIELDS}
            frame.update(update_id=1,phase=1,measurement_epoch=7,start_ns=110,end_ns=150,
                         render_executed=1,update_cpu_ns=30,update_wall_ns=40,draws=1)
            scope=["Q","1","1","-1","0","1","40","30","40","30","7","0","0"]
            (root/"telemetry.csv").write_text("F,"+",".join(str(frame[k]) for k in model.FRAME_FIELDS)+"\n"+",".join(scope)+"\n")
            manifest={"format_version":2,"report_kind":"residual_discovery","status":"complete",
                "phases":[{"name":"A0","measurement_epoch":7,"start_ns":100,"end_ns":200,"duration_s":1}]}
            (root/"manifest.json").write_text(json.dumps(manifest))
            result=model.analyze(root)
            self.assertEqual(result["diagnosis_status"],"partial attribution")
            self.assertTrue(result["exclusive_cost_tree"]["ranked_residuals"])
            self.assertIn("Ranked envelope residuals",(root/"report.md").read_text())
            self.assertEqual(json.loads((root/"report.json").read_text())["report_kind"],"residual_discovery")

if __name__=="__main__": unittest.main()
