# E1 coverage audit — gfx-live-20261025T155406Z

- Session admission eligible: **True** (fixtures: eligible)
- Eligible transcripts: **64**
- Manifest SHA-256 (canonical): `e8914fb1f71ec8feda8cce2f6407d9a7e846c4203ca6743fb3ba9ec9476ea437`

## Policy
- Required cells: 11; diversity floor: **12**
- Diversity met: **False**
- Distinct behaviors: **10**
- Duplicate captures (same key): **54**

## Missing cells
- Required: ['input:zero', 'slot:3', 'slot:4', 'slot:5']
- Optional: (none)

## Slot distribution
- slot 0: 7
- slot 1: 50
- slot 2: 7

## Selector distribution
- fixed-length: 3
- length-from-buffer: 61

## Input class distribution
- absent: 3
- nonzero: 61

## Top behavior keys (count)
- **26** × `slot=1|selector=length-from-buffer|input=nonzero|word_count=8|dirty=1|buffer=unchanged|handle=unchanged|alias=distinct|pointer=rsi|rax=ctx|canary=preserved`
- **14** × `slot=1|selector=length-from-buffer|input=nonzero|word_count=13|dirty=1|buffer=unchanged|handle=unchanged|alias=distinct|pointer=rsi|rax=ctx|canary=preserved`
- **10** × `slot=1|selector=length-from-buffer|input=nonzero|word_count=5|dirty=1|buffer=unchanged|handle=unchanged|alias=distinct|pointer=rsi|rax=ctx|canary=preserved`
- **5** × `slot=0|selector=length-from-buffer|input=nonzero|word_count=23|dirty=1|buffer=unchanged|handle=unchanged|alias=distinct|pointer=rsi|rax=ctx|canary=preserved`
- **3** × `slot=2|selector=length-from-buffer|input=nonzero|word_count=1|dirty=1|buffer=unchanged|handle=unchanged|alias=distinct|pointer=rsi|rax=ctx|canary=preserved`
- **2** × `slot=0|selector=length-from-buffer|input=nonzero|word_count=2|dirty=1|buffer=unchanged|handle=unchanged|alias=distinct|pointer=rsi|rax=ctx|canary=preserved`
- **1** × `slot=2|selector=fixed-length|input=absent|word_count=16|dirty=1|buffer=absent|handle=unchanged|alias=absent|pointer=rsi|rax=ctx|canary=preserved`
- **1** × `slot=2|selector=fixed-length|input=absent|word_count=20|dirty=1|buffer=absent|handle=unchanged|alias=absent|pointer=rsi|rax=ctx|canary=preserved`
- **1** × `slot=2|selector=fixed-length|input=absent|word_count=80|dirty=1|buffer=absent|handle=unchanged|alias=absent|pointer=rsi|rax=ctx|canary=preserved`
- **1** × `slot=2|selector=length-from-buffer|input=nonzero|word_count=8|dirty=1|buffer=unchanged|handle=unchanged|alias=distinct|pointer=rsi|rax=ctx|canary=preserved`
