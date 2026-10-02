"""Resolve player files without changing the source checkout layout."""
from pathlib import Path

FILES = {
    'config.json': 'custom/game/config.json',
    'timer.json': 'custom/timer/timer.json',
    'audio.json': 'custom/audio/audio.json',
    'network.json': 'custom/network/network.json',
    'updates.json': 'custom/network/updates.json',
    'room-server.json': 'custom/network/room-server.json',
    'version.json': '_internal/version.json',
}

def player_layout(root):
    return (Path(root)/'custom').is_dir()

def config_path(root, name):
    root=Path(root)
    return root/FILES.get(name,name) if player_layout(root) else root/name

def presets_path(root):
    return Path(root)/('custom/presets' if player_layout(root) else 'presets')

def data_path(root, name):
    return Path(root)/('userdata' if player_layout(root) else '')/name

def sounds_path(root):
    return config_path(root,'audio.json').parent/'sounds'


def migrate_legacy(root):
    """Import files copied by old updaters. Verified originals remain in userdata."""
    import hashlib
    import shutil
    import uuid
    root=Path(root).resolve()
    if not player_layout(root): return
    sources=[root/name for name in FILES if (root/name).is_file()]
    for folder in ('sounds','presets'):
        source=root/folder
        if source.is_dir(): sources.extend(p for p in source.rglob('*') if p.is_file())
    if not sources:return
    backup=data_path(root,'migration-backups')/uuid.uuid4().hex
    def digest(p):return hashlib.sha256(p.read_bytes()).digest()
    for source in sources:
        if source.is_symlink() or not source.resolve().is_relative_to(root):
            raise ValueError('Unsafe legacy file path')
        relative=source.relative_to(root)
        saved=backup/'original'/relative
        saved.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(source,saved)
        if digest(saved)!=digest(source):raise OSError('Migration backup verification failed')
    # Every source is backed up before any source file is moved or target replaced.
    for source in sources:
        relative=source.relative_to(root)
        if len(relative.parts)==1: target=config_path(root,source.name)
        elif relative.parts[0]=='sounds': target=sounds_path(root)/Path(*relative.parts[1:])
        else:target=presets_path(root)/Path(*relative.parts[1:])
        if source.name!='version.json' or len(relative.parts)!=1:
            if target.exists():
                saved=backup/'defaults'/target.relative_to(root)
                saved.parent.mkdir(parents=True,exist_ok=True)
                shutil.copy2(target,saved)
                if digest(target)!=digest(saved):raise OSError('Default backup verification failed')
            target.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,target)
            if digest(source)!=digest(target):raise OSError('Migration verification failed')
        archived=backup/'retired'/relative
        archived.parent.mkdir(parents=True,exist_ok=True)
        source.rename(archived)
    # Only remove empty legacy directories, never recursively delete user data.
    for name in ('sounds','presets'):
        directory=root/name
        if directory.is_dir():
            for folder in sorted((p for p in directory.rglob('*') if p.is_dir()),key=lambda p:len(p.parts),reverse=True):
                if not any(folder.iterdir()):folder.rmdir()
            if not any(directory.iterdir()):directory.rmdir()
