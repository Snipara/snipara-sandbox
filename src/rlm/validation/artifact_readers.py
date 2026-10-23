"""Trusted descriptor-relative readers for Python and Node validation images.

Each directory is held open; O_NOFOLLOW applies to every component. A renamed
ancestor or a concurrent symlink substitution cannot redirect the open file.
"""

PYTHON_READER = r"""
import os, stat, sys
parts = sys.argv[2].split('/')
limit = int(sys.argv[3])
parent = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
try:
    for part in parts[:-1]:
        child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        os.close(parent)
        parent = child
    fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or (info.st_size > limit and sys.argv[4] != 'truncate'):
            raise ValueError('artifact must be a bounded regular unlinked file')
        remaining = limit + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            sys.stdout.buffer.write(chunk)
            remaining -= len(chunk)
    finally:
        os.close(fd)
finally:
    os.close(parent)
"""

NODE_READER = r"""
const fs = require('node:fs');
const parts = process.argv[2].split('/');
const limit = Number(process.argv[3]);
const C = fs.constants;
let parent = fs.openSync(process.argv[1], C.O_RDONLY | C.O_DIRECTORY | C.O_NOFOLLOW);
try {
  for (const part of parts.slice(0, -1)) {
    const child = fs.openSync('/proc/self/fd/' + parent + '/' + part, C.O_RDONLY | C.O_DIRECTORY | C.O_NOFOLLOW);
    fs.closeSync(parent); parent = child;
  }
  const fd = fs.openSync('/proc/self/fd/' + parent + '/' + parts.at(-1), C.O_RDONLY | C.O_NOFOLLOW | C.O_NONBLOCK);
  try {
    const info = fs.fstatSync(fd);
    if (!info.isFile() || info.nlink !== 1 || (info.size > limit && process.argv[4] !== 'truncate')) throw Error('artifact must be a bounded regular unlinked file');
    const buffer = Buffer.alloc(65536);
    let remaining = limit + 1;
    while (remaining > 0) {
      const count = fs.readSync(fd, buffer, 0, Math.min(buffer.length, remaining), null);
      if (!count) break;
      fs.writeSync(1, buffer, 0, count); remaining -= count;
    }
  } finally {fs.closeSync(fd);}
} finally {fs.closeSync(parent);}
"""
