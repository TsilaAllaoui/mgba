#!/usr/bin/env python3
from __future__ import annotations
import argparse,subprocess,sys
from pathlib import Path
from mgba_common import create_manifest,find_executable

def main()->int:
    ap=argparse.ArgumentParser(description='Patch one ROM with current GBASaveHandler and immediately test it.')
    ap.add_argument('rom');ap.add_argument('target');ap.add_argument('--patcher');ap.add_argument('--output')
    ap.add_argument('--frames',type=int,default=3000);ap.add_argument('--fresh',action='store_true');ap.add_argument('--save',action='store_true');ap.add_argument('--cold-save',action='store_true')
    ap.add_argument('--state');ap.add_argument('--movie');ap.add_argument('--state-keep-cart',action='store_true')
    args=ap.parse_args()
    src=Path(args.rom).resolve()
    if not src.is_file():ap.error(f'ROM not found: {src}')
    patcher=Path(args.patcher).resolve() if args.patcher else find_executable(['GBASaveHandler','GBASaveHandler-linux-x86_64-static'],Path(__file__),['GBASAVEHANDLER'])
    if not patcher:ap.error('GBASaveHandler not found')
    out=Path(args.output).resolve() if args.output else src.with_name(f'{src.stem}-{args.target}.gsa.gba')
    report=out.with_suffix('.patch.txt'); manifest=out.with_suffix('.mgba.json')
    print('[1/3] Patching...')
    cp=subprocess.run([str(patcher),str(src),str(out),args.target],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    report.write_text(cp.stdout,encoding='utf-8'); print(cp.stdout,end='')
    if cp.returncode:return cp.returncode
    print('[2/3] Building strict virtual-cart manifest...')
    try:create_manifest(out,report,manifest)
    except Exception as e:print(f'Manifest FAILED: {e}',file=sys.stderr);return 2
    print(f'Manifest: {manifest}')
    print('[3/3] Headless test...')
    test=Path(__file__).with_name('mgba_test.py')
    cmd=[sys.executable,str(test),str(out),'--manifest',str(manifest),'--frames',str(args.frames)]
    if args.fresh:cmd.append('--fresh')
    if args.cold_save:cmd.append('--cold-save')
    elif args.save:cmd.append('--quick-save')
    if args.state:cmd += ['--state',args.state]
    if args.state_keep_cart:cmd.append('--state-keep-cart')
    if args.movie:cmd += ['--movie',args.movie]
    return subprocess.call(cmd)
if __name__=='__main__':raise SystemExit(main())
