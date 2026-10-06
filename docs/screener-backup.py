#!/usr/bin/python3
"""Archive a stable staging tree; SQLite is authoritative after restore."""
import datetime,pathlib,sqlite3,subprocess,tempfile,shutil,fnmatch
root=pathlib.Path('/var/backups/crypto-screener');root.mkdir(mode=0o700,parents=True,exist_ok=True)
name='screener-'+datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%d-%H%M%S')+'.tgz'
with tempfile.TemporaryDirectory(prefix='screener-backup-') as directory:
 stage=pathlib.Path(directory);files=stage/'files';dbs=stage/'sqlite'
 for app in ['crypto-screener','crypto-trade']:
  source=pathlib.Path('/opt')/app/'data'
  for p in source.rglob('*'):
   if not p.is_file() or p.name.endswith(('.tmp','.db-wal','.db-shm','.lock')) or fnmatch.fnmatch(p.name,'*.log*'):continue
   tree=dbs if p.name.endswith('.db') else files
   dest=tree/app/'data'/p.relative_to(source);dest.parent.mkdir(parents=True,exist_ok=True)
   if p.name.endswith('.db'):
    a=sqlite3.connect(p);b=sqlite3.connect(dest)
    try:a.backup(b)
    finally:b.close();a.close()
   else:
    try:shutil.copy2(p,dest)
    except FileNotFoundError:pass # a renamed queue file is represented by SQL consumed
  env=files/app/'.env';env.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(pathlib.Path('/opt')/app/'.env',env)
 for tree,suffix in [(files,''),(dbs,'.sqlite')]:
  archive=root/(name+suffix+'.part');final=root/(name if not suffix else name+'.sqlite.tgz')
  subprocess.run(['tar','-czf',str(archive),'-C',str(tree),'.'],check=True);archive.chmod(0o600);archive.replace(final)
 # Keep 28 complete generations, each with files and SQLite archives.
 generations={}
 for p in root.glob('screener-*.tgz'):
  key=p.name.removesuffix('.sqlite.tgz').removesuffix('.tgz');generations.setdefault(key,[]).append(p)
 for key in sorted(generations,reverse=True)[28:]:
  for p in generations[key]:
   if p.parent.resolve()==root.resolve():p.unlink()
print('Backup complete:',name)
