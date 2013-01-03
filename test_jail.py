# Strategy of test suite is to, for each test, compile a small C program
# from a string, run it, and inspect the results. stdout is captured
# from the programs and returned.

import os
import sys
from os.path import join as pjoin
import subprocess
import tempfile
import contextlib
import shutil
from textwrap import dedent
import errno
import functools
import nose
from nose.tools import ok_, eq_

JAIL_SO = os.path.realpath(pjoin(os.path.dirname(__file__), 'build', 'hdistjail.so'))

#
# Fixture/utils
#

@contextlib.contextmanager
def temp_dir(keep=False, prefix='tmp'):
    tempdir = tempfile.mkdtemp(prefix=prefix)
    try:
        yield tempdir
    finally:
        if not keep:
            shutil.rmtree(tempdir)


def fixture():
    def decorator(func):
        @functools.wraps(func)
        def decorated():
            tempdir = tempfile.mkdtemp(prefix='jailtest')
            try:
                os.mkdir(pjoin(tempdir, 'work'))
                return func(tempdir)
            finally:
                shutil.rmtree(tempdir)
        return decorated
    return decorator

def mock_files(tempdir, filenames):
    for filename in filenames:
        p = pjoin(tempdir, 'work', filename)
        try:
            os.makedirs(os.path.dirname(p))
        except OSError, e:
            if e.errno == errno.EEXIST:
                pass
        with file(p, 'w') as f:
            f.write('contents\n')

def compile(path, main_func_code):
    with file(path + '.c', 'w') as f:
        f.write(dedent('''
        #include <stdio.h>
        #include <stdlib.h>
        #include <sys/stat.h>
        #include <fcntl.h>
        #include <errno.h>

        int main() {
        %s
        return 0;
        }
        ''') % main_func_code)
    subprocess.check_call(['gcc', '-O0', '-g', '-o', path, path + '.c'])

def run_in_jail(tempdir,
                main_func_code,
                jail_hide=False,
                whitelist=None):
    work_dir = pjoin(tempdir, 'work')
    executable = pjoin(tempdir, 'test')
    compile(executable, dedent(main_func_code))
    cmd = [executable]
    env = dict(LD_PRELOAD=JAIL_SO)
    if jail_hide:
        env['HDIST_JAIL_HIDE'] = '1'

    if whitelist is not None:
        # make whitelist contain absolute paths
        whitelist = [
            x if os.path.isabs(x) else pjoin(work_dir, x)
            for x in whitelist]
        whitelist_path = pjoin(tempdir, 'whitelist.txt')
        with file(whitelist_path, 'w') as f:
            f.write('\n'.join(whitelist) + '\n')
        env['HDIST_JAIL_WHITELIST'] = whitelist_path

    # make a bash script too in case manual debugging is needed
    run_sh = pjoin(tempdir, 'run.sh')
    with file(run_sh, 'w') as f:
        f.write('#!/bin/bash\n')
        f.write('set -e\n')
        for key, var in env.items():
            f.write('export %s="%s"\n' % (key, var))
        f.write('(cd work; ../test)\n')
        os.chmod(run_sh, 0777)

    # ...but run the test in a slightly more controlled environment
    out = subprocess.check_output(cmd, cwd=work_dir, env=env)
    lines = [x for x in out.splitlines() if x]
    return lines

def run_int_checks(tempdir, preamble, checks, **kw):
    """like run_in_jail, but takes a list of integer-producing statements
    and converts the output to ints"""
    code = preamble + '\n'
    code += '\n'.join(r'printf("%%d\n", (%s));' % check for check in checks)
    out = run_in_jail(tempdir, code, **kw)
    return [int(x) for x in out]

#
# Tests
#

@fixture()
def test_test_machinery(tempdir):
    out = run_in_jail(tempdir, r'''
       printf("Hello %s\n", getenv("LD_PRELOAD"));
       ''')
    eq_(['Hello %s' % JAIL_SO], out)

@fixture()
def test_whitelist_open(tempdir):
    mock_files(tempdir, ['okfile', 'hiddenfile'])
    out = run_int_checks(tempdir,
        '',
        ['open("okfile", O_RDONLY) != -1',
         'errno',
         'open("hiddenfile", O_RDONLY) != -1',
         'errno'
         ],
        jail_hide=True,
        whitelist=['okfile'])
    eq_([1, 0, 0, errno.ENOENT], out)

if __name__ == '__main__':
    nose.main(sys.argv)

