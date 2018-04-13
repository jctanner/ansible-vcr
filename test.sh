#!/bin/bash
export SSH_AUTH_SOCK=0
VERSION=$(ansible --version | head -n1 | awk '{print $2}')
PLAYBOOK=site.yml
#VMODE="-v"
VMODE="-vvvv"


export ANSIBLE_CALLBACK_WHITELIST=vcr


rm -rf ansible.log
rm -rf /tmp/fixtures

echo "#### STARTING RECORD MODE ..."
ANSIBLE_RECORDER_MODE="record" ansible-playbook $VMODE -i inventory $PLAYBOOK
RC=$?
if [[ $RC != 0 ]]; then
    exit $RC
fi

#exit 1

echo "#### WAITING 3 SECONDS ..."
#sleep 1
rm -rf ansible.log
mv -f /tmp/fixtures/callback.log /tmp/fixtures/callback.log.record
rm -rf /tmp/fixtures/fixture_read.log

echo "#### STARTING PLAY MODE ..."
ANSIBLE_RECORDER_MODE="play" ANSIBLE_KEEP_REMOTE_FILES=1 ansible-playbook $VMODE -i inventory $PLAYBOOK

RC=$?
exit $RC
