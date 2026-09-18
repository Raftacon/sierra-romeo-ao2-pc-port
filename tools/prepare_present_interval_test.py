"""Extract the actual recompiled retail interval selection for native tests.

The second slice restores the former hook placement as a negative control.
Neither slice substitutes a host implementation for the retail branch graph.
"""
from pathlib import Path
import sys

source,out=map(Path,sys.argv[1:])
marker = 'DEFINE_REX_FUNC(sub_82A56350) {'
# Codegen may repartition translation units after hook/function changes. A
# numeric .108.cpp filename is not a stable identity for this retail function.
if source.is_dir():
    matches = []
    for candidate in source.glob('army_of_two_recomp.*.cpp'):
        data = candidate.read_bytes()
        if marker.encode() in data:
            matches.append(data.decode().replace('\r\n', '\n'))
    if len(matches) != 1:
        raise ValueError(f'Expected one interval function, found {len(matches)}')
    text = matches[0]
else:
    text = source.read_text()
if text.count(marker) != 1:
    raise ValueError('Expected exactly one retail interval function')
function=text.split(marker,1)[1]
block=function.split('loc_82A56404:\n',1)[1].split('\t// lis r10,-32091',1)[0]
if block.count('AotPresentInterval(ctx.r10);')!=1:
    raise ValueError('Require exactly one interval hook')
join=block.index('loc_82A56458:')
if block.index('AotPresentInterval')<join or block.index('AotPresentInterval')>block.index('// rlwinm r8,r10,8,0,23'):
    raise ValueError('Interval hook must cover the common join before packing')
old=block.replace('\tAotPresentInterval(ctx.r10);\n','')
anchor='\tctx.r10.s64 = 2;\n'
if old.count(anchor)!=1:raise ValueError('Require the original two-refresh branch')
old=old.replace(anchor,anchor+'\tAotPresentInterval(ctx.r10);\n')
out.mkdir(parents=True,exist_ok=True)
for name,value in [('present_interval_fixed.inc',block),('present_interval_previous.inc',old)]:
    path=out/name
    if not path.exists() or path.read_text()!=value:path.write_text(value)
