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


class FixtureLogger(object):

    '''CSV like file reader+writer for fixture logging'''

    def __init__(self, logdir='/tmp/fixtures'):
        self.logfile = os.path.join(logdir, 'fixture_read.log')

    def get_last_file(self, taskid, hostdir, function):
        '''What was the last fixture file used?'''
        if not os.path.isfile(self.logfile):
            return None

        lf = None
        with open(self.logfile, 'rb') as csvfile:
            data = csv.reader(csvfile, delimiter=';', quotechar='"')
            for row in data:
                #pprint(row)
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

    logfile = '/tmp/fixtures/callback.log'
    logdata = {}

    def _read_log(self):
        '''Consume the current log created by the callback'''
        with open(self.logfile, 'r') as f:
            self.logdata = json.loads(f.read())

    def get_current_task(self):
        '''Get the very last task from the list'''
        self._read_log()
        return self.logdata['tasks'][-1]


class AnsibleVCR(object):

    def __init__(self):
        self.fixture_dir = '/tmp/fixtures'
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

    def get_fixture_file(self, function, op, argvals=None, connection=None, cmd=None):
        '''Use the data to generate a fixture filename for the caller'''

        display.v('#=================> GET FIXTURE FILE')

        # read the current task info from the callback
        task_info = self.callback_reader.get_current_task()
        self.current_task_number = task_info['number']
        self.current_task_info = task_info.copy()

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
        hn = connection.get_option('_original_host')
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
                import epdb; epdb.st()
                filen = None

            self.fixture_logger.set_last_file(self.current_task_number, hostdir, function, filen)

        display.vvvv('RETURN FILE: ' + str(filen))
        return filen

    def record_exec_command(self, connection, command, returncode, stdout, stderr):

        fixture_file = self.get_fixture_file(
            'exec',
            'record',
            connection=connection
        )

        # build the datastructure with everything we know ...
        jdata = self._serialize_all_info(connection, returncode, stdout, stderr, command=command)

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
        candidates = glob.glob('%s/*%s' % (os.path.dirname(fixture_file), suffix))
        content_file = candidates[-1]

        if not os.path.isdir(content_file):
            shutil.copy(content_file, out_path)
        else:
            shutil.copytree(content_file, out_path)

        return (jdata['returncode'], jdata['stdout'], jdata['stderr'])
