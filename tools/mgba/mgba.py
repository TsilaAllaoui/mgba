#!/usr/bin/env python3
from __future__ import annotations
import json, os, subprocess, sys
from pathlib import Path

HERE=Path(__file__).resolve().parent
ROOT=HERE.parent.parent
sys.path.insert(0,str(HERE))
from mgba_common import (default_library_folder, default_rom_folder, default_virtual_cart_folder,
                        find_executable, load_settings, read_json, save_settings, settings_path)


def q(s:str)->str:
    return f'"{s}"' if any(c.isspace() for c in s) else s


def run(script,*args):
    cmd=[sys.executable,str(HERE/script),*map(str,args)]
    print('\n>',' '.join(q(x) for x in cmd),'\n')
    return subprocess.call(cmd)


def ask(prompt,default=''):
    suffix=f' [{default}]' if default else ''
    v=input(f'{prompt}{suffix}: ').strip()
    return v or default


def detect_tools():
    patcher=find_executable(['GBASaveHandler-dev7','GBASaveHandler','GBASaveHandler-linux-x86_64-static'],Path(__file__),['GBASAVEHANDLER'])
    autoqa=find_executable(['mgba-autoqa','mgba-gbabr-autoqa'],Path(__file__),['MGBA_AUTOQA','MGBA_GBABR_AUTOQA'])
    selftest=find_executable(['mgba-gbavc-selftest','mgba-gbabr-selftest'],Path(__file__),['MGBA_SELFTEST','MGBA_GBABR_SELFTEST'])
    gui=find_executable(['mgba-qt'],Path(__file__),['MGBA_QT'])
    return patcher,autoqa,selftest,gui


def print_status(roms:str,lib:str,carts:str):
    patcher,autoqa,selftest,gui=detect_tools()
    print('\n'+'='*72)
    print('mGBA GBAVirtualCart fork v1.0 status')
    print('='*72)
    print(f'ROM folder    : {roms} ' + ('[OK]' if Path(roms).expanduser().is_dir() else '[NOT FOUND]'))
    print(f'Test library  : {lib} ' + ('[OK]' if Path(lib).expanduser().is_dir() else '[will be created]'))
    print(f'Virtual carts : {carts} ' + ('[OK]' if Path(carts).expanduser().is_dir() else '[will be created]'))
    print(f'Settings      : {settings_path()}')
    print(f'GBASaveHandler: {patcher or "NOT FOUND"}')
    print(f'AutoQA        : {autoqa or "NOT FOUND"}')
    print(f'Self-test     : {selftest or "NOT FOUND"}')
    print(f'Qt GUI        : {gui or "NOT BUILT YET"}')
    print(f'Qt display    : DISPLAY={os.environ.get("DISPLAY","<unset>")} WAYLAND_DISPLAY={os.environ.get("WAYLAND_DISPLAY","<unset>")}')
    print('='*72)


def latest_files(lib:Path):
    p=lib/'LATEST_RUN.txt'
    if not p.is_file():
        print('No completed scan found yet.')
        return
    run_dir=Path(p.read_text().strip())
    print(f'\nLatest run: {run_dir}')
    for name in ('NEEDS_RECORDING.md','OPTIONAL_RECORDING.md','FAILURES.md','PATCH_GAPS.md','INAPPLICABLE.md'):
        f=run_dir/name
        if f.is_file():
            print('\n'+'='*72);print(name);print('='*72);print(f.read_text(errors='replace').strip())


def library_games(lib:Path):
    p=lib/'library.json'
    if not p.is_file():return []
    try:d=read_json(p)
    except Exception:return []
    games=list(d.get('games',[]))
    games.sort(key=lambda g:((g.get('game_code') or 'ZZZZ').upper(),(g.get('title') or g.get('filename') or '').lower()))
    return games


def pick_game(lib:Path, prompt='Select game'):
    games=library_games(lib)
    if not games:
        return ask('Game code / title / SHA prefix')
    print('\nGames in shared test library:')
    for i,g in enumerate(games,1):
        code=g.get('game_code') or g.get('sha256','')[:8]
        title=g.get('title') or g.get('filename') or '(unknown)'
        lvl=g.get('best_level',0)
        print(f' {i:>2}. {code:<8} L{lvl}  {title}')
    while True:
        v=input(f'{prompt} [number, code/title, 0=cancel]: ').strip()
        if not v or v=='0':return ''
        if v.isdigit() and 1<=int(v)<=len(games):
            g=games[int(v)-1]
            return g.get('game_code') or g.get('sha256','')[:12]
        # Let mgba_record do its normal exact/fuzzy resolution for text queries.
        return v


def persist_paths(roms:str,lib:str,carts:str|None=None):
    p=save_settings(roms=roms,library=lib,virtual_cart_folder=carts)
    os.environ['MGBA_ROMS']=roms
    os.environ['MGBA_LIBRARY']=lib
    if carts: os.environ['MGBA_CARTS']=carts
    print(f'Saved permanently: {p}')


def main():
    default_roms=str(default_rom_folder())
    default_lib=str(default_library_folder())
    default_carts=str(default_virtual_cart_folder())
    # Persist auto-detected defaults on first use so every subcommand sees the same folders.
    if not settings_path().is_file():
        save_settings(roms=default_roms,library=default_lib,virtual_cart_folder=default_carts)
    while True:
        patcher,autoqa,selftest,gui=detect_tools()
        print(f'''\n============================================================
             mGBA GBAVirtualCart fork v1.0
============================================================
 ROMs   : {default_roms}
 Library: {default_lib}
 Carts  : {default_carts}
------------------------------------------------------------
  1. Automatic batch scan (all ROMs x all carts)
  2. Show latest results / recording queue / patch gaps
  3. Record one-time SAVE checkpoint + movie (Qt GUI)
  4. Record one-time LOAD checkpoint + movie (Qt GUI)
  5. Run regression on all recorded games
  6. Import a folder of .sav files
  7. Test ONE ROM on ONE cart
  8. Run virtual cartridge self-test
  9. Run GBASaveHandler database check
 10. Build / rebuild mGBA
 11. Show setup/status
 12. Change folders (saved permanently)
 13. Create small diagnostic bundle for sharing
 14. Boot existing melonDS virtual cart in mGBA (SDL preferred)
 15. List shared GBAVirtualCart profiles/backings
 16. Run mGBA qualification
  0. Exit
''')
        try: choice=input('Select: ').strip()
        except EOFError: return 0
        lib=Path(default_lib).expanduser()
        if choice=='1':
            roms=ask('ROM folder',default_roms)
            jobs=ask('Parallel jobs',str(min(8,os.cpu_count() or 4)))
            frames=ask('Smoke frames','3000')
            # Store immediately so a successful/failed scan never loses the path choice.
            default_roms=str(Path(roms).expanduser().resolve()); default_lib=str(Path(default_lib).expanduser().resolve())
            persist_paths(default_roms,default_lib,default_carts)
            extra=[]
            if patcher: extra += ['--patcher',str(patcher)]
            if autoqa: extra += ['--autoqa',str(autoqa)]
            run('mgba_scan.py',default_roms,'--jobs',jobs,'--frames',frames,'--library',default_lib,*extra)
        elif choice=='2':
            latest_files(lib)
        elif choice in ('3','4'):
            game=pick_game(lib,'Game to record')
            if not game: continue
            args=[game,'--library',default_lib,'--phase','save' if choice=='3' else 'load']
            if gui: args += ['--gui',str(gui)]
            run('mgba_record.py',*args)
        elif choice=='5':
            jobs=ask('Parallel jobs',str(min(8,os.cpu_count() or 4)))
            extra=[]
            if patcher: extra += ['--patcher',str(patcher)]
            if autoqa: extra += ['--autoqa',str(autoqa)]
            run('mgba_regress.py','--library',default_lib,'--jobs',jobs,*extra)
        elif choice=='6':
            saves=ask('Folder containing .sav files')
            if saves: run('mgba_import_saves.py',saves,'--library',default_lib)
        elif choice=='7':
            rom=ask('ROM file')
            target=ask('Cart target (m6m/m36/m6mgd137/m28w640fs-t70za6/mx26l6420mc-90)','m6m')
            frames=ask('Frames','3000')
            extra=[]
            if patcher:extra += ['--patcher',str(patcher)]
            if autoqa:extra += ['--autoqa',str(autoqa)]
            run('mgba_patch_test.py',rom,target,'--frames',frames,'--fresh',*extra)
        elif choice=='8':
            if not selftest: print('Self-test executable not found. Build the Qt/headless suite first.')
            else: subprocess.call([str(selftest)])
        elif choice=='9':
            if not patcher: print('GBASaveHandler executable not found.')
            else: subprocess.call([str(patcher),'check'])
        elif choice=='10':
            subprocess.call([str(ROOT/'build.sh')])
        elif choice=='11':
            print_status(default_roms,default_lib,default_carts)
        elif choice=='12':
            default_roms=str(Path(ask('ROM folder',default_roms)).expanduser().resolve())
            default_lib=str(Path(ask('Test library folder',default_lib)).expanduser().resolve())
            default_carts=str(Path(ask('Shared virtual-cart folder',default_carts)).expanduser().resolve())
            persist_paths(default_roms,default_lib,default_carts)
        elif choice=='13':
            run('mgba_bundle.py','--library',default_lib)
        elif choice=='14':
            run('mgba_virtual_cart.py','--cart-dir',default_carts)
        elif choice=='15':
            run('mgba_virtual_cart.py','--cart-dir',default_carts,'--list')
        elif choice=='16':
            subprocess.call([str(HERE/'test.sh'),str(ROOT/'build')])
        elif choice in ('0','q','quit','exit'):
            return 0
        else:
            print('Invalid choice.')

if __name__=='__main__':
    raise SystemExit(main())
