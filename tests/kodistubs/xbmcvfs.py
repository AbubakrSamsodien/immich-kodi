"""Stub of the Kodi 21 (Omega) `xbmcvfs` module."""

from __future__ import annotations

import os
import shutil

from kodi_state import need_str


def translatePath(path):
    need_str(path, "xbmcvfs.translatePath", "path")
    # Kodi expands special:// only; anything else comes back verbatim.
    if path.startswith("special://"):
        return path
    return path


def validatePath(path):
    need_str(path, "xbmcvfs.validatePath", "path")
    return path


def exists(path):
    need_str(path, "xbmcvfs.exists", "path")
    return os.path.exists(path)


def mkdir(path):
    need_str(path, "xbmcvfs.mkdir", "path")
    try:
        os.mkdir(path)
        return True
    except OSError:
        return False


def mkdirs(path):
    need_str(path, "xbmcvfs.mkdirs", "path")
    os.makedirs(path, exist_ok=True)
    return True


def rmdir(path, force=False):
    need_str(path, "xbmcvfs.rmdir", "path")
    if force:
        shutil.rmtree(path, ignore_errors=True)
        return True
    try:
        os.rmdir(path)
        return True
    except OSError:
        return False


def delete(file):  # noqa: A002
    need_str(file, "xbmcvfs.delete", "file")
    try:
        os.remove(file)
        return True
    except OSError:
        return False


def listdir(path):
    need_str(path, "xbmcvfs.listdir", "path")
    dirs, files = [], []
    if os.path.isdir(path):
        for name in os.listdir(path):
            (dirs if os.path.isdir(os.path.join(path, name)) else files).append(name)
    return dirs, files


class File:
    def __init__(self, filepath, mode="r"):
        need_str(filepath, "xbmcvfs.File", "filepath")
        self._handle = open(filepath, mode if "b" in mode else mode + "b")

    def read(self, numBytes=0):
        data = self._handle.read(numBytes or -1)
        return data.decode("utf-8", "replace")

    def readBytes(self, numbytes=0):
        return self._handle.read(numbytes or -1)

    def close(self):
        self._handle.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
