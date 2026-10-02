import tempfile
import unittest
from pathlib import Path
from unittest import mock
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'benchmark'))
import eu4_frame_model as model

class StreamedPhase:
    """An independent producer: delayed command pickup and delayed serialization."""
    def __init__(self, root, control, command_delay=.04, serialization_delay=.12, drop_sample=False):
        self.now=100.;self.next_frame=100.;self.root=root;self.control=control
        self.command_delay=command_delay;self.serialization_delay=serialization_delay
        self.drop_sample=drop_sample;self.dropped=False
        self.pending_commands=[];self.pending_records=[];self.active=None;self.remaining=0;self.render=0
        self.original_set=control.set;self.commands=[];self.events=[]

    def set(self,*args,**kwargs):
        gen=self.original_set(*args,**kwargs)
        fields=model.CONTROL.unpack_from(self.control.map)
        self.pending_commands.append((self.now+self.command_delay,fields))
        self.commands.append(fields)
        return gen

    def sleep(self, seconds):
        target=self.now+seconds
        while self.next_frame<=target:
            self.now=self.next_frame;self.next_frame+=.02
            while self.pending_commands and self.pending_commands[0][0]<=self.now:
                _,self.active=self.pending_commands.pop(0);self.remaining=self.active[5]
            if self.active and self.active[4]&2:
                self.render+=1
                f={k:0 for k in model.FRAME_FIELDS}
                f.update(update_id=self.render,render_id=self.render,phase=self.active[3],thread_id=11,
                    measurement_epoch=self.active[9],generation=self.active[8],
                    sample_window=self.active[8] if self.remaining else 0,render_executed=1,
                    start_ns=int((self.now-.02)*1e9),end_ns=int(self.now*1e9))
                if self.remaining: self.remaining-=1
                f.update({k:100 for k in ('update_cpu_ns','update_wall_ns','render_cpu_ns','render_wall_ns')})
                line='F,'+','.join(str(f[k]) for k in model.FRAME_FIELDS)+'\n'
                if self.drop_sample and f['sample_window'] and not self.dropped: self.dropped=True
                else: self.pending_records.append((self.now+self.serialization_delay,line))
            ready=[]
            while self.pending_records and self.pending_records[0][0]<=self.now:
                ready.append(self.pending_records.pop(0)[1])
            if ready:
                with (self.root/'telemetry.csv').open('a') as out: out.write(''.join(ready))
        self.now=target

    def ack(self, game, control, generation, label):
        self.sleep(self.command_delay+.02)
        model.struct.pack_into('<Q',control.map,80,control.command_sent_ns)
        # Keep the acknowledgement bracket exact while still delaying pickup.
        control.command_sent_ns=int(self.now*1e9)
        model.struct.pack_into('<Q',control.map,80,control.command_sent_ns)
        self.events.append(('ack',label,control.measurement_epoch))

    def mark(self,path,event,**kwargs):
        self.events.append((event,kwargs,self.control.measurement_epoch))
        return {'monotonic_ns':int(self.now*1e9),'wall_ns':int(self.now*1e9)}

class CalibrationTests(unittest.TestCase):
    def run_phase(self, **options):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);path=root/'control';path.write_bytes(bytes(model.CONTROL_SIZE))
            control=model.SharedControl(path);producer=StreamedPhase(root,control,**options)
            try:
                with mock.patch.object(control,'set',side_effect=producer.set), \
                     mock.patch.object(model,'_wait_ack',side_effect=producer.ack), \
                     mock.patch.object(model.time,'monotonic',side_effect=lambda:producer.now), \
                     mock.patch.object(model.time,'monotonic_ns',side_effect=lambda:int(producer.now*1e9)), \
                     mock.patch.object(model.time,'sleep',side_effect=producer.sleep), \
                     mock.patch.object(model.auto,'mark',side_effect=producer.mark), \
                     mock.patch.object(model.auto,'focus',return_value=True):
                    game=mock.Mock();game.poll.return_value=None
                    result=model._wait_phase(game,15,control,root/'events','P1','profile',0,
                        detail=True,detail_windows=6,detail_frames_per_window=4)
                frames=model.frame_rows(root/'telemetry.csv')
                selected=[f for f in frames if model.eligible_frame(f,result)]
                return result,producer,model.sampled_perturbation(selected)
            finally: control.close()

    def test_six_windows_through_controller_and_analysis(self):
        phase,producer,evidence=self.run_phase()
        self.assertEqual(evidence['status'],'passed')
        self.assertEqual(phase['duration_s'],15)
        arms=phase['sample_requests'];self.assertEqual(len(arms),6)
        start=phase['start_ns']
        self.assertEqual([a['requested_arm_ns'] for a in arms],
                         [int((start/1e9+15*i/7)*1e9) for i in range(1,7)])
        self.assertTrue(all(a['actual_arm_ns']>=a['requested_arm_ns'] for a in arms))
        self.assertTrue(all(len(w['sampled'])==len(w['before'])==len(w['after'])==4 for w in evidence['windows']))
        self.assertEqual({r['measurement_epoch'] for w in evidence['windows'] for r in w['sampled']+w['before']+w['after']},{1})
        # Transition, enable, then immediate provenance publication carries zero detail.
        self.assertEqual([c[5] for c in producer.commands[:3]],[0,0,0])
        self.assertEqual({c[9] for c in producer.commands[1:-1]},{1})
        self.assertEqual({a['generation'] for a in arms},{w['sample_window'] for w in evidence['windows']})
        boundary={k:0 for k in model.FRAME_FIELDS}
        boundary.update(phase=model.PHASE_NUMBER['P1'],measurement_epoch=1,start_ns=start-1,end_ns=start+1)
        self.assertFalse(model.eligible_frame(boundary,phase))

    def test_delayed_serialization_still_completes(self):
        phase,_,evidence=self.run_phase(serialization_delay=.4)
        self.assertEqual(evidence['status'],'passed');self.assertEqual(len(phase['sample_requests']),6)

    def test_missing_sample_fails_without_extending_phase(self):
        with self.assertRaisesRegex(model.base.BenchmarkError,'incomplete sample calibration'):
            self.run_phase(drop_sample=True)

    def test_delayed_commands_fail_explicitly(self):
        with self.assertRaisesRegex(model.base.BenchmarkError,'incomplete sample calibration'):
            self.run_phase(command_delay=2.5)

    def test_incremental_reader_retains_partial_line(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'telemetry';reader=model.TelemetryReader(path)
            self.assertEqual(reader.poll(),[])
            line='F,'+','.join('1' for _ in model.FRAME_FIELDS)+'\n'
            path.write_text(line[:17]);self.assertEqual(reader.poll(),[])
            with path.open('a') as out: out.write(line[17:])
            self.assertEqual(len(reader.poll()),1);self.assertEqual(reader.poll(),[])

    def test_incomplete_neighbors_and_unsupported_populations(self):
        frames=[]
        for i in range(12):
            f={'update_id':i,'render_id':i,'phase':1,'measurement_epoch':1,'thread_id':1,
               'render_executed':1,'sample_window':5 if 4<=i<8 else 0}
            f.update({k:100 for k in ('update_cpu_ns','update_wall_ns','render_cpu_ns','render_wall_ns')});frames.append(f)
        self.assertEqual(model.sampled_perturbation(frames[1:],1)['status'],'unavailable')
        for key,value in [('thread_id',2),('measurement_epoch',2),('phase',2),('render_executed',2),('render_id',20)]:
            altered=[dict(f) for f in frames];altered[10][key]=value
            self.assertEqual(model.sampled_perturbation(altered,1)['status'],'unavailable')
