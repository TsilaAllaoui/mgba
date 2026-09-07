#!/usr/bin/env python3
from __future__ import annotations
import argparse,shutil
from pathlib import Path
from mgba_common import *

def main()->int:
    ap=argparse.ArgumentParser(description='Import existing .sav files into the shared test library for future automatic load/direct tests.')
    ap.add_argument('folder');ap.add_argument('--library');args=ap.parse_args()
    folder=Path(args.folder).resolve();root=testlib_root(args.library)
    if not folder.is_dir() or not (root/'library.json').is_file():ap.error('save folder or test library missing')
    games=read_json(root/'library.json').get('games',[]); imported=0;unmatched=[]
    for sav in sorted(folder.rglob('*.sav')):
        stem=sav.stem.lower();scores=[]
        for g in games:
            names=[Path(g.get('filename','')).stem.lower(),g.get('title','').lower(),g.get('game_code','').lower()]
            score=max([100 if stem==n and n else 0 for n in names]+[50 if n and (stem in n or n in stem) else 0 for n in names])
            if score:scores.append((score,g))
        scores.sort(key=lambda x:x[0],reverse=True)
        if not scores or (len(scores)>1 and scores[0][0]==scores[1][0]):unmatched.append(sav.name);continue
        g=scores[0][1];fd=game_dir(root,g['sha256'])/'fixtures';fd.mkdir(parents=True,exist_ok=True);dst=fd/'imported.sav';shutil.copy2(sav,dst)
        write_json(fd/'imported-save.json',{"schema":1,"source":str(sav),"sha256":sha256_file(dst),"bytes":dst.stat().st_size})
        imported+=1;print(f'{sav.name} -> {g.get("game_code")} {g.get("title")}')
    print(f'Imported {imported}; unmatched {len(unmatched)}')
    if unmatched:print('Unmatched: '+', '.join(unmatched[:20]))
    return 0
if __name__=='__main__':raise SystemExit(main())
