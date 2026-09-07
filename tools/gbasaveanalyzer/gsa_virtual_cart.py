#!/usr/bin/env python3
from __future__ import annotations
import argparse, os, subprocess, sys, tempfile
from pathlib import Path

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE))
from gsa_common import (default_interactive_mgba_config_home, default_virtual_cart_folder,
                        find_executable, gbavc_profile, gbavc_profiles, save_settings)

VISIBLE=32<<20


def _frontend(explicit:str|None, prefer_qt:bool=False)->Path:
    if explicit:
        p=Path(explicit).expanduser().resolve()
        if p.is_file():return p
        raise FileNotFoundError(p)
    # Interactive cart testing prefers SDL: it has direct keyboard/gamepad
    # handling and avoids Qt input-profile issues. Qt remains a fallback.
    names=(['mgba-Qt','mgba-qt','mgba-SDL','mgba-sdl'] if prefer_qt else
           ['mgba-SDL','mgba-sdl','mgba-Qt','mgba-qt'])
    p=find_executable(names,Path(__file__),['mgba_GUI','mgba_FRONTEND'])
    if not p:
        raise FileNotFoundError('No mgba SDL/Qt executable found. Rebuild with ./BUILD_QT_WSL.sh --clean or pass --gui /full/path/to/mgba-sdl')
    return Path(p)


def _profile_names(p:dict)->str:
    a=', '.join(str(x) for x in p.get('aliases',[])[:3])
    return f"{p['key']} ({a})" if a else str(p['key'])


def _paths(cart_dir:Path,p:dict)->tuple[Path,Path|None,Path]:
    nor=cart_dir/str(p['default_nor_filename'])
    fram_name=str(p.get('default_fram_filename') or '')
    fram=cart_dir/fram_name if fram_name else None
    evname=str(p.get('default_events_filename') or f"{p['key']}.events.jsonl")
    events=cart_dir/(Path(evname).stem+'.mgba.jsonl')
    return nor,fram,events


def _valid(cart_dir:Path,p:dict)->tuple[bool,str]:
    nor,fram,_=_paths(cart_dir,p)
    cap=int(p.get('capacity_bytes',0) or 0)
    if not nor.is_file():return False,'NOR missing'
    if nor.stat().st_size!=cap:return False,f'NOR size {nor.stat().st_size} != {cap}'
    fb=int(p.get('fram_bytes',0) or 0)
    if fb:
        if not fram or not fram.is_file():return False,'FRAM missing'
        if fram.stat().st_size!=fb:return False,f'FRAM size {fram.stat().st_size} != {fb}'
    return True,'ready'


def list_profiles(cart_dir:Path)->list[dict]:
    rows=[]
    for p in gbavc_profiles():
        ok,why=_valid(cart_dir,p)
        nor,fram,_=_paths(cart_dir,p)
        rows.append({'profile':p,'ok':ok,'why':why,'nor':nor,'fram':fram})
    return rows


def choose_profile(cart_dir:Path,requested:str|None,interactive:bool)->dict:
    if requested:
        p=gbavc_profile(requested);ok,why=_valid(cart_dir,p)
        if not ok:raise RuntimeError(f"{p['key']}: {why} in {cart_dir}")
        return p
    ready=[r for r in list_profiles(cart_dir) if r['ok'] and not ('read_only' in r['profile'].get('flags',[]))]
    if not ready:raise RuntimeError(f'No complete writable GBAVirtualCart backing found in {cart_dir}')
    if len(ready)==1 or not interactive:return ready[0]['profile']
    print('\nExisting virtual carts:')
    for i,r in enumerate(ready,1):
        p=r['profile'];print(f" {i:>2}. {p['key']:<22} {p.get('display_name','')}")
    while True:
        v=input('Select cart [0=cancel]: ').strip()
        if v=='0' or not v:raise KeyboardInterrupt
        if v.isdigit() and 1<=int(v)<=len(ready):return ready[int(v)-1]['profile']


def make_boot_image(nor:Path,p:dict,out:Path)->None:
    cap=int(p['capacity_bytes']);visible=int(p.get('visible_bytes',VISIBLE) or VISIBLE)
    want=min(VISIBLE,visible,cap)
    with nor.open('rb') as src,out.open('wb') as dst:
        left=want
        while left:
            b=src.read(min(1<<20,left))
            if not b:break
            dst.write(b);left-=len(b)
    if out.stat().st_size<0xC0:raise RuntimeError('virtual NOR is too small to contain a GBA boot header')
    b=out.read_bytes()[:0xC0]
    # A flashed cart should have a real GBA image in its cold-boot aperture.
    if b[:4]==b'\xff\xff\xff\xff':raise RuntimeError('cold-boot aperture is blank (FFFF); cart does not appear flashed/initialized')


def build_env(cart_dir:Path,p:dict,events:Path,config_home:Path|None=None,use_system_config:bool=False)->dict[str,str]:
    env=os.environ.copy();nor,fram,_=_paths(cart_dir,p)
    if not use_system_config:
        base=(config_home or default_interactive_mgba_config_home()).expanduser().resolve()
        # mGBA appends its binary name below XDG_CONFIG_HOME; use sibling data/cache
        # trees so this interactive profile is stable but does not pollute normal mGBA.
        cfg=base/'config';data=base/'data';cache=base/'cache'
        for d in (cfg,data,cache):d.mkdir(parents=True,exist_ok=True)
        env['XDG_CONFIG_HOME']=str(cfg)
        env['XDG_DATA_HOME']=str(data)
        env['XDG_CACHE_HOME']=str(cache)
        env['mgba_INTERACTIVE_MGBA_CONFIG']=str(base)
    vals={
      'PROFILE':str(p['key']),'NOR':str(nor),'EVENTS':str(events),'EXISTING':'1',
      'STRICT':'0','ROM_OFFSET':'0','NATIVE_SAVE_ALLOWED':'0',
    }
    if fram:vals['FRAM']=str(fram);vals['NATIVE_SAVE_ALLOWED']='1'
    if str(p['key'])=='m36':vals['SAVE_SCRATCH_BYTES']='32768'
    for k,v in vals.items():
        env['MGBA_GBAVC_'+k]=v
        # Legacy mirrors keep older helper/frontend code compatible.
        if k!='EXISTING':env['MGBA_GBABR_'+k]=v
    return env


def main()->int:
    ap=argparse.ArgumentParser(description='Boot an existing melonDS/GBAVirtualCart backing directly in mgba mGBA.')
    ap.add_argument('--cart-dir',default=str(default_virtual_cart_folder()))
    ap.add_argument('--profile',help='profile key/alias (s29, m36, m6, m28, ...); auto-pick if omitted')
    ap.add_argument('--gui',help='explicit mGBA frontend executable (mgba-sdl or mgba-qt)')
    ap.add_argument('--qt',action='store_true',help='prefer Qt frontend instead of the default SDL frontend')
    ap.add_argument('--list',action='store_true')
    ap.add_argument('--prepare-only',action='store_true')
    ap.add_argument('--no-save-folder',action='store_true',help='do not persist this cart folder in mgba settings')
    ap.add_argument('--mgba-config-home',help='persistent mgba interactive mGBA config root')
    ap.add_argument('--use-system-mgba-config',action='store_true',help='inherit the normal ambient mGBA/XDG config instead of the dedicated persistent mgba config')
    a=ap.parse_args()
    cart_dir=Path(a.cart_dir).expanduser().resolve()
    cart_dir.mkdir(parents=True,exist_ok=True)
    if a.list:
        print(f'Virtual cart folder: {cart_dir}')
        for r in list_profiles(cart_dir):
            p=r['profile'];mark='READY' if r['ok'] else '--'
            print(f" {mark:<5} {p['key']:<22} {r['why']:<28} {r['nor'].name}")
        return 0
    try:p=choose_profile(cart_dir,a.profile,sys.stdin.isatty())
    except KeyboardInterrupt:return 0
    nor,fram,events=_paths(cart_dir,p)
    gui=_frontend(a.gui,prefer_qt=a.qt)
    if not a.no_save_folder:save_settings(virtual_cart_folder=str(cart_dir))
    with tempfile.TemporaryDirectory(prefix='mgba-cartboot-') as td:
        boot=Path(td)/f"{p['key']}-coldboot.gba"
        make_boot_image(nor,p,boot)
        config_home=Path(a.mgba_config_home).expanduser().resolve() if a.mgba_config_home else default_interactive_mgba_config_home()
        env=build_env(cart_dir,p,events,config_home=config_home,use_system_config=a.use_system_mgba_config)
        cmd=[str(gui),'-C','useBios=0','-C','gba.forceGbp=0',str(boot)]
        print('\nGBAVirtualCart existing-cart boot')
        print(f" profile : {p['key']} — {p.get('display_name','')}")
        print(f' NOR     : {nor}')
        if fram:print(f' FRAM    : {fram}')
        print(f' events  : {events}')
        frontend='SDL' if 'sdl' in gui.name.lower() else ('Qt' if 'qt' in gui.name.lower() else 'custom')
        print(f' frontend: {frontend}')
        print(f' GUI     : {gui}')
        if a.use_system_mgba_config:
            print(' config  : SYSTEM/ambient mGBA config')
        else:
            print(f' config  : {config_home} (persistent interactive)')
        print(' mode    : EXISTING (no reset, no ROM overlay)')
        if a.prepare_only:
            print(' command :',' '.join(cmd))
            return 0
        return subprocess.call(cmd,env=env)

if __name__=='__main__':
    raise SystemExit(main())
