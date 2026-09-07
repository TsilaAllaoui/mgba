#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,os,subprocess,sys
from pathlib import Path
from mgba_common import environment_for_manifest,find_executable,find_manifest,load_manifest,sha256_file

def main()->int:
    ap=argparse.ArgumentParser(description='Run one patched ROM in the mGBA virtual cartridge.')
    ap.add_argument('rom'); ap.add_argument('--manifest'); ap.add_argument('--workdir')
    ap.add_argument('--fresh',action='store_true'); ap.add_argument('--frames',type=int,default=3000)
    ap.add_argument('--require',choices=['program','erase','fram','save','any']); ap.add_argument('--stop-on',choices=['program','erase','fram','save','any'])
    ap.add_argument('--after-event',type=int,default=60); ap.add_argument('--post-reset',type=int,default=600)
    ap.add_argument('--cold-reset-on-event',action='store_true'); ap.add_argument('--quick-save',action='store_true'); ap.add_argument('--cold-save',action='store_true')
    ap.add_argument('--state'); ap.add_argument('--state-keep-cart',action='store_true'); ap.add_argument('--movie')
    ap.add_argument('--checkpoint',action='store_true'); ap.add_argument('--checkpoint-interval',type=int,default=60); ap.add_argument('--final-state',action='store_true')
    ap.add_argument('--autoqa')
    args=ap.parse_args()
    rom=Path(args.rom).resolve()
    if not rom.is_file():ap.error(f'ROM not found: {rom}')
    manp=find_manifest(rom,args.manifest); man=load_manifest(manp,rom)
    work=Path(args.workdir).resolve() if args.workdir else rom.parent/'.gsa-work'/f"{rom.stem}-{sha256_file(rom)[:12]}"
    work.mkdir(parents=True,exist_ok=True)
    if args.fresh:
        for n in ('cart.bin','fram.bin','events.jsonl','report.json','pre-event.ss','event.ss','final.ss'):
            p=work/n
            if p.exists():p.unlink()
    events=work/'events.jsonl'; report=work/'report.json'
    if events.exists():events.unlink()
    autoqa=Path(args.autoqa).resolve() if args.autoqa else find_executable(
        ['mgba-autoqa','mgba-gbabr-autoqa'],Path(__file__),['MGBA_AUTOQA','MGBA_GBABR_AUTOQA'])
    if not autoqa or not autoqa.is_file():ap.error('AutoQA executable not found')
    require=args.require; stop=args.stop_on; cold=args.cold_reset_on_event
    if args.quick_save: require=stop='save'
    if args.cold_save: require=stop='save'; cold=True
    cmd=[str(autoqa),str(rom),'--frames',str(args.frames),'--after-event',str(args.after_event),'--report',str(report)]
    # R3+ supports post-reset/state-keep-cart. If an older binary is supplied, failure is explicit.
    if cold:cmd += ['--cold-reset-on-event','--post-reset',str(args.post_reset)]
    if require:cmd += ['--require',require]
    if stop:cmd += ['--stop-on',stop]
    if args.state:
        st=Path(args.state)
        if not st.is_absolute() and not st.exists() and (work/st).exists():st=work/st
        cmd += ['--state',str(st.resolve())]
        if args.state_keep_cart:cmd += ['--state-keep-cart']
    if args.movie:cmd += ['--movie',str(Path(args.movie).resolve())]
    if args.checkpoint:cmd += ['--pre-event-state',str(work/'pre-event.ss'),'--checkpoint-interval',str(args.checkpoint_interval),'--event-state',str(work/'event.ss')]
    if args.final_state:cmd += ['--final-state',str(work/'final.ss')]
    env=environment_for_manifest(man,work,events)
    print(f"mGBA: {rom.name}\n  cart: {man['cart']['target_key']} ({man['cart']['profile']})\n  work: {work}")
    cp=subprocess.run(cmd,env=env)
    if report.is_file():
        r=json.loads(report.read_text()); e=r.get('events',{})
        print(f"RESULT {r.get('result')}: {r.get('reason')} | prog={e.get('program',0)} erase={e.get('erase',0)} fram={e.get('fram',0)} shadow={e.get('shadow_reads',0)}/{e.get('shadow_writes',0)} illegal={e.get('illegal_nor',0)} invalid={e.get('invalid_sequence',0)} native={e.get('native_save',0)}")
        print(f"Report: {report}")
    return cp.returncode
if __name__=='__main__':raise SystemExit(main())
