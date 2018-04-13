# (c) 2017 Ansible Project
# GNU General Public License v3.0+ (see COPYING or https://www.gnu.org/licenses/gpl-3.0.txt)

# Make coding more python3-ish
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type

import json
import os
import sys
from ansible.plugins.callback import CallbackBase


PDATA = {
    'argv': [],
    'playbooks': [],
    'tasks': []
}


class CallbackModule(CallbackBase):

    CALLBACK_VERSION = 2.0
    CALLBACK_TYPE = 'notification'
    CALLBACK_NAME = 'vcr'

    def write_data(self):
        logdir = '/tmp/fixtures'
        logfile = os.path.join(logdir, 'callback.log')
        if not os.path.isdir(logdir):
            os.makedirs(logdir)
        with open(logfile, 'w') as f:
            f.write(json.dumps(PDATA))

    def get_index_for_task_uuid(self, uuid):
        if not PDATA['tasks']:
            return None
        ix = None
        for idx,x in enumerate(PDATA['tasks']):
            if x['uuid'] == uuid:
                ix = idx
                break
        return ix

    def v2_playbook_on_start(self, playbook):
        PDATA['argv'] = sys.argv[:]
        PDATA['playbooks'].append(playbook._file_name)
        self.write_data()

    def v2_playbook_on_task_start(self, task, is_conditional):
        tinfo = {
            'playbook': PDATA['playbooks'][-1],
            'path': task.get_path(),
            'name': task.name,
            'uuid': task._uuid,
            'number': len(PDATA['tasks']),
            'calls': 1
        }

        ix = self.get_index_for_task_uuid(tinfo['uuid'])
        if ix:
            tinfo['number'] = PDATA['tasks'][ix]['number']
            PDATA['tasks'][ix]['calls'] += 1

        PDATA['tasks'].append(tinfo)
        #import epdb; epdb.st()
        self.write_data()
