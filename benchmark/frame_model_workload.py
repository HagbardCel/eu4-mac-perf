"""Structural GL overhead recipes from a complete passive frame, with no asset payloads."""
import collections
import hashlib
import json
import random
import statistics
import struct
import subprocess
from pathlib import Path
import eu4_draw_trace as trace
import eu4_benchmark as base

SOURCE=trace.ROOT/'results/20260928T113359Z-draw-trace/trace.bin'
INVENTORY=trace.ROOT/'analysis/draw-callers.json'
RECORD=struct.Struct('<8I')

def recipes(directory):
    if not SOURCE.is_file(): raise base.BenchmarkError('Passive trace is unavailable for representative recipes')
    rows=list(trace.read_records(SOURCE,trace.read_header(SOURCE)['used']))
    frames=collections.Counter((r['window'],r['frame']) for r in rows)
    key=next((key for key,count in frames.items() if count==5863),None)
    if key is None: raise base.BenchmarkError('No complete 5863-draw passive frame for recipe validation')
    sites={r['return_offset']:r['category'] for r in json.loads(INVENTORY.read_text())['direct_sites']}
    groups={'mesh':{'mesh_object'},'borders':{'borders'},'text_ui':{'map_text','ui_or_text'}}
    result=[]
    for name,categories in groups.items():
        selected=[r for r in rows if (r['window'],r['frame'])==key and
                  sites.get(r['caller_offset'],sites.get(r['immediate_offset'])) in categories]
        if not selected or any(r['flags'] or r['api'] not in (1,2,3) or r['mode'] not in (4,5) for r in selected):
            raise base.BenchmarkError(f'Invalid or unsupported trace recipe: {name}')
        payload=bytearray();previous=None
        for r in selected:
            change=lambda field: int(previous is None or r[field]!=previous[field])
            payload.extend(RECORD.pack(r['api'],r['mode'],r['count'],change('uniform_sig'),
                change('texture_sig'),change('render_sig'),change('vertex_sig'),change('program')))
            previous=r
        path=directory/(name+'.recipe');path.write_bytes(payload)
        result.append({'name':name,'path':str(path),'sha256':hashlib.sha256(payload).hexdigest(),
            'draws':len(selected),'indices':sum(r['count'] for r in selected),
            'source_frame':list(key),'source_sha256':base.sha256(SOURCE),
            'policy':'Observed draw counts/topology/API and adjacent state-change frequencies; generated valid resources and shaders, no sleeps or added busywork.',
            'limits':'GL structural surrogate; original resource payloads, shaders, and unobserved between-draw commands are not reconstructed. Live perturbation remains mandatory.'})
    return result


def paired_summary(pairs, limit):
    ratios=[p['instrumented']/p['reference']-1 for p in pairs]
    # Deterministic paired bootstrap of the median; retain raw trials for audit.
    rng=random.Random(1729)
    bootstrap=sorted(statistics.median(rng.choices(ratios,k=len(ratios))) for _ in range(2000))
    interval=[bootstrap[50],bootstrap[1950]]
    median=statistics.median(ratios)
    return {'median_fraction':median,'confidence_interval_95':interval,'pairs':pairs,
            'limit':limit,'status':'passed' if max(abs(median),abs(interval[0]),abs(interval[1]))<=limit else 'failed'}
