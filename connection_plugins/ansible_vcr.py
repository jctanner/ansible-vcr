# Copyright (c) 2012, Michael DeHaan <michael.dehaan@gmail.com>
# Copyright 2015 Abhijit Menon-Sen <ams@2ndQuadrant.com>
# Copyright 2017 Toshio Kuratomi <tkuratomi@ansible.com>
# Copyright (c) 2017 Ansible Project
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

from __future__ import (absolute_import, division, print_function)
__metaclass__ = type


import os
import datetime
import glob
import json
import re
import shutil
import csv

try:
    from __main__ import display
except ImportError:
    from ansible.utils.display import Display
    display = Display()


def clean_context(context):
    '''Remove sets in playcontext so it can be jsonified'''
    for k,v in context.items():
        if isinstance(v, set):
            context[k] = [x for x in v]
    return context

class StraceProcessor(object):
    def __init__(self, directory):
        self.directory = directory
        self.created = set()
        self.unlinked = set()
        self._process()

    def get_created(self):
        return list(self.created)

    def get_removed(self):
        return list(self.unlinked)

    def _process(self):

        syscalls = set()
        blacklist = [
            'execve',
            'stat',
            'access',
            'fchmodat',
            'readlink',
            'getcwd',
            'lstat',
            'unlink',
            'rmdir',
            'utimensat'
        ]

        dirfiles = glob.glob('%s/*' % self.directory)
        lines = []
        for dirfile in dirfiles:
            with open(dirfile, 'r') as f:
                lines += f.readlines()

        # open("/usr/lib/locale/locale-archive", O_RDONLY|O_CLOEXEC) = 5\n
        #cwd = None
        #current_file = None
        #current_fn = None
        for idx,x in enumerate(lines):
            x = x.strip()

            time_tuple = x.split(None, 1)
            if time_tuple[-1].startswith('+++ exited'):
                continue
            if ' ENOENT ' in time_tuple[-1]:
                continue
            if ' EEXIST ' in time_tuple[-1]:
                continue
            if ' SIGCHLD ' in time_tuple[-1]:
                continue

            syscall = time_tuple[-1].split('(', 1)[0]
            if syscall in blacklist:
                continue

            print(syscall)
            print(time_tuple[-1])
            syscalls.add(syscall)
            try:
                xpath = time_tuple[-1].split('"', 2)[1]
            except Exception as e:
                print(e)
                import epdb; epdb.st()

            '''
            if syscall == 'chdir':
                if xpath.startswith('/'):
                    cwd = xpath[:]
                elif cwd:
                    cwd = os.path.join(cwd, xpath)
                else:
                    cwd = xpath[:]
                continue

            current_file = xpath[:]

            try:
                current_fn = int(time_tuple[1].split()[-1])
            except Exception as e:
                print(e)
                continue
                #import epdb; epdb.st()

            if syscall == 'mkdir':
                dirs.add(os.path.join(cwd, xpath))
                continue

            if syscall == 'open':
                continue

            #if 'write' in syscall:
            #    import epdb; epdb.st()
            '''

            if syscall == 'creat':
                self.created.add(xpath)
            elif syscall == 'unlink':
                self.unlinked.add(xpath)

        #if len(list(syscalls)) > 1:
        #    import epdb; epdb.st()

class FixtureLogger(object):

    '''CSV like file reader+writer for fixture logging'''

    def __init__(self, logdir='/tmp/fixtures'):
        mode = os.environ.get('ANSIBLE_VCR_MODE', '')
        if not logdir:
            logdir = \
                os.environ.get('ANSIBLE_VCR_FIXTURE_DIR', '/tmp/fixtures')
        self.logfile = os.path.join(logdir, 'fixture_%s.log' % mode)

    def get_last_file(self, taskid, hostdir, function):
        '''What was the last fixture file used?'''
        if not os.path.isfile(self.logfile):
            return None

        lf = None
        with open(self.logfile, 'rb') as csvfile:
            data = csv.reader(csvfile, delimiter=';', quotechar='"')
            for row in data:
                #pprint(row)
                #if row[0] == taskid:
                if int(row[0]) == taskid:
                    if row[1] == hostdir:
                        if row[2] == function:
                            lf = row[3]
        return lf

    def set_last_file(self, taskid, hostdir, function, filen):
        '''Record that a fixture was read+written in the log'''
        with open(self.logfile, 'a') as csvfile:
            writer = csv.writer(csvfile, delimiter=';', quotechar='"')
            writer.writerow([taskid, hostdir, function, filen])

    def get_current_hostdir(self):
        '''What task+host fixture path should we be looking at?'''
        hd = None
        with open(self.logfile, 'rb') as csvfile:
            data = csv.reader(csvfile, delimiter=';', quotechar='"')
            for row in data:
                hd = row[1]
        return hd


class VCRCallbackReader(object):

    '''A callback client of sorts'''

    logdata = {}

    def get_logfile(self):
        fixturedir = os.environ.get('ANSIBLE_VCR_FIXTURE_DIR', '/tmp/fixtures')
        mode = os.environ.get('ANSIBLE_VCR_MODE', '')
        logfile = os.path.join(fixturedir, 'callback_%s.log' % mode)
        return logfile

    def _read_log(self):
        '''Consume the current log created by the callback'''
        logfile = self.get_logfile()
        with open(logfile, 'r') as f:
            self.logdata = json.loads(f.read())

    def get_current_task(self):
        '''Get the very last task from the list'''
        self._read_log()
        return self.logdata['tasks'][-1]


class AnsibleVCR(object):

    def __init__(self):
        self.mode = os.environ.get('ANSIBLE_VCR_MODE', None)
        self.fixture_dir = \
            os.environ.get('ANSIBLE_VCR_FIXTURE_DIR', '/tmp/fixtures')
        self.fixture_logger = FixtureLogger(self.fixture_dir)
        self.callback_reader = VCRCallbackReader()
        self.current_task_number = None
        self.current_task_info = None

        self.exec_index = 0
        self.put_index = 0
        self.fetch_index = 0

    def _serialize_all_info(self, connection, returncode, stdout, stderr, command=None, in_path=None, out_path=None):
        # build the datastructure with everything we know ...

        jdata = {
            'task_info': self.current_task_info.copy(),
            'context': clean_context(connection._play_context.serialize()),
            'transport': connection.transport,
            'command': command,
            'in_path': in_path,
            'out_path': out_path,
            'returncode': returncode,
            'stdout': stdout,
            'stderr': stderr
        }

        for attrib in ['host', 'user', 'port', 'control_path', 'socket_path']:
            if hasattr(connection, attrib):
                jdata[attrib] = getattr(connection, attrib)
            else:
                if attrib == 'host':
                    jdata[attrib] = 'localhost'
                else:
                    jdata[attrib] = None

        try:
            json.dumps(jdata)
        except Exception as e:
            print(e)
            import epdb; epdb.st()

        return jdata

    def get_strace_exec(self, connection, cmd):

        task_info = self.callback_reader.get_current_task()
        strace_dir = os.path.join(
            self.fixture_dir,
            str(task_info['number']),
            connection.get_option('_original_host'),
            'strace.out'
        )

        if not os.path.isdir(strace_dir):
            os.makedirs(strace_dir)

        einfo = {
            'dir': strace_dir,
            'cmd_orig': cmd[:],
            'cmd': 'strace -fftttv -e trace=creat,unlink -o ' + strace_dir + '/test ' + cmd
        }

        return (einfo['cmd'], einfo)

    def get_fixture_file(self, function, op, argvals=None, connection=None, cmd=None):
        '''Use the data to generate a fixture filename for the caller'''

        display.v('#=================> GET FIXTURE FILE')

        # read the current task info from the callback
        task_info = self.callback_reader.get_current_task()
        self.current_task_number = task_info['number']
        self.current_task_info = task_info.copy()

        '''
        if hasattr(connection, 'task_uuid'):
            self.current_task_number = connection.task_uuid.split('-')[-1]
        else:
            import epdb; epdb.st()
        '''

        # set the top level directory for the task fixtures
        taskdir = os.path.join(self.fixture_dir, str(self.current_task_number))
        try:
            if not os.path.isdir(taskdir):
                os.makedirs(taskdir)
        except OSError as e:
            # fork race conditions
            pass

        # https://github.com/ansible/ansible/blob/devel/lib/ansible/executor/task_executor.py#L797
        # connection = self._shared_loader_obj.connection_loader.get(conn_type, self._play_context, self._new_stdin, ansible_playbook_pid=to_text(os.getppid()))

        # use connection to determine the remote host
        '''
        if hasattr(connection, 'host'):
            hostdir = os.path.join(taskdir, connection.host)
        else:
            hn = connection.get_option('_original_host')
            hostdir = os.path.join(taskdir, 'localhost[%s]' % hn)
        '''
        # depends on https://github.com/ansible/ansible/pull/38818
        if not hasattr(connection, 'host'):
            hn = connection.get_option('_original_host')
        else:
            if connection.host == 'localhost':
                hn = connection.get_option('_original_host')
            else:
                hn = connection.host
        #import epdb; epdb.st()
        if not hn:
            hostdir = os.path.join(taskdir, connection.host)
        hostdir = os.path.join(taskdir, hn)

        # ensure we have a place to read and write the fixtures for the host
        if not os.path.isdir(hostdir):
            os.makedirs(hostdir)

        # fixtures are timestamped for easier visual sorting
        ts = datetime.datetime.strftime(
            datetime.datetime.now(),
            '%Y-%m-%d_%H-%M-%S-%f'
        )

        # this is what needs to be returned so the caller knows what to
        # read or write for this connection.
        filen = None

        if op == 'record':
            display.vvvv('WRITE TASKID: %s' % self.current_task_number)
            display.vvvv('WRITE FUNCTION: %s' % function)
            display.vvvv('WRITE OP: %s' % op)

            prefix = os.path.join(hostdir, ts + '_' + function + '_')
            existing = glob.glob('%s/*.json' % hostdir)
            existing = [x for x in existing if function in x]
            existing = [x for x in existing if x.endswith('.json')]
            existing = [x.replace('.json', '') for x in existing]
            existing = [x.split('_')[-1] for x in existing]
            existing = sorted([int(x) for x in existing])

            _prefix = os.path.join(hostdir, ts + '_' + function + '_')
            if not existing:
                filen = _prefix + '1.json'
            else:
                filen = _prefix + '%s.json' % (existing[-1] + 1)

        elif op == 'read':
            display.vvvv('READ TASKID: %s' % self.current_task_number)
            display.vvvv('READ FUNCTION: %s' % function)
            display.vvvv('READ OP: %s' % op)

            existing = glob.glob('%s/*.json' % hostdir)
            existing = [x for x in existing if function in x]
            existing = [x for x in existing if x.endswith('.json')]
            display.vvvv('1. possible choices: ' % existing)

            if cmd:
                existing = sorted(existing)
                candidates = []
                for ef in existing:
                    with open(ef, 'r') as f:
                        jdata = json.loads(f.read())
                    if 'command' not in jdata:
                        continue
                    if jdata['command'][-1] == cmd[-1]:
                        candidates.append(ef)
                        continue
                    if jdata['command'][-1][:30] == cmd[-1][:30]:
                        candidates.append(ef)
                        continue

                if candidates:
                    existing = candidates[:]
                display.vvvv('2. possible choices: ' % existing)

            existing = [x.replace('.json', '') for x in existing]
            existing = [x.split('_')[-1] for x in existing]
            existing = sorted([int(x) for x in existing], reverse=True)
            display.vvvv('3. possible choices: ' % existing)

            # use the last file to increment for this call
            lastf = self.fixture_logger.get_last_file(self.current_task_number, hostdir, function)
            display.v('READ LASTFILE: %s' % lastf)

            # increment the id of the file
            if lastf is None:
                fileid = 1
            else:
                fileid = lastf.split('_')[-1].replace('.json', '')
                fileid = int(fileid)
                fileid += 1
            display.vvvv('READ FID: ' + str(fileid))

            # try to find the file with the new id
            suffix = '_%s_%s.json' % (function, fileid)
            _existing = glob.glob('%s/*%s' % (hostdir, suffix))
            display.v('READ _EXISTING: %s' % _existing)

            if len(_existing) == 1:
                filen = _existing[-1]
            else:
                display.error('_existing: %s' % _existing)
                breakhost = os.environ.get('ANSIBLE_VCR_HOST_BREAK')
                if not breakhost or breakhost == hn:
                    import epdb; epdb.st()
                filen = None

            self.fixture_logger.set_last_file(self.current_task_number, hostdir, function, filen)

        display.vvvv('RETURN FILE: ' + str(filen))
        return filen

    def record_exec_command(self, connection, command, returncode, stdout, stderr, strace_info=None):

        fixture_file = self.get_fixture_file(
            'exec',
            'record',
            connection=connection
        )

        # build the datastructure with everything we know ...
        jdata = self._serialize_all_info(connection, returncode, stdout, stderr, command=command)
        if strace_info:
            jdata['command'] = strace_info['cmd']
            jdata['strace_info'] = strace_info.copy()
            if os.path.isdir(strace_info['dir']):
                sp = StraceProcessor(strace_info['dir'])
                created = sp.get_created()
                removed = sp.get_removed()

                fdir = fixture_file.replace('.json', '.strace')
                shutil.copytree(strace_info['dir'], fdir)
                shutil.rmtree(strace_info['dir'])

                if created or removed:
                    jdata['removed'] = removed[:]

                    artifacts = fixture_file.replace('.json', '.artifacts')
                    if not os.path.isdir(artifacts):
                        os.makedirs(artifacts)

                    jdata['created'] = {}
                    for create in created:
                        if not os.path.isfile(create):
                            continue
                        dest = os.path.join(artifacts, os.path.basename(create))
                        if os.path.isdir(create):
                            shutil.copytree(create, dest)
                        else:
                            shutil.copy(create, dest)
                        jdata['created'][create] = dest

                    #import epdb; epdb.st()

        with open(fixture_file, 'w') as f:
            f.write(json.dumps(jdata, indent=2))


    def read_exec_command(self, connection, cmd):
        display.v('FIXTURE_EXEC_INDEX: %s' % self.exec_index)
        fixture_file = self.get_fixture_file('exec', 'read', connection=connection, cmd=cmd)

        with open(fixture_file, 'r') as f:
            jdata = json.loads(f.read())

        display.v('IN CMD: %s' % cmd[-1])
        display.v('OUT CMD(1): %s' % jdata['command'][-1])

        if cmd[-1] != jdata['command'][-1] and 'ansible-tmp' in cmd[-1]:
            #  /home/vagrant/.ansible/tmp/ansible-tmp-1523577514.5-202990892955254
            orig = None
            curr = None
            try:
                curr = re.search('ansible-tmp-[0-9]+\.[0-9]+\-[0-9]+', cmd[-1]).group()
                orig = re.search('ansible-tmp-[0-9]+\.[0-9]+\-[0-9]+', jdata['command'][-1]).group()
            except Exception as e:
                display.vvv('ERROR: %s' % e)
                pass

            if orig and curr:
                jdata['stdout'] = jdata['stdout'].replace(orig, curr)
                jdata['stderr'] = jdata['stderr'].replace(orig, curr)

                fixed_cmd = jdata['command'][-1].replace(orig, curr)
                #if cmd[-1] != fixed_cmd:
                #    import epdb; epdb.st()
                jdata['command'][-1] = fixed_cmd[:]

        if cmd[-1] != jdata['command'][-1] and 'BECOME-SUCCESS' in cmd[-1]:
            # echo BECOME-SUCCESS-ocuebsgsnklcydfcjeakuxyvjdbuymhn;
            #import epdb; epdb.st()
            orig = None
            curr = None
            try:
                curr = re.search('BECOME-SUCCESS-[\w]+', cmd[-1]).group()
                orig = re.search('BECOME-SUCCESS-[\w]+', jdata['command'][-1]).group()
            except Exception as e:
                display.vvv('ERROR: %s' % e)
                pass

            if orig and curr:
                jdata['stdout'] = jdata['stdout'].replace(orig, curr)
                jdata['stderr'] = jdata['stderr'].replace(orig, curr)

                fixed_cmd = jdata['command'][-1].replace(orig, curr)
                #if cmd[-1] != fixed_cmd:
                #    import epdb; epdb.st()
                jdata['command'][-1] = fixed_cmd[:]

        if jdata.get('removed'):
            for fn in jdata['removed']:
                if os.path.exists(fn):
                    if os.path.isdir(fn):
                        shutil.rmtree(fn)
                    else:
                        os.remove(fn)

        if jdata.get('created'):
            for k,v in jdata['created'].items():
                if os.path.exists(k):
                    if os.path.isdir(k):
                        shutil.rmtree(k)
                    else:
                        os.remove(k)

                dirname = os.path.dirname(k)
                if not os.path.isdir(dirname):
                    os.makedirs(dirname)

                if os.path.isfile(v):
                    shutil.copy(v, k)
                else:
                    shutil.copytree(k, v)
            #import epdb; epdb.st()

        display.v('OUT CMD(2): %s' % jdata['command'][-1])

        return (jdata['returncode'], jdata['stdout'], jdata['stderr'])

    def record_put_file(self, connection, in_path, out_path, returncode, stdout, stderr):
        self.put_index += 1

        fixture_file = self.get_fixture_file('put', 'record', connection=connection)
        jdata = self._serialize_all_info(
            connection,
            returncode,
            stdout,
            stderr,
            in_path=in_path,
            out_path=out_path
        )

        with open(fixture_file, 'w') as f:
            f.write(json.dumps(jdata, indent=2))

        fixture_index = os.path.basename(fixture_file)
        fixture_index = fixture_index.replace('.json', '')
        fixture_index = fixture_index.split('_')[-1]
        fixture_date = '_'.join(fixture_file.split('_')[0:2])

        content_file = os.path.join(
            os.path.dirname(fixture_file),
            '%s_put_content_%s_%s' % (
                fixture_date, fixture_index, os.path.basename(out_path)
            )
        )

        if not os.path.isdir(in_path):
            shutil.copy(in_path, content_file)
        else:
            shutil.copytree(in_path, content_file)

    def read_put_file(self, connection, in_path, out_path):
        self.put_index += 1
        display.v('FIXTURE_PUT_INDEX: %s' % self.put_index)
        fixture_file = self.get_fixture_file('put', 'read', connection=connection)

        with open(fixture_file, 'r') as f:
            jdata = json.loads(f.read())

        return (jdata['returncode'], jdata['stdout'], jdata['stderr'])

    def record_fetch_file(self, connection, in_path, out_path, returncode, stdout, stderr):
        self.fetch_index += 1
        fixture_file = self.get_fixture_file('fetch', 'record', connection=connection)

        jdata = self._serialize_all_info(
            connection,
            returncode,
            stdout,
            stderr,
            in_path=in_path,
            out_path=out_path
        )

        with open(fixture_file, 'w') as f:
            f.write(json.dumps(jdata, indent=2))

        fixture_index = os.path.basename(fixture_file)
        fixture_index = fixture_index.replace('.json', '')
        fixture_index = fixture_index.split('_')[-1]
        fixture_date = '_'.join(fixture_file.split('_')[0:2])

        content_file = os.path.join(
            os.path.dirname(fixture_file),
            '%s_fetch_content_%s_%s' % (fixture_date, fixture_index, os.path.basename(out_path))
        )
        if not os.path.isdir(out_path):
            shutil.copy(out_path, content_file)
        else:
            shutil.copytree(out_path, content_file)

    def read_fetch_file(self, connection, in_path, out_path):
        self.fetch_index += 1
        fixture_file = self.get_fixture_file('fetch', 'read', connection=connection)

        with open(fixture_file, 'r') as f:
            jdata = json.loads(f.read())

        # /tmp/fixtures/4/el7host/1_fetch_content_foobar
        # 2018-04-13_08-33-17-377361_fetch_content_1_foobar

        suffix = 'fetch_content_%s_%s' % (self.fetch_index, os.path.basename(out_path))
        display.v('FETCH SUFFIX: %s' % suffix)
        display.v('FETCH GLOB PATTERN: %s/*%s' % (os.path.dirname(fixture_file), suffix))
        candidates = glob.glob('%s/*%s' % (os.path.dirname(fixture_file), suffix))
        display.v('FETCH CANDIDATES: ' % candidates)

        # openshift hackaround
        if not candidates:
            nsuffix = 'fetch_content_*_%s' % (os.path.basename(out_path))
            display.v('FETCH NSUFFIX: %s' % nsuffix)
            display.v('FETCH NGLOB PATTERN: %s/*%s' % (os.path.dirname(fixture_file), nsuffix))
            candidates = glob.glob('%s/*%s' % (os.path.dirname(fixture_file), nsuffix))
            display.v('FETCH NCANDIDATES: ' % candidates)

        content_file = candidates[-1]

        if not os.path.isdir(content_file):
            shutil.copy(content_file, out_path)
        else:
            shutil.copytree(content_file, out_path)

        return (jdata['returncode'], jdata['stdout'], jdata['stderr'])
