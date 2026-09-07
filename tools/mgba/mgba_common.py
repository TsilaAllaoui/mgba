#!/usr/bin/env python3
from __future__ import annotations
import csv, hashlib, json, os, re, shutil, subprocess, sys, time
from pathlib import Path
from typing import Any

PUBLIC_NAME = "mGBA"
SCHEMA = 2
DEFAULT_TARGETS = ["m6m", "m28w640fs-t70za6", "m36", "m6mgd137", "mx26l6420mc-90"]
# DEFAULT_TARGETS are patcher capabilities from the supplied libgbasave/GBASaveHandler
# release.  Virtual-cart identity/capacity/aliases come from GBAVirtualCart below.

SETTINGS_SCHEMA = 1

GBAVC_PROFILE_JSON = Path(__file__).resolve().parents[2]/"externals"/"GBAVirtualCart"/"profiles"/"builtin_profiles.json"

def gbavc_profiles() -> list[dict[str,Any]]:
    try:
        d=json.loads(GBAVC_PROFILE_JSON.read_text(encoding="utf-8"))
        return [x for x in d.get("profiles",[]) if isinstance(x,dict) and x.get("key")]
    except Exception:
        return []

def gbavc_profile(value:str) -> dict[str,Any]:
    q=value.strip().lower()
    for p in gbavc_profiles():
        names=[str(p.get("key","")).lower(),*[str(x).lower() for x in p.get("aliases",[])]]
        if q in names:return p
    raise ValueError(f"unsupported GBAVirtualCart profile: {value}")

def default_virtual_cart_folder() -> Path:
    if os.environ.get("MGBA_CARTS"):
        return Path(os.environ["MGBA_CARTS"]).expanduser().resolve()
    s=load_settings().get("virtual_cart_folder")
    if s:return Path(s).expanduser().resolve()
    # Reuse the persistent folder from the maintainable melonDS launcher when present.
    cfg=Path.home()/".config"/"gbabr-melonds"/"config"
    if cfg.is_file():
        try:
            import subprocess
            out=subprocess.check_output(["bash","--noprofile","--norc","-c",'source "$1"; printf "%s" "${CART_DIR:-}"',"_",str(cfg)],text=True,stderr=subprocess.DEVNULL).strip()
            if out:return Path(out).expanduser().resolve()
        except Exception:pass
    return (Path.home()/"mGBA_TEST"/"virtual-carts").resolve()


def default_interactive_mgba_config_home() -> Path:
    """Persistent XDG root for interactive mGBA virtual-cart sessions.

    Recording/AutoQA deliberately use library-local isolated XDG folders; interactive
    virtual-cart boot must instead have a stable writable configuration so keyboard
    and SDL controller mappings survive restarts.
    """
    if os.environ.get("MGBA_CONFIG_HOME"):
        return Path(os.environ["MGBA_CONFIG_HOME"]).expanduser().resolve()
    s=load_settings().get("interactive_mgba_config_home")
    if s:return Path(s).expanduser().resolve()
    return (Path.home()/".config"/"mgba-gbavc"/"user").resolve()

def settings_path() -> Path:
    override=os.environ.get("MGBA_SETTINGS")
    if override:
        return Path(override).expanduser().resolve()
    return Path.home()/".config"/"mgba-gbavc"/"settings.json"

def load_settings() -> dict[str,Any]:
    p=settings_path()
    if not p.is_file():
        return {}
    try:
        d=json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d,dict) else {}
    except Exception:
        return {}

def save_settings(*, roms:str|Path|None=None, library:str|Path|None=None, **extra:Any) -> Path:
    p=settings_path(); d=load_settings()
    d["schema"]=SETTINGS_SCHEMA
    if roms is not None:
        d["rom_folder"]=str(Path(roms).expanduser().resolve())
    if library is not None:
        d["library_folder"]=str(Path(library).expanduser().resolve())
    for k,v in extra.items():
        if v is not None:d[k]=v
    p.parent.mkdir(parents=True,exist_ok=True)
    p.write_text(json.dumps(d,indent=2,ensure_ascii=False)+"\n",encoding="utf-8")
    return p

def default_rom_folder() -> Path:
    if os.environ.get("MGBA_ROMS"):
        return Path(os.environ["MGBA_ROMS"]).expanduser().resolve()
    s=load_settings().get("rom_folder")
    if s:return Path(s).expanduser().resolve()
    for p in (Path("/mnt/h/GBA/US"), Path.home()/"GBA"/"ROMS"):
        if p.is_dir():return p.resolve()
    return (Path.home()/"mGBA_TEST"/"ROMS").resolve()

def default_library_folder() -> Path:
    if os.environ.get("MGBA_LIBRARY"):
        return Path(os.environ["MGBA_LIBRARY"]).expanduser().resolve()
    s=load_settings().get("library_folder")
    if s:return Path(s).expanduser().resolve()
    preferred=Path("/mnt/h/GBA/mGBA_Library")
    if preferred.is_dir():return preferred.resolve()
    return (Path.home()/"mGBA_TEST"/"mGBA_Library").resolve()

def sha256_file(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda:f.read(1<<20),b''): h.update(chunk)
    return h.hexdigest()

def rom_identity(path: Path) -> dict[str,Any]:
    b=path.read_bytes()[:0xC0]
    def dec(s:bytes): return ''.join(chr(c) if 32<=c<127 else ' ' for c in s).strip()
    return {
        "sha256":sha256_file(path), "bytes":path.stat().st_size,
        "title":dec(b[0xA0:0xAC]) if len(b)>=0xAC else path.stem,
        "game_code":dec(b[0xAC:0xB0]) if len(b)>=0xB0 else "",
        "maker_code":dec(b[0xB0:0xB2]) if len(b)>=0xB2 else "",
        "filename":path.name, "path":str(path.resolve()),
    }

def normalize_profile(v:str)->str:
    return str(gbavc_profile(v)["key"])

def parse_patch_report(text:str)->dict[str,Any]:
    def one(rx):
        m=re.search(rx,text,re.M); return m.group(1) if m else None
    target=one(r"^TARGET CART/NOR PROFILE: .*?\(([^()]+)\)\s*$")
    insha=one(r"^Input SHA256:\s*([0-9A-Fa-f]{64})\s*$")
    outsha=one(r"^Output SHA256:\s*([0-9A-Fa-f]{64})\s*$")
    save_lib=one(r"^Save library:\s*(.+?)\s*$")
    save_type=one(r"^Save type:\s*(.+?)\s*$")
    output_size=one(r"^\s*Output size:\s*(\d+)\s+bytes")
    ranges=[]
    rx=re.compile(r"^\s*(block\s+\d+|journal\s+span\s+\d+)\s*->\s*0x([0-9A-Fa-f]+)\.\.0x([0-9A-Fa-f]+)\b.*$",re.M)
    for m in rx.finditer(text):
        a,b=int(m.group(2),16),int(m.group(3),16)
        if b>=a:ranges.append({"offset":a,"bytes":b-a+1,"source":m.group(1)})
    # M36 direct-RWW currently uses the save aperture as volatile scratch/staging.
    # Current hardware/proven R3 model uses 32 KiB. Keep explicit in manifest so
    # it can later become report-derived without changing the test library schema.
    scratch=32768 if (target or '').lower()=="m36" else 0
    return {"target_key":(target or '').lower() or None,"input_sha256":(insha or '').lower() or None,
            "output_sha256":(outsha or '').lower() or None,"output_size":int(output_size) if output_size else None,
            "save_library":save_lib,"save_type":save_type,"ranges":ranges,"save_scratch_bytes":scratch}

def write_json(path:Path,obj:Any):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,indent=2,ensure_ascii=False)+"\n",encoding='utf-8')

def read_json(path:Path)->Any: return json.loads(path.read_text(encoding='utf-8'))

def find_executable(names, script_file:Path, env_names=()):
    """Find an mGBA companion executable without requiring shell setup.

    Search order is deliberately predictable: explicit environment overrides first,
    then common in-tree/release build locations, then PATH.  This lets the same
    scripts work from the full source tree, the lightweight release bundle, or a
    user's WSL Qt build without exporting BIN/TOOLS variables every terminal.
    """
    if isinstance(names,str): names=[names]
    if isinstance(env_names,str): env_names=[env_names]
    for e in env_names:
        if os.environ.get(e):
            p=Path(os.environ[e]).expanduser()
            if p.is_file(): return p.resolve()
    here=script_file.resolve().parent
    # tools/mgba -> repo root, plus a few package parents.
    roots=[]
    for root in (here, *here.parents[:6]):
        if root not in roots: roots.append(root)
    subs=(
        '', 'bin', 'binaries', 'binaries/linux',
        'build', 'build/qt', 'build/sdl',
        'build-headless', 'build-headless/qt', 'build-headless/sdl',
        'build-gbabr-static', 'build-gbabr',
    )
    for root in roots:
        for sub in subs:
            base=root/sub if sub else root
            for n in names:
                p=base/n
                if p.is_file() and os.access(p,os.X_OK): return p.resolve()
    # CMake's Qt target is normally emitted below a qt/ subdirectory.  Also
    # tolerate custom BUILD_DIR names so a successful user build is found
    # without requiring --gui or a root-level symlink.
    for root in roots:
        try:
            for build in root.glob('build*'):
                if not build.is_dir(): continue
                for n in names:
                    for p in (build/n, build/'sdl'/n, build/'qt'/n, build/'src'/'platform'/'sdl'/n, build/'src'/'platform'/'qt'/n):
                        if p.is_file() and os.access(p,os.X_OK): return p.resolve()
        except OSError:
            pass
    for n in names:
        f=shutil.which(n)
        if f:return Path(f).resolve()
    return None

def testlib_root(explicit:str|None=None, base:Path|None=None)->Path:
    if explicit:return Path(explicit).expanduser().resolve()
    if os.environ.get('MGBA_LIBRARY'):return Path(os.environ['MGBA_LIBRARY']).expanduser().resolve()
    s=load_settings().get('library_folder')
    if s:return Path(s).expanduser().resolve()
    preferred=Path('/mnt/h/GBA/mGBA_Library')
    if preferred.is_dir():return preferred.resolve()
    return (base or Path.cwd()).resolve()/"mGBA_Library"

def game_dir(root:Path, source_sha:str)->Path:return root/'games'/source_sha

def target_dir(root:Path, source_sha:str,target:str)->Path:return game_dir(root,source_sha)/'targets'/target

def recording_dir(root:Path,source_sha:str,phase='save')->Path:return game_dir(root,source_sha)/'recordings'/phase

def create_manifest(patched:Path, report:Path, output:Path)->dict[str,Any]:
    info=parse_patch_report(report.read_text(encoding='utf-8',errors='replace'))
    target=info['target_key']
    if not target: raise ValueError("patch report did not identify a NOR target")
    try:
        profile_info=gbavc_profile(target)
    except ValueError as e:
        raise ValueError(f"patch target {target!r} has no GBAVirtualCart profile") from e
    profile=str(profile_info["key"])
    capacity=int(profile_info.get("capacity_bytes",0) or 0)
    if not capacity: raise ValueError(f"GBAVirtualCart profile {profile!r} has no capacity")
    digest=sha256_file(patched)
    if info['output_sha256'] and info['output_sha256']!=digest: raise ValueError('patch report output SHA256 mismatch')
    ranges=[]
    for r in info['ranges']: ranges.append({"offset":int(r['offset']),"bytes":int(r['bytes']),"source":f"patch report {r['source']}"})
    data={
      "schema":SCHEMA,
      "rom":{"file":patched.name,"sha256":digest,"bytes":patched.stat().st_size,"source_sha256":info['input_sha256']},
      "cart":{"profile":profile,"target_key":target,"capacity":capacity,"rom_offset":0,"strict":True,
              "native_save_allowed":False,"save_scratch_bytes":int(info.get('save_scratch_bytes') or 0)},
      "save":{"library":info.get('save_library'),"type":info.get('save_type')},
      "mutable_ranges":ranges,
      "source":{"patch_report":str(report.resolve())},
    }
    write_json(output,data); return data

def environment_for_manifest(manifest:dict[str,Any], workdir:Path, events:Path|None=None)->dict[str,str]:
    env=os.environ.copy(); cart=manifest['cart']; profile=normalize_profile(cart['profile'])
    def both(name:str,value:str|None):
        for prefix in ('MGBA_GBAVC_','MGBA_GBABR_'):
            k=prefix+name
            if value is None:env.pop(k,None)
            else:env[k]=value
    both('PROFILE',profile)
    both('STRICT','1' if cart.get('strict',True) else '0')
    both('ALLOW',','.join(f"0x{int(r['offset']):X}:0x{int(r['bytes']):X}" for r in manifest.get('mutable_ranges',[])))
    both('ROM_OFFSET',hex(int(cart.get('rom_offset',0))))
    both('NOR',str(workdir/'cart.bin'))
    try: p=gbavc_profile(profile)
    except Exception:p={}
    if int(p.get('fram_bytes',0) or 0):both('FRAM',str(workdir/'fram.bin'))
    else:both('FRAM',None)
    if events:both('EVENTS',str(events))
    else:both('EVENTS',None)
    both('NATIVE_SAVE_ALLOWED','1' if cart.get('native_save_allowed',False) else '0')
    scratch=int(cart.get('save_scratch_bytes',0) or 0)
    both('SAVE_SCRATCH_BYTES',str(scratch) if scratch else None)
    return env

def load_manifest(path:Path, patched:Path|None=None)->dict[str,Any]:
    d=read_json(path)
    if int(d.get('schema',0)) not in (1,2):raise ValueError(f"unsupported manifest schema {d.get('schema')}")
    if patched:
        want=d.get('rom',{}).get('sha256'); got=sha256_file(patched)
        if want and want!=got:raise ValueError('patched ROM SHA256 does not match manifest')
    return d

def find_manifest(patched:Path, explicit:str|None=None)->Path:
    if explicit:
        p=Path(explicit).resolve();
        if not p.is_file():raise FileNotFoundError(p)
        return p
    for p in [Path(str(patched)+'.mgba.json'),patched.with_suffix('.mgba.json'),patched.with_suffix('.gbabr.json')]:
        if p.is_file():return p
    raise FileNotFoundError('manifest missing')

def summary_rows_to_csv(path:Path,rows:list[dict[str,Any]]):
    keys=['game_code','title','rom','target','patch','smoke','level','program','erase','fram','illegal','invalid','native','shadow_reads','shadow_writes','needs_recording','reason']
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=keys,extrasaction='ignore');w.writeheader();w.writerows(rows)

def safe_name(s:str)->str:
    return re.sub(r'[^A-Za-z0-9._-]+','_',s).strip('_') or 'game'
