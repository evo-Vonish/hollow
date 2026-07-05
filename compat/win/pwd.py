# Windows shim for the Unix-only stdlib `pwd` module.
# vendor/searxng/searx/valkeydb.py does `import pwd` at module level, but only
# calls pwd.getpwuid() in an error path we never reach (we don't configure
# valkey, so initialize() returns before touching it). This stub exists solely
# so the import succeeds on Windows. Prepend this directory to PYTHONPATH:
#   PYTHONPATH=compat\win;vendor\searxng
from collections import namedtuple

struct_passwd = namedtuple(
    "struct_passwd", "pw_name pw_passwd pw_uid pw_gid pw_gecos pw_dir pw_shell"
)


def getpwuid(uid):
    import getpass

    return struct_passwd(getpass.getuser(), "x", uid, uid, "", "", "")
