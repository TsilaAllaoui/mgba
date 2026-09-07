#!/usr/bin/env python3
from __future__ import annotations
import argparse,concurrent.futures as cf,os,subprocess,threading
from datetime import datetime
from pathlib import Path
from mgba_common import *
_lock=threading.Lock()
def log(s):
    with _lock:print(s,flush=True)

def run_one(root:Path,g:dict,target:str,patcher:Path,autoqa:Path,frames:int)->dict:
    src=Path(g['path'])
    rd=recording_dir(root,g['sha256'],'save');state=rd/'pre-save.ss';movie=rd/'save.movie'
    td=target_dir(root,g['sha256'],target);rg=td/'regression-current';rg.mkdir(parents=True,exist_ok=True)
    patched=rg/'patched.gba';report=rg/'patch.txt';manp=rg/'manifest.mgba.json';work=rg/'work'
    row={"source_sha256":g['sha256'],"game_code":g.get('game_code',''),"title":g.get('title',''),"rom":g.get('filename',''),"target":target,
         "patch":"FAIL","smoke":"SKIP","level":0,"program":0,"erase":0,"fram":0,"illegal":0,"invalid":0,"native":0,"shadow_reads":0,"shadow_writes":0,"needs_recording":False,"reason":""}
    if not src.is_file():row['reason']='source ROM path missing';return row
    cp=subprocess.run([str(patcher),str(src),str(patched),target],stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True);report.write_text(cp.stdout,encoding='utf-8')
    if cp.returncode:row['reason']='repatch failed';return row
    row['patch']='PASS'
    try:man=create_manifest(patched,report,manp)
    except Exception as e:row['reason']=f'manifest: {e}';return row
    work.mkdir(parents=True,exist_ok=True)
    for n in ('cart.bin','fram.bin','events.jsonl','report.json'):
        p=work/n
        if p.exists():p.unlink()
    ev=work/'events.jsonl';rp=work/'report.json';env=environment_for_manifest(man,work,ev)
    cmd=[str(autoqa),str(patched),'--frames',str(frames),'--state',str(state),'--state-keep-cart','--movie',str(movie),
         '--require','save','--stop-on','save','--after-event','60','--cold-reset-on-event','--post-reset','300','--report',str(rp)]
    cp=subprocess.run(cmd,env=env,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True)
    if not rp.is_file():row['reason']='no regression report';return row
    r=read_json(rp);e=r.get('events',{})
    for k,sk in [('program','program'),('erase','erase'),('fram','fram'),('illegal','illegal_nor'),('invalid','invalid_sequence'),('native','native_save'),('shadow_reads','shadow_reads'),('shadow_writes','shadow_writes')]:row[k]=int(e.get(sk,0) or 0)
    if r.get('result')=='PASS' and r.get('cold_reset_performed'):
        row['smoke']='PASS';row['level']=4;row['reason']='recorded game save replay + settled cold persistence PASS'
    else:row['reason']=r.get('reason','regression fail')
    return row

def main()->int:
    ap=argparse.ArgumentParser(description='Repatch all recorded games with current libgbasave and replay their one-time save movies on all passing cart targets.')
    ap.add_argument('--library');ap.add_argument('--jobs',type=int,default=max(1,min(8,os.cpu_count() or 4)));ap.add_argument('--frames',type=int,default=18000);ap.add_argument('--patcher');ap.add_argument('--autoqa')
    args=ap.parse_args();root=testlib_root(args.library)
    if not (root/'library.json').is_file():ap.error('test library missing; run scan first')
    patcher=Path(args.patcher).resolve() if args.patcher else find_executable(['GBASaveHandler','GBASaveHandler-linux-x86_64-static'],Path(__file__),['GBASAVEHANDLER'])
    autoqa=Path(args.autoqa).resolve() if args.autoqa else find_executable(['mgba-autoqa','mgba-gbabr-autoqa'],Path(__file__),['MGBA_AUTOQA','MGBA_GBABR_AUTOQA'])
    if not patcher or not autoqa:ap.error('patcher or AutoQA missing')
    games=read_json(root/'library.json').get('games',[]);jobs=[];skipped=[]
    for g in games:
        rd=recording_dir(root,g['sha256'],'save')
        if not ((rd/'pre-save.ss').is_file() and (rd/'save.movie').is_file()):skipped.append(g);continue
        for t in g.get('targets',[]):jobs.append((g,t))
    if not jobs:print('No recorded game/target jobs. Use mGBA record first.');return 1
    runid=datetime.now().strftime('%Y%m%d-%H%M%S');rundir=root/'regressions'/runid;rundir.mkdir(parents=True,exist_ok=True)
    print(f'mGBA regression: {len(jobs)} jobs, workers={args.jobs}, skipped-unrecorded={len(skipped)}')
    rows=[]
    with cf.ThreadPoolExecutor(max_workers=max(1,args.jobs)) as ex:
        futs={ex.submit(run_one,root,g,t,patcher,autoqa,args.frames):(g,t) for g,t in jobs}
        n=0
        for f in cf.as_completed(futs):
            g,t=futs[f];n+=1
            try:r=f.result()
            except Exception as e:r={"source_sha256":g['sha256'],"game_code":g.get('game_code',''),"title":g.get('title',''),"rom":g.get('filename',''),"target":t,"patch":"ERROR","smoke":"ERROR","level":0,"program":0,"erase":0,"fram":0,"illegal":0,"invalid":0,"native":0,"shadow_reads":0,"shadow_writes":0,"needs_recording":False,"reason":repr(e)}
            rows.append(r);log(f'[{n}/{len(jobs)}] {r["game_code"]:4} {t:20} L{r["level"]} {r["reason"]}')
    rows.sort(key=lambda r:(r['game_code'],r['target']));summary={"schema":1,"tool":"mGBA","run":runid,"results":rows};write_json(rundir/'summary.json',summary);summary_rows_to_csv(rundir/'summary.csv',rows);write_json(root/'latest-regression.json',summary);summary_rows_to_csv(root/'latest-regression.csv',rows)
    fails=[r for r in rows if r['level']<4];(rundir/'FAILURES.md').write_text('# Regression failures\n\n'+('\n'.join(f"- `{r['game_code']}` {r['title']} / {r['target']}: {r['reason']}" for r in fails) if fails else 'None.')+'\n',encoding='utf-8')
    passed=len(rows)-len(fails);print(f'\nDONE: {passed}/{len(rows)} recorded save regressions PASS at L4. Results: {rundir}')
    return 0 if not fails else 1
if __name__=='__main__':raise SystemExit(main())
