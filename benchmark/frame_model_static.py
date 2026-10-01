"""Bounded static traversal and local shader evidence. Never exports game assets."""
from __future__ import annotations
import hashlib
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'analysis/frame-model-static.json'


def graph(binary, symbols, roots, max_depth=4, capacity=512):
    aliases = {}
    for name, address in symbols.items():
        aliases.setdefault(address, []).append(name)
    pending = sorted(set(roots)); seen = set(); nodes = {}; truncated = []
    for depth in range(max_depth+1):
        batch = [s for s in pending if s in symbols and symbols[s] not in seen]
        remaining = capacity-len(seen)
        if len(batch)>remaining: truncated += batch[remaining:]; batch=batch[:remaining]
        if not batch: break
        # Aliases refer to one body, so disassemble a body only once.
        batch = list({symbols[s]:s for s in batch}.values())
        raw = subprocess.run(['xcrun','llvm-objdump','--symbolize-operands',
            '--disassemble-symbols='+','.join(batch),str(binary)],
            text=True,capture_output=True,check=True).stdout
        current=None; next_batch=[]
        for line in raw.splitlines():
            header=re.match(r'^([0-9a-f]{16}) <([^>]+)>:',line)
            if header:
                address=int(header[1],16); current=header[2]; seen.add(address)
                nodes[current]={'address':address,'depth':depth,'aliases':sorted(aliases.get(address,[])),
                    'edges':[],'indirect_edges':[],'gl_pointer_references':[]}
                continue
            if not current: continue
            instruction=re.match(r'^\s*([0-9a-f]+):.*?\t(callq|jmpq)\s+(.*)',line)
            refs=re.findall(r'<([^>]*(?:glew|_gl[A-Z]|_CGL)[^>]*)>',line)
            if refs:
                nodes[current]['gl_pointer_references'].append({'instruction':line.strip(),'targets':refs,
                    'confidence':'symbol reference; indirect call/dataflow still requires verification'})
            if not instruction: continue
            site=int(instruction[1],16); operand=instruction[3]
            target=re.search(r'<([^>]+)>',operand)
            if target and not target[1].startswith('L') and not operand.startswith('*'):
                edge={'address':site,'target':target[1],'kind':'tail' if instruction[2]=='jmpq' else 'call',
                    'confidence':'direct symbolized instruction'}
                nodes[current]['edges'].append(edge)
                if target[1] in symbols: next_batch.append(target[1])
            elif '*' in operand:
                nodes[current]['indirect_edges'].append({'address':site,'operand':operand,'status':'unresolved'})
        pending=sorted(set(next_batch))
    truncated += [s for s in pending if s in symbols and symbols[s] not in seen]
    return {'roots':sorted(roots),'nodes':nodes,'max_depth':max_depth,'capacity':capacity,
            'unvisited_at_bound':sorted(set(truncated)),
            'limits':['Indirect edges remain unresolved; pointer references are not proven calls.',
                      'Traversal is bounded, not exhaustive. Aliases share an address.']}


def shaders(install, user_data):
    # Enabled mod descriptors are found from the existing launcher settings. No assets copied.
    roots=[('installed',install/'gfx/FX')]; unresolved=[]; enabled=[]
    settings=user_data/'dlc_load.json'
    if settings.is_file():
        import json
        for descriptor in json.loads(settings.read_text()).get('enabled_mods',[]):
            path=user_data/descriptor
            if not path.is_file(): unresolved.append(str(descriptor)); continue
            text=path.read_text(errors='replace')
            match=re.search(r'\bpath\s*=\s*"([^"]+)"',text)
            if match:
                mod=Path(match[1]);mod=mod if mod.is_absolute() else user_data/mod
                enabled.append({'descriptor':str(descriptor),'descriptor_sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                    'status':'shader overrides present' if (mod/'gfx/FX').is_dir() else 'no gfx/FX override directory' if mod.is_dir() else 'mod directory missing'})
                if (mod/'gfx/FX').is_dir(): roots.append(('enabled_mod:'+str(descriptor),mod/'gfx/FX'))
                elif not mod.is_dir(): unresolved.append(str(descriptor))
            else: unresolved.append(str(descriptor))
    rows=[]
    features={'geometry_stage':r'GeometryShader|geometry_shader',
        'texture_sampling':r'tex2D|texture2D|texture\s*\(',
        'derivatives':r'ddx|ddy|dFdx|dFdy', 'discard':r'\bdiscard\b|\bclip\s*\(',
        'instancing':r'InstanceID|gl_InstanceID', 'shadow_sampling':r'sampler.*Shadow|shadow2D',
        'compatibility_builtins':r'gl_Vertex|gl_FragColor|gl_ModelView|gl_TexCoord'}
    for label,directory in roots:
        if not directory.is_dir(): unresolved.append(label);continue
        for path in sorted(directory.rglob('*')):
            if path.suffix.lower() not in {'.shader','.fxh','.glsl','.vert','.frag'}: continue
            data=path.read_bytes();text=data.decode('utf-8',errors='replace')
            rows.append({'source':label,'path':str(path.relative_to(directory)),
                'sha256':hashlib.sha256(data).hexdigest(),'bytes':len(data),
                'includes':sorted(set(re.findall(r'["<]([^">]+\.fxh)[">]',text))),
                'features':{key:len(re.findall(pattern,text,re.I)) for key,pattern in features.items()}})
    return {'status':'static feature seed; no compiler/Metal validation', 'files':rows,
            'enabled_mods':enabled,'unresolved_mods':unresolved,'runtime_frequency':'not measured',
            'asset_policy':'Only paths, hashes, sizes, includes, and feature counts are stored.'}
