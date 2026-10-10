"""Read private inputs or fixed, read-only systemd per-service copies."""
from __future__ import annotations

import os
from pathlib import Path
import stat

from .manual_runtime_profile import ManualRuntimeError, relative_path, require


UNITS = {'native': 'argentum-play.service', 'recorder': 'argentum-recorder.service',
         'sidecar': 'argentum-luna-sidecar.service'}
NAMES = {'native': frozenset({'profile.json', 'commander-gym.sidecar.token'}),
         'recorder': frozenset({'profile.json'}),
         'sidecar': frozenset({'profile.json', 'commander-gym.sidecar.token', 'openai.env'})}


def _identity(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode,
            info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _directory_identity(info):
    return (info.st_dev, info.st_ino, info.st_uid, info.st_gid, info.st_mode)


def credential(directory: Path, name: str, *, uid: int, limit: int,
               role: str | None = None) -> str:
    relative_path(name)
    require('/' not in name, 'credential_name')
    require(type(limit) is int and limit > 0 and type(uid) is int and uid >= 0,
            'credential_bounds')
    # Never normalize away traversal or accept a caller's environment as authority.
    require(directory.is_absolute() and str(directory) == directory.as_posix()
            and all(p not in ('', '.', '..') for p in str(directory).split('/')[1:]),
            'credential_directory_path')
    if role is not None:
        require(role in UNITS and name in NAMES[role], 'credential_role')
        require(directory == Path('/run/credentials') / UNITS[role], 'credential_unit_path')
        require(uid == os.getuid() and uid > 0, 'credential_service_identity')
    fds = []
    links = []
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC
    directory_flags = getattr(os, 'O_PATH', os.O_RDONLY) | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_DIRECTORY
    try:
        fd = os.open('/', directory_flags)
        fds.append(fd)
        info = os.fstat(fd)
        require(info.st_uid == 0 and info.st_gid == 0 and info.st_mode & 0o022 == 0,
                'credential_ancestor_custody')
        for part in directory.parts[1:]:
            parent = fd
            fd = os.open(part, directory_flags, dir_fd=parent)
            fds.append(fd)
            info = os.fstat(fd)
            require(stat.S_ISDIR(info.st_mode) and info.st_uid in ((0,) if role else (0, uid))
                    and info.st_mode & 0o7022 == 0, 'credential_directory_custody')
            if role:
                require(info.st_gid == 0, 'credential_directory_custody')
            links.append((parent, part, fd, info))
        if role:
            require(stat.S_IMODE(info.st_mode) == 0o550, 'credential_directory_custody')
            # systemd delivers a private read-only credential mount. A root-owned
            # shared file at the same spelling on an ordinary filesystem fails.
            require(os.fstatvfs(fd).f_flag & os.ST_RDONLY, 'credential_readonly_mount')
        parent = fd
        fd = os.open(name, flags | os.O_NONBLOCK, dir_fd=parent)
        fds.append(fd)
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
                and 0 <= info.st_size <= limit, 'credential_file_custody')
        if role:
            require(info.st_uid == 0 and info.st_gid == 0 and stat.S_IMODE(info.st_mode) == 0o440,
                    'credential_file_custody')
            require(os.fstatvfs(fd).f_flag & os.ST_RDONLY, 'credential_readonly_mount')
        else:
            require(info.st_uid in (0, uid) and stat.S_IMODE(info.st_mode) in (0o400, 0o600)
                    and info.st_gid == (0 if info.st_uid == 0 else os.getgid()),
                    'credential_file_custody')
        raw = bytearray()
        while data := os.read(fd, min(65536, limit - len(raw) + 1)):
            raw.extend(data)
            require(len(raw) <= limit, 'credential_size')
        require(_identity(info) == _identity(os.fstat(fd))
                == _identity(os.stat(name, dir_fd=parent, follow_symlinks=False)),
                'credential_changed')
        # Detect ancestor renames/replacements as well as mutation of the file.
        for parent, part, opened, before in links:
            require(_directory_identity(before) == _directory_identity(os.fstat(opened))
                    == _directory_identity(os.stat(part, dir_fd=parent, follow_symlinks=False)),
                    'credential_directory_changed')
        try:
            return raw.decode('utf-8')
        except UnicodeError:
            raise ManualRuntimeError('credential_encoding') from None
    except OSError:
        raise ManualRuntimeError('credential_unavailable') from None
    finally:
        for fd in reversed(fds):
            os.close(fd)
