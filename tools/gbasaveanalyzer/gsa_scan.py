#!/usr/bin/env python3
from __future__ import annotations
import argparse, concurrent.futures as cf, json, os, subprocess, sys, threading, time
from datetime import datetime
from pathlib import Path
from gsa_common import *

_print_lock=threading.Lock()
def log(s):
    with _print_lock: print(s,flush=True)

def classify_patch_failure(text:str)->tuple[str,str,bool]:
    """Return (category, concise_reason, applicable_to_target)."""
    low=text.lower()
    rules=[
        ('capacity','source ROM exceeds target capacity',False,'source rom exceeds selected target/profile capacity'),
        ('runtime-space','no verified runtime space for injected save code',True,'no verified runtime cave and appended runtime exceeds'),
        ('analyzer-plan','exact analyzer plan is required for this ROM/backend',True,'requires an exact analyzer plan'),
        ('workspace','safe SRAM/EWRAM workspace was not proven',True,'workspace is not proven safe'),
        ('save-pattern','save marker found but expected save routines were not recognized',True,'standard nintendo read/write/verify routines were not all found'),
        ('unsupported-save','no supported save-library marker recognized',True,'no supported save-library marker found'),
        ('storage-space','save storage cannot be placed in verified ROM holes/address space',True,'save storage cannot be placed'),
        ('program-only-journal','program-only NOR journal layout does not fit this ROM',True,'no program-only sram journal fits'),
    ]
    for cat,reason,app,token in rules:
        if token in low:return cat,reason,app
    # Preserve the most useful final ERROR line when possible.
    errs=[line.strip() for line in text.splitlines() if 'error:' in line.lower()]
    return 'patch-error',(errs[-1] if errs else 'patch rejected/failed'),True

def patch_and_smoke(src:Path, ident:dict, target:str, root:Path, patcher:Path, autoqa:Path, frames:int, persistence:bool)->dict:
    td=target_dir(root,ident['sha256'],target);td.mkdir(parents=True,exist_ok=True)
    patched=td/'patched.gba'; patch_report=td/'patch.txt'; manifest_path=td/'manifest.gsa.json'; work=td/'work'
    row={"source_sha256":ident['sha256'],"game_code":ident['game_code'],"title":ident['title'],"rom":ident['filename'],"target":target,
         "patch":"FAIL","patch_category":"","applicable":True,"smoke":"SKIP","level":0,"program":0,"erase":0,"fram":0,"illegal":0,"invalid":0,"native":0,"shadow_reads":0,"shadow_writes":0,
         "needs_recording":False,"reason":""}
    cp=subprocess.run([str(patcher),str(src),str(patched),target],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
    patch_report.write_text(cp.stdout,encoding='utf-8')
    if cp.returncode:
        cat,reason,applicable=classify_patch_failure(cp.stdout)
        row['patch_category']=cat;row['applicable']=applicable;row['reason']=reason
        row['patch']='FAIL' if applicable else 'SKIP'
        return row
    row['patch']='PASS';row['patch_category']='ok';row['applicable']=True
    try: man=create_manifest(patched,patch_report,manifest_path)
    except Exception as e:row['reason']=f'manifest: {e}';return row
    work.mkdir(parents=True,exist_ok=True)
    for n in ('cart.bin','fram.bin','events.jsonl','report.json'):
        p=work/n
        if p.exists():p.unlink()
    events=work/'events.jsonl'; report=work/'report.json'; env=environment_for_manifest(man,work,events)
    cmd=[str(autoqa),str(patched),'--frames',str(frames),'--report',str(report)]
    cp=subprocess.run(cmd,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
    if not report.is_file():
        row['reason']='AutoQA produced no report'; return row
    try:r=read_json(report)
    except Exception as e:row['reason']=f'bad AutoQA report: {e}';return row
    e=r.get('events',{})
    for k,src_key in [('program','program'),('erase','erase'),('fram','fram'),('illegal','illegal_nor'),('invalid','invalid_sequence'),('native','native_save'),('shadow_reads','shadow_reads'),('shadow_writes','shadow_writes')]: row[k]=int(e.get(src_key,0) or 0)
    if r.get('result')!='PASS':row['reason']=r.get('reason','smoke fail');return row
    row['smoke']='PASS';row['level']=1;row['reason']='boot smoke passed'
    save_events=row['program']+row['erase']+row['fram']
    if save_events:
        row['level']=2;row['needs_recording']=False;row['reason']='automatic backend activity observed'
        if persistence:
            # Re-run fresh and wait for the complete event stream to settle before cold reset.
            for n in ('cart.bin','fram.bin','events-persist.jsonl','report-persist.json'):
                p=work/n
                if p.exists():p.unlink()
            pevents=work/'events-persist.jsonl'; preport=work/'report-persist.json'; penv=environment_for_manifest(man,work,pevents)
            pcmd=[str(autoqa),str(patched),'--frames',str(max(frames,6000)),'--require','save','--stop-on','save','--after-event','60','--cold-reset-on-event','--post-reset','300','--report',str(preport)]
            pcp=subprocess.run(pcmd,env=penv,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
            if preport.is_file():
                pr=read_json(preport)
                if pr.get('result')=='PASS' and pr.get('cold_reset_performed'):
                    row['level']=3;row['reason']='automatic backend activity + cold persistence passed'
                else: row['reason']+='; persistence test did not pass'
    return row

def main()->int:
    ap=argparse.ArgumentParser(description='Patch and headless-test every GBA ROM in a folder, in parallel, on every fixed NOR target.')
    ap.add_argument('folder');ap.add_argument('--library');ap.add_argument('--jobs',type=int,default=max(1,min(8,(os.cpu_count() or 4))))
    ap.add_argument('--frames',type=int,default=3000);ap.add_argument('--targets',nargs='*',default=DEFAULT_TARGETS);ap.add_argument('--patcher');ap.add_argument('--autoqa')
    ap.add_argument('--no-persistence',action='store_true');ap.add_argument('--non-recursive',action='store_true')
    args=ap.parse_args(); folder=Path(args.folder).expanduser().resolve()
    if not folder.is_dir():ap.error(f'folder not found: {folder}')
    root=testlib_root(args.library,folder.parent);root.mkdir(parents=True,exist_ok=True)
    # Remember successful user folder choices across terminals/menu sessions.
    try:save_settings(roms=folder,library=root)
    except Exception as e:log(f'WARNING: could not persist settings: {e}')
    patcher=Path(args.patcher).resolve() if args.patcher else find_executable(['GBASaveHandler','GBASaveHandler-linux-x86_64-static'],Path(__file__),['GBASAVEHANDLER'])
    autoqa=Path(args.autoqa).resolve() if args.autoqa else find_executable(['mgba-mgba-autoqa','mgba-gbabr-autoqa'],Path(__file__),['mgba_AUTOQA','MGBA_GBABR_AUTOQA'])
    if not patcher or not patcher.is_file():ap.error(f'GBASaveHandler executable not found: {patcher}')
    if not os.access(patcher,os.X_OK):ap.error(f'GBASaveHandler is not executable: {patcher} (run chmod +x)')
    if not autoqa or not autoqa.is_file():ap.error(f'mgba AutoQA executable not found: {autoqa}')
    if not os.access(autoqa,os.X_OK):ap.error(f'mgba AutoQA is not executable: {autoqa} (run chmod +x)')
    roms=sorted(folder.glob('*.gba') if args.non_recursive else folder.rglob('*.gba'))
    if not roms:print('No .gba files found.');return 1
    targets=[t.lower() for t in args.targets]
    runid=datetime.now().strftime('%Y%m%d-%H%M%S');rundir=root/'runs'/runid;rundir.mkdir(parents=True,exist_ok=True)
    games=[]
    for src in roms:
        ident=rom_identity(src);games.append((src,ident))
        gd=game_dir(root,ident['sha256']);gd.mkdir(parents=True,exist_ok=True)
        existing={}
        gp=gd/'game.json'
        if gp.exists():
            try:existing=read_json(gp)
            except:pass
        existing.update(ident);existing['last_seen']=datetime.now().isoformat();write_json(gp,existing)
    jobs=[(src,ident,t) for src,ident in games for t in targets]
    log(f'mgba scan: {len(roms)} ROMs x {len(targets)} targets = {len(jobs)} jobs | workers={args.jobs}')
    rows=[]
    with cf.ThreadPoolExecutor(max_workers=max(1,args.jobs)) as ex:
        futs={ex.submit(patch_and_smoke,src,ident,t,root,patcher,autoqa,args.frames,not args.no_persistence):(ident,t) for src,ident,t in jobs}
        done=0
        for f in cf.as_completed(futs):
            ident,t=futs[f]
            try:r=f.result()
            except Exception as e:r={"source_sha256":ident['sha256'],"game_code":ident['game_code'],"title":ident['title'],"rom":ident['filename'],"target":t,"patch":"ERROR","patch_category":"runner-error","applicable":True,"smoke":"ERROR","level":0,"program":0,"erase":0,"fram":0,"illegal":0,"invalid":0,"native":0,"shadow_reads":0,"shadow_writes":0,"needs_recording":False,"reason":repr(e)}
            rows.append(r);done+=1
            log(f'[{done:>3}/{len(jobs)}] {r["game_code"] or r["title"][:12]:12} {t:20} patch={r["patch"]:4} smoke={r["smoke"]:4} L{r["level"]} {r["reason"]}')
    rows.sort(key=lambda x:(x.get('game_code',''),x.get('rom',''),x.get('target','')))
    summary={"schema":1,"tool":"mgba","run":runid,"rom_folder":str(folder),"library":str(root),"patcher":str(patcher),"autoqa":str(autoqa),"frames":args.frames,"jobs":args.jobs,"targets":targets,"results":rows}
    write_json(rundir/'summary.json',summary);summary_rows_to_csv(rundir/'summary.csv',rows);write_json(root/'latest-summary.json',summary);summary_rows_to_csv(root/'latest-summary.csv',rows)
    # library index
    index={"schema":1,"tool":"mgba","updated":datetime.now().isoformat(),"games":[]}
    for src,ident in games:
        gr=[r for r in rows if r['source_sha256']==ident['sha256']]
        index['games'].append({**ident,"best_level":max([r['level'] for r in gr] or [0]),"targets":[r['target'] for r in gr if r['smoke']=='PASS']})
    write_json(root/'library.json',index)
    needs=[]; optional=[]; failures=[]; patch_gaps=[]; inapplicable=[]
    for src,ident in games:
        gr=[r for r in rows if r['source_sha256']==ident['sha256']]
        passing=[r for r in gr if r['smoke']=='PASS']
        automatic=[r for r in passing if r['level']>=2]
        rec=recording_dir(root,ident['sha256'])
        has_record=(rec/'pre-save.ss').is_file() and (rec/'save.movie').is_file()
        line=f"- `{ident['game_code'] or ident['sha256'][:8]}` **{ident['title'] or ident['filename']}** — {ident['filename']}"
        # Only games with an actually passing emulated cart path can benefit from
        # a gameplay recording. Complete patch failures belong in PATCH_GAPS.
        if passing and not has_record:
            (optional if automatic else needs).append(line + (f" — auto backend activity on {', '.join(r['target'] for r in automatic)}" if automatic else f" — passing targets: {', '.join(r['target'] for r in passing)}"))
        for r in gr:
            if r.get('patch')=='SKIP':
                inapplicable.append(line+f" — {r['target']}: {r['reason']}")
            elif r.get('patch') in ('FAIL','ERROR'):
                patch_gaps.append(line+f" — {r['target']} [{r.get('patch_category','patch-error')}]: {r['reason']}")
            elif r.get('patch')=='PASS' and r.get('smoke')!='PASS':
                failures.append(line+f" — {r['target']}: {r['reason']}")
        if not passing and not any(r.get('patch')=='PASS' for r in gr):
            # Useful one-line game summary; details are in PATCH_GAPS/INAPPLICABLE.
            failures.append(line+' — no patchable target reached boot smoke')
    (rundir/'NEEDS_RECORDING.md').write_text('# Manual save recordings needed\n\nThese games already PASS boot smoke on at least one virtual cart, but no automatic save/backend activity was observed. Record once per game; all passing cart targets can reuse that checkpoint/movie.\n\n'+('\n'.join(needs) if needs else 'None.')+'\n',encoding='utf-8')
    (rundir/'OPTIONAL_RECORDING.md').write_text('# Optional/recommended recordings\n\nThese games already exercised a save backend automatically, but a manual game Save/Load movie raises confidence to gameplay level.\n\n'+('\n'.join(optional) if optional else 'None.')+'\n',encoding='utf-8')
    (rundir/'FAILURES.md').write_text('# Runtime/overall failures needing review\n\n'+('\n'.join(failures) if failures else 'None.')+'\n',encoding='utf-8')
    (rundir/'PATCH_GAPS.md').write_text('# Patcher/analyzer gaps\n\nThese combinations were applicable to the selected cart capacity but GBASaveHandler/libgbasave could not currently produce a patch. These need patcher/analyzer work, not a gameplay recording.\n\n'+('\n'.join(patch_gaps) if patch_gaps else 'None.')+'\n',encoding='utf-8')
    (rundir/'INAPPLICABLE.md').write_text('# Inapplicable cart combinations\n\nThese are expected skips, most commonly because the source ROM is larger than the selected physical cart capacity.\n\n'+('\n'.join(inapplicable) if inapplicable else 'None.')+'\n',encoding='utf-8')
    # simple latest pointers as text files (portable; avoid symlink issues on Windows)
    (root/'LATEST_RUN.txt').write_text(str(rundir)+'\n',encoding='utf-8')
    passed=sum(r['smoke']=='PASS' for r in rows); auto=sum(r['level']>=2 for r in rows); persist=sum(r['level']>=3 for r in rows)
    print(f'\nDONE: {passed}/{len(rows)} boot smoke PASS, {auto} automatic backend activity, {persist} cold-persistence PASS')
    print(f'Manual recording queue: {len(needs)} games | optional: {len(optional)}')
    print(f'Results: {rundir}')
    return 0 if passed else 1
if __name__=='__main__':raise SystemExit(main())
