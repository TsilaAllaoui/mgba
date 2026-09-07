#!/usr/bin/env python3
from __future__ import annotations
import argparse,os,shutil,subprocess,sys
from pathlib import Path
from gsa_common import *

TARGET_PREFERENCE=['m6m','m36','m6mgd137','mx26l6420mc-90','m28w640fs-t70za6']

def resolve_game(root:Path,q:str)->dict:
    idx=read_json(root/'library.json')
    ql=q.lower()
    exact=[]; fuzzy=[]
    for g in idx.get('games',[]):
        vals=(g.get('sha256','').lower(),g.get('game_code','').lower(),g.get('filename','').lower(),g.get('title','').lower())
        if ql in vals or any(v.startswith(ql) for v in vals if v):exact.append(g)
        elif any(ql in v for v in vals if v):fuzzy.append(g)
    c=exact or fuzzy
    if len(c)!=1:raise ValueError(f"game query matched {len(c)} entries; use an exact game code, menu number, or SHA prefix")
    return c[0]

def previous_levels(root:Path,sha:str)->dict[str,int]:
    for p in (root/'latest-summary.json',):
        if p.is_file():
            try:
                d=read_json(p); out={}
                for r in d.get('results',[]):
                    if r.get('source_sha256')==sha and r.get('smoke')=='PASS':
                        out[r.get('target','')]=max(out.get(r.get('target',''),0),int(r.get('level',0) or 0))
                if out:return out
            except Exception:pass
    return {}

def choose_target(root:Path,g:dict,explicit:str|None)->tuple[str,Path,Path,int]:
    if explicit:
        candidates=[explicit]
    else:
        levels=previous_levels(root,g['sha256'])
        available=list(dict.fromkeys(g.get('targets',[])))
        pref={t:i for i,t in enumerate(TARGET_PREFERENCE)}
        candidates=sorted(available,key=lambda t:(-levels.get(t,0),pref.get(t,999),t))
    levels=previous_levels(root,g['sha256'])
    for t in candidates:
        td=target_dir(root,g['sha256'],t);patched=td/'patched.gba';man=td/'manifest.gsa.json'
        if patched.is_file() and man.is_file():return t,patched,man,levels.get(t,0)
    raise ValueError('no passing scanned target is available; run scan first')

def main()->int:
    ap=argparse.ArgumentParser(description='Create/register the one-time save checkpoint + movie for a game.')
    ap.add_argument('game',help='game code, title fragment, filename or SHA prefix');ap.add_argument('--library');ap.add_argument('--target')
    ap.add_argument('--phase',choices=['save','load'],default='save');ap.add_argument('--gui',help='mGBA Qt/SDL executable built from this fork')
    ap.add_argument('--import-state');ap.add_argument('--import-movie')
    ap.add_argument('--fresh',action='store_true',help='force fresh virtual cart backing')
    ap.add_argument('--keep-cart',action='store_true',help='reuse existing GUI cart backing (SAVE capture defaults to fresh)')
    ap.add_argument('--use-user-mgba-config',action='store_true',help='inherit normal user mGBA settings instead of isolated mgba XDG folders')
    ap.add_argument('--prepare-only',action='store_true',help='print selected target/environment/launch command without opening the GUI')
    args=ap.parse_args();root=testlib_root(args.library)
    if not (root/'library.json').is_file():ap.error(f'test library not found: {root}; run mgba scan first or change the saved library folder')
    try:g=resolve_game(root,args.game);target,patched,manp,prev_level=choose_target(root,g,args.target)
    except Exception as e:ap.error(str(e))
    rd=recording_dir(root,g['sha256'],args.phase);rd.mkdir(parents=True,exist_ok=True)
    state=rd/('pre-save.ss' if args.phase=='save' else 'pre-load.ss');movie=rd/(f'{args.phase}.movie');recipe=rd/'recipe.json'
    if args.import_state:shutil.copy2(Path(args.import_state).resolve(),state)
    if args.import_movie:shutil.copy2(Path(args.import_movie).resolve(),movie)
    if args.import_state or args.import_movie:
        if not state.is_file() or not movie.is_file():ap.error('both state and movie are required to complete a recording')
        write_json(recipe,{"schema":1,"game_sha256":g['sha256'],"game_code":g.get('game_code'),"title":g.get('title'),"phase":args.phase,"reference_target":target,"state":state.name,"movie":movie.name})
        print(f'Recording registered: {rd}');return 0
    gui=Path(args.gui).resolve() if args.gui else find_executable(['mgba-Qt','mgba-qt','mgba-sdl'],Path(__file__),['mgba_GUI'])
    if not gui:
        print('No GUI build found. Build Qt from menu option 10, or register an existing checkpoint/movie:')
        print(f'  mgba record {g.get("game_code") or g["sha256"][:8]} --import-state PRE.ss --import-movie SAVE.movie --library "{root}"')
        return 2
    manifest=load_manifest(manp,patched);work=target_dir(root,g['sha256'],target)/'gui-record-work';work.mkdir(parents=True,exist_ok=True)
    fresh=args.fresh or (args.phase=='save' and not args.keep_cart)
    if fresh:
        for n in ('cart.bin','fram.bin','events-gui.jsonl'):
            p=work/n
            if p.exists():p.unlink()
    env=environment_for_manifest(manifest,work,work/'events-gui.jsonl')
    env['mgba_RECORD_DIR']=str(rd)
    env['mgba_RECORD_PHASE']=args.phase
    env['mgba_GAME_SHA']=g['sha256']
    env['mgba_LIBRARY']=str(root)
    if not args.use_user_mgba_config:
        iso=root/'settings'/'qt-isolated'
        cfg=iso/'config';data=iso/'data';cache=iso/'cache'
        for p in (cfg,data,cache):p.mkdir(parents=True,exist_ok=True)
        env['XDG_CONFIG_HOME']=str(cfg);env['XDG_DATA_HOME']=str(data);env['XDG_CACHE_HOME']=str(cache)
    safe_args=['-C','useBios=0','-C','gba.forceGbp=0']
    launch=[str(gui),*safe_args,str(patched)]
    print('\nONE-TIME GUI RECORDING — v1.0 SAFE MODE')
    print(f'Game: {g.get("title")} [{g.get("game_code")}]')
    print(f'Reference cart: {target} | previous confidence: L{prev_level}')
    print(f'Virtual cart start: {"FRESH" if fresh else "REUSE EXISTING"}')
    print('mGBA settings: '+('user profile + safety overrides' if args.use_user_mgba_config else 'isolated mgba profile (HLE BIOS, GBP forced off)'))
    print('1. Confirm the game boots normally. If not, close it; do not record.')
    print('2. Play/fast-forward until immediately BEFORE the in-game Save/Load action.')
    print('3. Choose: mgba -> Capture checkpoint + start recording...')
    print('4. Perform exactly ONE in-game operation and wait for it to finish.')
    print('5. Choose: mgba -> Finish recording, then close mGBA.')
    print(f'Recording folder: {rd}')
    print('Launching:',' '.join(launch))
    if args.prepare_only:return 0
    rc=subprocess.call(launch,env=env)
    if not state.is_file() or not movie.is_file() or movie.stat().st_size<20:
        print('Recording is incomplete. Expected both files:',file=sys.stderr)
        print(f'  {state}\n  {movie}',file=sys.stderr)
        print(f'GUI event log: {work/"events-gui.jsonl"}',file=sys.stderr)
        return rc or 2
    write_json(recipe,{"schema":3,"game_sha256":g['sha256'],"game_code":g.get('game_code'),"title":g.get('title'),"phase":args.phase,"reference_target":target,"reference_level":prev_level,"state":state.name,"movie":movie.name,"capture":"mgba Qt safe one-session","fresh_cart":fresh})
    print(f'\nDONE. One-time recording stored in {rd}')
    print('Future cart targets and libgbasave revisions can reuse this checkpoint/movie.')
    return rc
if __name__=='__main__':raise SystemExit(main())
