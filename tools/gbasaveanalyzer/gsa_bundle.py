#!/usr/bin/env python3
from __future__ import annotations
import argparse, json, shutil, tarfile, tempfile
from pathlib import Path
from gsa_common import testlib_root, read_json

KEEP_RUN=('summary.json','summary.csv','NEEDS_RECORDING.md','OPTIONAL_RECORDING.md','FAILURES.md','PATCH_GAPS.md','INAPPLICABLE.md')
KEEP_TARGET=('patch.txt','manifest.gsa.json')
KEEP_WORK=('report.json','report-persist.json','FAILURE_SUMMARY.txt')

def main()->int:
    ap=argparse.ArgumentParser(description='Create a small shareable diagnostic archive without ROMs or virtual cartridge images.')
    ap.add_argument('--library');ap.add_argument('--output')
    args=ap.parse_args();root=testlib_root(args.library)
    lp=root/'LATEST_RUN.txt'
    if not lp.is_file():ap.error('LATEST_RUN.txt not found; run a scan first')
    run=Path(lp.read_text().strip())
    if not run.is_dir():ap.error(f'latest run folder not found: {run}')
    summary=read_json(run/'summary.json')
    runid=summary.get('run') or run.name
    out=Path(args.output).expanduser().resolve() if args.output else (Path.home()/f'mgba_DIAGNOSTICS_{runid}.tar.xz')
    with tempfile.TemporaryDirectory(prefix='gsa-diag-') as td0:
        td=Path(td0)/f'mgba_DIAGNOSTICS_{runid}';td.mkdir()
        for n in KEEP_RUN:
            src=run/n
            if src.is_file():shutil.copy2(src,td/n)
        meta={k:summary.get(k) for k in ('schema','tool','run','rom_folder','frames','jobs','targets')}
        (td/'BUNDLE_INFO.json').write_text(json.dumps(meta,indent=2)+'\n')
        copied=set()
        for r in summary.get('results',[]):
            sha=r.get('source_sha256');target=r.get('target')
            if not sha or not target:continue
            key=(sha,target)
            if key in copied:continue
            copied.add(key)
            srcdir=root/'games'/sha/'targets'/target
            dstdir=td/'games'/sha/'targets'/target;dstdir.mkdir(parents=True,exist_ok=True)
            for n in KEEP_TARGET:
                src=srcdir/n
                if src.is_file():shutil.copy2(src,dstdir/n)
            work=srcdir/'work'
            for n in KEEP_WORK:
                src=work/n
                if src.is_file():shutil.copy2(src,dstdir/n)
            gp=root/'games'/sha/'game.json'
            gd=td/'games'/sha
            if gp.is_file() and not (gd/'game.json').exists():shutil.copy2(gp,gd/'game.json')
        out.parent.mkdir(parents=True,exist_ok=True)
        with tarfile.open(out,'w:xz') as tf:tf.add(td,arcname=td.name)
    print(f'Diagnostic bundle: {out}')
    print('Contains reports/manifests only; ROMs, patched ROMs, cart.bin and fram.bin are excluded.')
    return 0
if __name__=='__main__':raise SystemExit(main())
